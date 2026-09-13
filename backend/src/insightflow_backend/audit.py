"""Audit trail for mutating actions -- CLAUDE.md's tenant hierarchy names "audit" as a citizen of
the model from day one (§4), so this is wired into every mutating route now rather than
retrofitted later. `record` only adds the row to the session; it deliberately does not commit --
callers write it in the same transaction as the mutation it's describing, so the two can never
diverge (an audit entry for a write that got rolled back, or a write with no audit trail because
the commit happened first and the audit write failed after).
"""
import uuid

from sqlalchemy.orm import Session

from insightflow_backend.db.models import AuditLog


def record(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    detail: str | None = None,
) -> None:
    db.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            detail=detail,
        )
    )
