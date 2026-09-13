from fastapi import APIRouter, Depends
from pydantic import BaseModel

from insightflow_chatbot.models.result import Answer

from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.dataset_deps import get_latest_dataset, get_pipeline_cache
from insightflow_backend.db.models import Dataset, Project, Role
from insightflow_backend.pipeline_cache import PipelineCache

router = APIRouter()


class AskRequest(BaseModel):
    question: str


@router.post("/projects/{project_id}/chat/ask", response_model=Answer, tags=["chat"])
def ask(
    body: AskRequest,
    project: Project = Depends(require_project_role(Role.VIEWER)),
    dataset: Dataset = Depends(get_latest_dataset),
    cache: PipelineCache = Depends(get_pipeline_cache),
) -> Answer:
    chat_pipeline = cache.get_or_build_chat(dataset)
    return chat_pipeline.answer(body.question)  # never raises for refusals -- Answer.refused carries that
