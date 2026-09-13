from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile

from insightflow_core.models import SemanticModel

from insightflow_backend.config import settings
from insightflow_backend.session import get_session
from insightflow_backend.wiring import run_schema_discovery

router = APIRouter()

_ALLOWED_SUFFIXES = {".csv"}


@router.post("/discover", response_model=SemanticModel)
async def discover_schema(files: list[UploadFile], request: Request) -> SemanticModel:
    # insightflow_core's QueryExecutor.register_sources hardcodes a ".csv" lookup per entity
    # regardless of what POC 1's own FileParser would otherwise accept (.csv/.xlsx/.xls) -- so
    # accepting Excel here would let discovery succeed while every downstream query silently
    # fails to find its source file. Reject early instead.
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"unsupported file type '{suffix}' -- only .csv is accepted in Phase 5")

    session = get_session(request)
    session.reset()  # single global session: a new upload replaces whatever dataset came before

    upload_dir = session.new_upload_dir(settings.upload_root)
    saved_paths: list[str] = []
    for f in files:
        dest = upload_dir / f.filename
        dest.write_bytes(await f.read())
        saved_paths.append(str(dest))

    semantic_model = run_schema_discovery(saved_paths)
    session.semantic_model = semantic_model
    session.data_dir = str(upload_dir)
    return semantic_model
