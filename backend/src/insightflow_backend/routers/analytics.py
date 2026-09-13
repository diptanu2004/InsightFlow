from fastapi import APIRouter, Depends, HTTPException

from insightflow_core.models import AnalyticalQuery, MetricResult

from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.dataset_deps import get_latest_dataset, get_pipeline_cache
from insightflow_backend.db.models import Dataset, Project, Role
from insightflow_backend.pipeline_cache import PipelineCache

router = APIRouter()


@router.post("/projects/{project_id}/analytics/query", response_model=MetricResult, tags=["analytics"])
def run_query(
    query: AnalyticalQuery,
    project: Project = Depends(require_project_role(Role.VIEWER)),
    dataset: Dataset = Depends(get_latest_dataset),
    cache: PipelineCache = Depends(get_pipeline_cache),
) -> MetricResult:
    engine = cache.get_or_build_engine(dataset)
    try:
        return engine.run(query)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
