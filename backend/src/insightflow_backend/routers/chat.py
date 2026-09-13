from fastapi import APIRouter, Depends
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

    chat_pipeline = cache.get_or_build_chat(dataset)
    result = chat_pipeline.answer(body.question)  # never raises for refusals -- Answer.refused carries that
    result_cache.set(cache_key, result.model_dump_json())
    return result
