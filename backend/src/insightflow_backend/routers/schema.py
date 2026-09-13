import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from insightflow_core.models import SemanticModel

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

    return JobOut(id=job.id, project_id=job.project_id, status=job.status)


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


@router.get("/projects/{project_id}/schema/jobs/{job_id}", response_model=JobOut, tags=["schema"])
def get_discovery_job(
    job_id: uuid.UUID,
    project: Project = Depends(require_project_role(Role.VIEWER)),
    db: Session = Depends(get_db),
) -> JobOut:
    job = db.query(Job).filter(Job.id == job_id, Job.project_id == project.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    dataset = None
    if job.status == JobStatus.DONE:
        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()

    return JobOut(
        id=job.id,
        project_id=job.project_id,
        status=job.status,
        error=job.error,
        dataset=DatasetOut.model_validate(dataset) if dataset is not None else None,
    )
