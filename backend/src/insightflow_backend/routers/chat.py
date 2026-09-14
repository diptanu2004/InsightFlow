from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from insightflow_chatbot.models.result import Answer

from insightflow_backend.auth.rate_limit import rate_limit_llm
from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.cache import ResultCache, build_cache_key
from insightflow_backend.dataset_deps import get_latest_dataset, get_pipeline_cache, get_result_cache
from insightflow_backend.db.models import Dataset, Project, Role
from insightflow_backend.pipeline_cache import PipelineCache

router = APIRouter()


class AskRequest(BaseModel):
    question: str


@router.post(
    "/projects/{project_id}/chat/ask",
    response_model=Answer,
    tags=["chat"],
    dependencies=[Depends(rate_limit_llm)],
)
def ask(
    body: AskRequest,
    project: Project = Depends(require_project_role(Role.VIEWER)),
    dataset: Dataset = Depends(get_latest_dataset),
    cache: PipelineCache = Depends(get_pipeline_cache),
    result_cache: ResultCache = Depends(get_result_cache),
) -> Answer:
    cache_key = build_cache_key(dataset.id, "chat_ask", {"question": body.question})
    cached = result_cache.get(cache_key)
    if cached is not None:
        return Answer.model_validate_json(cached)

    try:
        # Building the pipeline can fail on the dataset itself, before any question is asked:
        # POC 4 infers its date bounds from a configured time entity, which a real upload whose
        # entities are named after its own files may not have. That's a property of the data,
        # not a server fault -- report it instead of letting it escape as a raw 500.
        chat_pipeline = cache.get_or_build_chat(dataset)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"this dataset can't be queried in chat: {e}")
    try:
        # Refusals come back as Answer.refused, not exceptions. A ValueError here is the engine itself
        # declining a query that passed validation (e.g. the SQL safety check) -- the same case the
        # analytics and dashboard routes already report as 422; it escaped this route as a raw 500.
        result = chat_pipeline.answer(body.question)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"couldn't compute an answer: {e}")
    result_cache.set(cache_key, result.model_dump_json())
    return result
