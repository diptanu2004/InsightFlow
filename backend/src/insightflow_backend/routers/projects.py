"""Mixed-prefix router (mounted with no prefix in main.py): project creation/listing hang off
`/organizations/{org_id}/projects` since a project always belongs to an org, while single-project
routes use `/projects/{project_id}` directly once the caller already has a project id -- same
convention as GitHub/GitLab's own API shape for org-owned resources.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from insightflow_backend.auth.rbac import require_org_role, require_project_role
from insightflow_backend.db.models import Membership, Project, Role
from insightflow_backend.db.session import get_db

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    created_at: datetime


@router.post("/organizations/{org_id}/projects", response_model=ProjectOut, status_code=201, tags=["projects"])
def create_project(
    body: CreateProjectRequest,
    membership: Membership = Depends(require_org_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> ProjectOut:
    project = Project(org_id=membership.org_id, name=body.name)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectOut.model_validate(project)


@router.get("/organizations/{org_id}/projects", response_model=list[ProjectOut], tags=["projects"])
def list_projects(
    membership: Membership = Depends(require_org_role(Role.VIEWER)), db: Session = Depends(get_db)
) -> list[ProjectOut]:
    projects = db.query(Project).filter(Project.org_id == membership.org_id).all()
    return [ProjectOut.model_validate(p) for p in projects]


@router.get("/projects/{project_id}", response_model=ProjectOut, tags=["projects"])
def get_project(project: Project = Depends(require_project_role(Role.VIEWER))) -> ProjectOut:
    return ProjectOut.model_validate(project)


@router.delete("/projects/{project_id}", status_code=204, tags=["projects"])
def delete_project(
    project: Project = Depends(require_project_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> None:
    db.delete(project)
    db.commit()
