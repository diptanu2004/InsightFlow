import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from insightflow_core.models import SemanticModel

from insightflow_backend import audit
from insightflow_backend.auth.dependencies import get_current_user
from insightflow_backend.auth.rbac import require_project_role
from insightflow_backend.dataset_deps import get_latest_dataset, get_pipeline_cache
from insightflow_backend.db.models import Dataset, Project, Role, User
from insightflow_backend.db.session import get_db
from insightflow_backend.pipeline_cache import PipelineCache
from insightflow_backend.powerbi.export import build_export_zip
from insightflow_backend.registry.bootstrap import bootstrap_registry

router = APIRouter()


@router.get(
    "/projects/{project_id}/powerbi/export",
    tags=["powerbi"],
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}, "description": "A Power BI project (PBIP) and this dataset's CSVs."}},
)
def export_powerbi(
    project: Project = Depends(require_project_role(Role.ANALYST)),
    dataset: Dataset = Depends(get_latest_dataset),
    cache: PipelineCache = Depends(get_pipeline_cache),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Download this project's active dataset as a Power BI project, openable in Power BI Desktop.

    **Analyst, not Viewer, deliberately.** Every other read route returns numbers the engine computed,
    with caveats, truncation flags and number-grounding checks attached; this one returns the raw rows
    themselves, and once the zip is on a disk none of the project's roles apply to it any more. A
    Viewer may read results here without being able to walk away with the underlying data.

    Audit-logged despite being a GET, for the same reason: this is a bulk data egress event, which is
    the thing an audit trail most needs to show. `audit.record` only adds the row, so the commit below
    writes it in one transaction, matching every mutating route's pattern.

    Not cached in `ResultCache`. A dataset is immutable, so the bytes would be stable -- but the CSVs
    are the bulk of them (a real Olist export is ~50 MB), and Redis is not a file store. Rebuilding is
    a few seconds of local DuckDB profiling.
    """
    semantic_model = SemanticModel(**dataset.semantic_model)
    data_dir = cache.get_data_dir(dataset)

    try:
        blob = build_export_zip(dataset.name, semantic_model, bootstrap_registry(), data_dir)
    except ValueError as exc:
        # Same 422 boundary the analytics/dashboard/chat routes use for a dataset the engine can't
        # work with (an unreadable source file, a semantic model with nothing trusted left in it).
        raise HTTPException(status_code=422, detail=str(exc))

    audit.record(
        db,
        org_id=project.org_id,
        user_id=user.id,
        action="dataset.powerbi_exported",
        resource_type="dataset",
        resource_id=dataset.id,
        detail=f"{len(blob)} bytes",
    )
    db.commit()

    filename = f"{dataset.name or 'insightflow'}-powerbi.zip".replace('"', "")
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
