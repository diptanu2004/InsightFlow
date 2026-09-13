import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from insightflow_backend import audit
from insightflow_backend.auth.dependencies import get_current_user
from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.dataset_deps import get_storage
from insightflow_backend.db.models import Dataset, Project, Role, User
from insightflow_backend.db.session import get_db
from insightflow_backend.storage import ObjectStorage
from insightflow_backend.wiring import run_schema_discovery

router = APIRouter()

_ALLOWED_SUFFIXES = {".csv"}


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    semantic_model: dict
    created_at: datetime


@router.post("/projects/{project_id}/schema/discover", response_model=DatasetOut, status_code=201, tags=["schema"])
async def discover_schema(
    files: list[UploadFile],
    name: str | None = Form(None),
    project: Project = Depends(require_project_role(Role.ANALYST)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
) -> DatasetOut:
    # insightflow_core's QueryExecutor.register_sources hardcodes a ".csv" lookup per entity
    # regardless of what POC 1's own FileParser would otherwise accept (.csv/.xlsx/.xls) -- so
    # accepting Excel here would let discovery succeed while every downstream query silently
    # fails to find its source file. Reject early instead.
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"unsupported file type '{suffix}' -- only .csv is accepted")

    contents = [(f.filename, await f.read()) for f in files]

    # Schema discovery reads local files, not S3 -- write to a throwaway temp dir just for the
    # duration of this request; the durable copy is what gets uploaded to S3 below.
    with tempfile.TemporaryDirectory() as tmp:
        local_paths = []
        for filename, data in contents:
            dest = Path(tmp) / filename
            dest.write_bytes(data)
            local_paths.append(str(dest))
        semantic_model = run_schema_discovery(local_paths)

    dataset_id = uuid.uuid4()
    storage_prefix = f"{project.org_id}/{project.id}/{dataset_id}"
    for filename, data in contents:
        storage.save(f"{storage_prefix}/{filename}", data)

    dataset_name = name or ", ".join(sorted({Path(fn).stem for fn, _ in contents}))
    dataset = Dataset(
        id=dataset_id,
        project_id=project.id,
        name=dataset_name,
        storage_prefix=storage_prefix,
        semantic_model=semantic_model.model_dump(mode="json"),
        created_by=user.id,
    )
    db.add(dataset)
    audit.record(
        db,
        org_id=project.org_id,
        user_id=user.id,
        action="dataset.created",
        resource_type="dataset",
        resource_id=dataset.id,
        detail=dataset_name,
    )
    db.commit()
    db.refresh(dataset)
    return DatasetOut.model_validate(dataset)
