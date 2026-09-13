from fastapi import APIRouter, Depends, HTTPException

from insightflow_dashboard.dashboard.hydrated import HydratedDashboard

from insightflow_backend.auth.rate_limit import rate_limit_llm
from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.cache import ResultCache, build_cache_key
from insightflow_backend.dataset_deps import get_latest_dataset, get_pipeline_cache, get_result_cache
from insightflow_backend.db.models import Dataset, Project, Role
from insightflow_backend.pipeline_cache import PipelineCache

router = APIRouter()


@router.post(
    "/projects/{project_id}/dashboard/generate",
    response_model=HydratedDashboard,
    tags=["dashboard"],
    dependencies=[Depends(rate_limit_llm)],
)
def generate_dashboard(
    project: Project = Depends(require_project_role(Role.VIEWER)),
    dataset: Dataset = Depends(get_latest_dataset),
    cache: PipelineCache = Depends(get_pipeline_cache),
    result_cache: ResultCache = Depends(get_result_cache),
) -> HydratedDashboard:
    # No request body -- the dataset itself is the only thing this output varies on.
    cache_key = build_cache_key(dataset.id, "dashboard_generate", {})
    cached = result_cache.get(cache_key)
    if cached is not None:
        return HydratedDashboard.model_validate_json(cached)

    dashboard_pipeline = cache.get_or_build_dashboard(dataset)
    try:
        result = dashboard_pipeline.run()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result_cache.set(cache_key, result.model_dump_json())
    return result
