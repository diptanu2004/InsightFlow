"""Async schema-discovery job (Phase 7 M3). `run_schema_discovery()` itself (POC 1's pipeline)
is completely unchanged -- this module only handles the async plumbing: loading/updating the
`Job` row, pulling the already-uploaded files back from S3, and creating the `Dataset` +
audit-log rows on success. See routers/schema.py for the HTTP side and worker.py for the process
that actually runs `run_discovery_job`.
"""
import tempfile
import uuid
from pathlib import Path

import redis
from rq import Queue
from sqlalchemy.orm import Session

from insightflow_backend import audit
from insightflow_backend.config import settings
from insightflow_backend.db.models import Dataset, Job, JobStatus, Project
from insightflow_backend.db.session import SessionLocal
from insightflow_backend.llm_errors import is_transient, provider_message
from insightflow_backend.storage import build_storage
from insightflow_core.execution.query_executor import check_source_readable

from insightflow_backend.wiring import run_schema_discovery


def build_rq_connection() -> redis.Redis:
    # RQ pickles job payloads onto this connection -- every other Redis client in this backend
    # (rate limiting, result cache) uses decode_responses=True, which would corrupt that binary
    # data. This connection is RQ's alone; never share it with cache.py/auth/rate_limit.py.
    return redis.Redis.from_url(settings.redis_url)


def get_queue() -> Queue:
    return Queue(settings.rq_queue_name, connection=build_rq_connection())


def enqueue_discovery_job(job_id: uuid.UUID) -> None:
    get_queue().enqueue(
        run_discovery_job, str(job_id), job_timeout=settings.discovery_job_timeout_seconds
    )


def _describe(exc: BaseException) -> str:
    """What to store on the Job row -- the one thing a person sees when discovery fails.

    A provider failure gets llm_errors' sentence rather than `str(exc)`: Groq's 429 body carries
    this deployment's organization id and a billing link, and it reached the upload panel verbatim
    (frontend/docs/hld.md finding 28). Everything else is still reported as-is, because the raw
    message is the useful part for a malformed CSV or a bug in discovery itself.
    """
    message = provider_message(exc)
    if message is None:
        return str(exc)
    return f"{message} Re-upload to try again." if is_transient(exc) else message


def _mark_failed(db: Session, job_id: uuid.UUID, error: str) -> None:
    db.rollback()
    job = db.get(Job, job_id)
    if job is not None:
        job.status = JobStatus.FAILED
        job.error = error[:2000]
        db.commit()


def run_discovery_job(job_id: str) -> None:
    """Runs in the worker process (worker.py), not the FastAPI request -- opens its own DB
    session and storage client rather than reusing request-scoped ones from dataset_deps.py.
    """
    job_uuid = uuid.UUID(job_id)
    db = SessionLocal()
    try:
        job = db.get(Job, job_uuid)
        if job is None:
            return  # nothing to update -- shouldn't happen outside a manual DB edit
        job.status = JobStatus.RUNNING
        db.commit()

        storage = build_storage()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                local_paths = []
                for filename in job.filenames:
                    local_path = Path(tmp) / filename
                    storage.download_to(f"{job.storage_prefix}/{filename}", local_path)
                    local_paths.append(str(local_path))
                # Before spending an LLM call: a file the engine can't read would pass discovery (pandas is
                # lenient) and then fail every query. Raises UnreadableSource, recorded as the job's error.
                for path in local_paths:
                    check_source_readable(path)
                semantic_model = run_schema_discovery(local_paths)
        except Exception as e:
            _mark_failed(db, job_uuid, _describe(e))
            raise

        try:
            dataset = Dataset(
                id=job.dataset_id,
                project_id=job.project_id,
                name=job.dataset_name,
                storage_prefix=job.storage_prefix,
                semantic_model=semantic_model.model_dump(mode="json"),
                created_by=job.created_by,
            )
            db.add(dataset)

            project = db.get(Project, job.project_id)
            audit.record(
                db,
                org_id=project.org_id,
                user_id=job.created_by,
                action="dataset.created",
                resource_type="dataset",
                resource_id=dataset.id,
                detail=dataset.name,
            )

            job.status = JobStatus.DONE
            db.commit()
        except Exception as e:
            _mark_failed(db, job_uuid, _describe(e))
            raise
    finally:
        db.close()
