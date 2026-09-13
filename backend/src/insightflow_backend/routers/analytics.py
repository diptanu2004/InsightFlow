from fastapi import APIRouter, Depends, HTTPException

from insightflow_core.models import AnalyticalQuery, MetricResult

from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.cache import ResultCache, build_cache_key
from insightflow_backend.dataset_deps import get_latest_dataset, get_pipeline_cache, get_result_cache
from insightflow_backend.db.models import Dataset, Project, Role
from insightflow_backend.pipeline_cache import PipelineCache

router = APIRouter()


@router.post("/projects/{project_id}/analytics/query", response_model=MetricResult, tags=["analytics"])
def run_query(
    query: AnalyticalQuery,
    project: Project = Depends(require_project_role(Role.VIEWER)),
    dataset: Dataset = Depends(get_latest_dataset),
    cache: PipelineCache = Depends(get_pipeline_cache),
    result_cache: ResultCache = Depends(get_result_cache),
) -> MetricResult:
    cache_key = build_cache_key(dataset.id, "analytics_query", query.model_dump(mode="json"))
    cached = result_cache.get(cache_key)
    if cached is not None:
        return MetricResult.model_validate_json(cached)

    engine = cache.get_or_build_engine(dataset)
    try:
        result = engine.run(query)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result_cache.set(cache_key, result.model_dump_json())
    return result
