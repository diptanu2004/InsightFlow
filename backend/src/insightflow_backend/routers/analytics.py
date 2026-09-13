from fastapi import APIRouter, HTTPException, Request

from insightflow_core.models import AnalyticalQuery, MetricResult

from insightflow_backend.session import get_session, require_schema

router = APIRouter()


@router.post("/query", response_model=MetricResult)
def run_query(query: AnalyticalQuery, request: Request) -> MetricResult:
    session = get_session(request)
    require_schema(session)
    engine = session.get_or_build_engine()
    try:
        return engine.run(query)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
