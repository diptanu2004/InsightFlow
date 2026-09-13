"""Shared dependencies for the dataset-scoped routers (analytics/dashboard/chat): resolving
"the" dataset a project's queries run against, and reaching the process-wide PipelineCache.

A project can have multiple Dataset rows (each upload creates a new one -- see db/models.py), but
query/dashboard/chat routes take only `project_id`, not `dataset_id` (mirrors Phase 5's
one-active-dataset-at-a-time model, just scoped per project instead of globally): the most
recently created dataset for that project is "the" one in effect, same as a fresh
POST /schema/discover replacing Phase 5's single global session used to be.
"""
import uuid

from fastapi import Depends, HTTPException, Path, Request
from sqlalchemy.orm import Session

from insightflow_backend.cache import ResultCache
from insightflow_backend.db.models import Dataset
from insightflow_backend.db.session import get_db
from insightflow_backend.pipeline_cache import PipelineCache
from insightflow_backend.storage import ObjectStorage


def get_pipeline_cache(request: Request) -> PipelineCache:
    return request.app.state.pipeline_cache


def get_result_cache(request: Request) -> ResultCache:
    return request.app.state.result_cache


def get_storage(request: Request) -> ObjectStorage:
    return request.app.state.storage


def get_latest_dataset(project_id: uuid.UUID = Path(...), db: Session = Depends(get_db)) -> Dataset:
    dataset = (
        db.query(Dataset)
        .filter(Dataset.project_id == project_id)
        .order_by(Dataset.created_at.desc())
        .first()
    )
    if dataset is None:
        raise HTTPException(
            status_code=409,
            detail="no dataset uploaded yet for this project -- call "
            "POST /projects/{project_id}/schema/discover first",
        )
    return dataset
