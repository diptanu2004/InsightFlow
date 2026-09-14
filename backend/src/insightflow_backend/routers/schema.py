import copy
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from insightflow_core.models import SemanticModel

from insightflow_backend import audit
from insightflow_backend.auth.dependencies import get_current_user
from insightflow_backend.auth.rate_limit import rate_limit_llm
from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.dataset_deps import get_storage
from insightflow_backend.db.models import Dataset, Job, JobStatus, Project, Role, User
from insightflow_backend.db.session import get_db
from insightflow_backend.jobs import enqueue_discovery_job
from insightflow_backend.storage import ObjectStorage

router = APIRouter()

_ALLOWED_SUFFIXES = {".csv"}


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    # Typed rather than a bare `dict` (Phase 8 M3): the JSONB column is written from exactly this
    # model (`jobs.py` stores `SemanticModel.model_dump(mode="json")`), and declaring it here is
    # what puts Entity/SemanticField/Relationship into the OpenAPI document -- otherwise the
    # frontend's generated types bottom out at `unknown` for the one payload the semantic-model
    # viewer exists to render.
    semantic_model: SemanticModel
    created_at: datetime


class JobOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: JobStatus
    error: str | None = None
    dataset: DatasetOut | None = None
    created_at: datetime


@router.post(
    "/projects/{project_id}/schema/discover",
    response_model=JobOut,
    status_code=202,
    tags=["schema"],
    dependencies=[Depends(rate_limit_llm)],
)
async def discover_schema(
    files: list[UploadFile],
    name: str | None = Form(None),
    project: Project = Depends(require_project_role(Role.ANALYST)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
) -> JobOut:
    """Enqueues discovery instead of running it inline (Phase 7 M3) -- LLM-heavy relationship
    reasoning could take a while for a large/many-file upload, and doing that inside the HTTP
    request meant the client's connection had to stay open the whole time. Poll
    GET .../schema/jobs/{id} for the result; `run_schema_discovery()` itself, and every
    validation rule below, are unchanged from the synchronous version.
    """
    # insightflow_core's QueryExecutor.register_sources hardcodes a ".csv" lookup per entity
    # regardless of what POC 1's own FileParser would otherwise accept (.csv/.xlsx/.xls) -- so
    # accepting Excel here would let discovery succeed while every downstream query silently
    # fails to find its source file. Reject early instead.
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"unsupported file type '{suffix}' -- only .csv is accepted")

    contents = [(f.filename, await f.read()) for f in files]

    # The dataset id is generated now, before the Dataset row exists -- files are uploaded to
    # S3 under this id's prefix immediately, so the worker (which never sees the raw upload) can
    # download them straight back by key once it actually runs.
    dataset_id = uuid.uuid4()
    storage_prefix = f"{project.org_id}/{project.id}/{dataset_id}"
    filenames = []
    for filename, data in contents:
        storage.save(f"{storage_prefix}/{filename}", data)
        filenames.append(filename)

    dataset_name = name or ", ".join(sorted({Path(fn).stem for fn, _ in contents}))
    job = Job(
        project_id=project.id,
        status=JobStatus.PENDING,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        storage_prefix=storage_prefix,
        filenames=filenames,
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    enqueue_discovery_job(job.id)

    return _job_out(job, db)


def _job_out(job: Job, db: Session) -> JobOut:
    dataset = None
    if job.status == JobStatus.DONE:
        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        status=job.status,
        error=job.error,
        dataset=DatasetOut.model_validate(dataset) if dataset is not None else None,
        created_at=job.created_at,
    )


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetOut], tags=["schema"])
def list_datasets(
    project: Project = Depends(require_project_role(Role.VIEWER)),
    db: Session = Depends(get_db),
) -> list[DatasetOut]:
    """A project's datasets, newest first -- so `[0]` is the one query/dashboard/chat actually run
    against (`dataset_deps.get_latest_dataset` resolves the same row).

    Added in Phase 8 M3: without it the frontend could only ever see a semantic model in the
    response of the discovery job it just polled, so a page reload left the UI unable to tell
    whether the project had any data at all. Returns full semantic models rather than a summary
    plus a detail route -- one upload is one row and a model is a few KB, so the simpler shape
    covers both "does this project have data" and "show me the current model" in one request.
    """
    datasets = (
        db.query(Dataset)
        .filter(Dataset.project_id == project.id)
        .order_by(Dataset.created_at.desc())
        .all()
    )
    return [DatasetOut.model_validate(dataset) for dataset in datasets]


class MappingDecision(BaseModel):
    # (source_file, source_column) identifies a mapping: POC 1 emits exactly one per column, and an
    # entity is one source file.
    source_file: str
    source_column: str
    decision: Literal["confirm", "reject"]


class MappingDecisionsRequest(BaseModel):
    decisions: list[MappingDecision] = Field(min_length=1)


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/mapping-decisions",
    response_model=DatasetOut,
    status_code=201,
    tags=["schema"],
)
def review_mappings(
    dataset_id: uuid.UUID,
    body: MappingDecisionsRequest,
    project: Project = Depends(require_project_role(Role.ANALYST)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DatasetOut:
    """Confirm or reject column-to-semantic-type mappings. The engine computes only on "auto" or
    "confirmed" mappings (SemanticModel.trusted), so this is how a person promotes a correct
    low-confidence guess, or vetoes a wrong one POC 1 was sure about.

    Writes a NEW Dataset row -- same stored files, updated semantic model -- rather than editing this
    one. ResultCache and PipelineCache are only correct because a Dataset row never changes after it's
    created; a new version gets its own cache entries, becomes "the" dataset for the project
    immediately (newest wins), and keeps the previous version intact. One batch is one version.
    """
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.project_id == project.id).first()
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")

    latest = (
        db.query(Dataset).filter(Dataset.project_id == project.id).order_by(Dataset.created_at.desc()).first()
    )
    if latest.id != dataset.id:
        # Deciding against an older version would build the new one from stale state and silently
        # discard whatever was decided (or uploaded) since.
        raise HTTPException(
            status_code=409,
            detail=f"a newer version of this project's data exists ({latest.id}); review that one instead",
        )

    seen: set[tuple[str, str]] = set()
    for decision in body.decisions:
        key = (decision.source_file, decision.source_column)
        if key in seen:
            raise HTTPException(status_code=422, detail=f"more than one decision for {key[0]}.{key[1]}")
        seen.add(key)

    model = copy.deepcopy(dataset.semantic_model)
    fields = {(f["source_file"], f["source_column"]): f for e in model["entities"] for f in e["fields"]}
    unknown = [f"{d.source_file}.{d.source_column}" for d in body.decisions if (d.source_file, d.source_column) not in fields]
    if unknown:
        raise HTTPException(status_code=422, detail=f"no mapping for: {', '.join(unknown)}")

    changed = {"confirm": 0, "reject": 0}
    for decision in body.decisions:
        field = fields[(decision.source_file, decision.source_column)]
        status = "confirmed" if decision.decision == "confirm" else "rejected"
        if field.get("status") != status:
            field["status"] = status
            changed[decision.decision] += 1
    if not any(changed.values()):
        raise HTTPException(status_code=422, detail="these decisions don't change any mapping")

    new_version = Dataset(
        project_id=project.id,
        name=dataset.name,
        storage_prefix=dataset.storage_prefix,
        semantic_model=SemanticModel(**model).model_dump(mode="json"),
        created_by=user.id,
    )
    db.add(new_version)
    db.flush()
    audit.record(
        db,
        org_id=project.org_id,
        user_id=user.id,
        action="dataset.mappings_reviewed",
        resource_type="dataset",
        resource_id=new_version.id,
        detail=f"from {dataset.id}: {changed['confirm']} confirmed, {changed['reject']} rejected",
    )
    db.commit()
    db.refresh(new_version)
    return DatasetOut.model_validate(new_version)


@router.get("/projects/{project_id}/schema/jobs", response_model=list[JobOut], tags=["schema"])
def list_discovery_jobs(
    limit: int = Query(10, ge=1, le=100),
    project: Project = Depends(require_project_role(Role.VIEWER)),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    """A project's discovery jobs, newest first.

    Added after Phase 8 M6: the only way to follow a job was the id returned by the upload request,
    held in page state. A reload mid-discovery lost it, so a job that then failed (a real Groq quota
    429) was never reported -- the page just went back to "No data yet". The UI now resumes from here.
    """
    jobs = db.query(Job).filter(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(limit).all()
    return [_job_out(job, db) for job in jobs]


@router.get("/projects/{project_id}/schema/jobs/{job_id}", response_model=JobOut, tags=["schema"])
def get_discovery_job(
    job_id: uuid.UUID,
    project: Project = Depends(require_project_role(Role.VIEWER)),
    db: Session = Depends(get_db),
) -> JobOut:
    job = db.query(Job).filter(Job.id == job_id, Job.project_id == project.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job, db)
