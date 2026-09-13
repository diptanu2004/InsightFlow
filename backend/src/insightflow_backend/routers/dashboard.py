from fastapi import APIRouter, HTTPException, Request

from insightflow_dashboard.dashboard.hydrated import HydratedDashboard

from insightflow_backend.session import get_session, require_schema

router = APIRouter()


@router.post("/generate", response_model=HydratedDashboard)
def generate_dashboard(request: Request) -> HydratedDashboard:
    session = get_session(request)
    require_schema(session)
    dashboard_pipeline = session.get_or_build_dashboard_pipeline()
    try:
        return dashboard_pipeline.run()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
