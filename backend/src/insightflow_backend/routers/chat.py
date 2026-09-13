from fastapi import APIRouter, Request
from pydantic import BaseModel

from insightflow_chatbot.models.result import Answer

from insightflow_backend.session import get_session, require_schema

router = APIRouter()


class AskRequest(BaseModel):
    question: str


@router.post("/ask", response_model=Answer)
def ask(body: AskRequest, request: Request) -> Answer:
    session = get_session(request)
    require_schema(session)
    chat_pipeline = session.get_or_build_chat_pipeline()
    return chat_pipeline.answer(body.question)  # never raises for refusals -- Answer.refused carries that
