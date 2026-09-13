"""RBAC dependency factories. Membership is per (user, org) -- CLAUDE.md's hierarchy has one role
per org, not per project, so a project-scoped route resolves its org via `project.org_id` and
checks the same Membership table a straight org-scoped route would.

Role semantics (Phase 6 plan): Viewer = read-only (query/dashboard/chat, no upload); Analyst =
also upload/create datasets; Admin = manage projects/members; Owner = manage the org itself
(e.g. delete it). Enforced here purely as an ordinal >= check, not per-action special-casing,
except where a specific route needs a stronger guarantee (e.g. "can't remove the last Owner",
enforced in the route itself, not here).
"""
import uuid

from fastapi import Depends, HTTPException, Path
from sqlalchemy.orm import Session

from insightflow_backend.auth.dependencies import get_current_user
from insightflow_backend.db.models import Membership, Project, Role, User
from insightflow_backend.db.session import get_db

_ROLE_RANK = {Role.VIEWER: 0, Role.ANALYST: 1, Role.ADMIN: 2, Role.OWNER: 3}


def get_membership(db: Session, user_id: uuid.UUID, org_id: uuid.UUID) -> Membership | None:
    return db.query(Membership).filter(Membership.user_id == user_id, Membership.org_id == org_id).first()


def require_org_role(min_role: Role):
    """Dependency factory for routes shaped `/organizations/{org_id}/...`."""

    def dependency(
        org_id: uuid.UUID = Path(...),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Membership:
        membership = get_membership(db, user.id, org_id)
        if membership is None or _ROLE_RANK[membership.role] < _ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail="insufficient role for this organization")
        return membership

    return dependency


def require_project_role(min_role: Role):
    """Dependency factory for routes shaped `/projects/{project_id}/...`. Resolves the owning
    org from the project, then applies the exact same role check `require_org_role` does --
    there's no separate per-project role, so this is just a lookup hop, not a different policy.
    """

    def dependency(
        project_id: uuid.UUID = Path(...),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Project:
        project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")

        membership = get_membership(db, user.id, project.org_id)
        if membership is None or _ROLE_RANK[membership.role] < _ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail="insufficient role for this project")
        return project

    return dependency
