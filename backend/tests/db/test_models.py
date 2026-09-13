import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from insightflow_backend.db.models import (
    AuditLog,
    Dataset,
    Membership,
    Organization,
    Project,
    RefreshToken,
    Role,
    User,
)
from tests.db.conftest import requires_postgres


def _make_user(db_session, email="alice@example.com") -> User:
    user = User(email=email, hashed_password="not-a-real-hash")
    db_session.add(user)
    db_session.flush()
    return user


def _make_org(db_session, name="Acme") -> Organization:
    org = Organization(name=name, slug=name.lower())
    db_session.add(org)
    db_session.flush()
    return org


@requires_postgres
def test_create_user(db_session):
    user = _make_user(db_session)
    assert user.id is not None
    assert user.is_active is True


@requires_postgres
def test_user_email_must_be_unique(db_session):
    _make_user(db_session, email="dupe@example.com")
    db_session.add(User(email="dupe@example.com", hashed_password="x"))
    with pytest.raises(IntegrityError):
        db_session.flush()


@requires_postgres
def test_membership_ties_user_to_org_with_role(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session)
    membership = Membership(user_id=user.id, org_id=org.id, role=Role.ADMIN)
    db_session.add(membership)
    db_session.flush()

    fetched = db_session.get(Membership, membership.id)
    assert fetched.role == Role.ADMIN
    assert fetched.user.email == user.email
    assert fetched.organization.slug == org.slug


@requires_postgres
def test_membership_is_unique_per_user_and_org(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session)
    db_session.add(Membership(user_id=user.id, org_id=org.id, role=Role.VIEWER))
    db_session.flush()

    db_session.add(Membership(user_id=user.id, org_id=org.id, role=Role.OWNER))
    with pytest.raises(IntegrityError):
        db_session.flush()


@requires_postgres
def test_project_and_dataset_hierarchy(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session)
    project = Project(org_id=org.id, name="Q3 Analytics")
    db_session.add(project)
    db_session.flush()

    dataset = Dataset(
        project_id=project.id,
        name="orders-2024",
        storage_prefix=f"{org.id}/{project.id}/{uuid.uuid4()}",
        semantic_model={"entities": []},
        created_by=user.id,
    )
    db_session.add(dataset)
    db_session.flush()

    fetched = db_session.get(Dataset, dataset.id)
    assert fetched.semantic_model == {"entities": []}
    assert fetched.project.org_id == org.id


@requires_postgres
def test_dataset_requires_existing_project(db_session):
    user = _make_user(db_session)
    db_session.add(
        Dataset(
            project_id=uuid.uuid4(),
            name="orphan",
            storage_prefix="nowhere",
            semantic_model={},
            created_by=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


@requires_postgres
def test_refresh_token_lifecycle(db_session):
    from datetime import datetime, timedelta, timezone

    user = _make_user(db_session)
    token = RefreshToken(
        user_id=user.id,
        token_hash="hashed-token-value",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(token)
    db_session.flush()

    assert token.revoked_at is None
    token.revoked_at = datetime.now(timezone.utc)
    db_session.flush()
    assert db_session.get(RefreshToken, token.id).revoked_at is not None


@requires_postgres
def test_audit_log_records_action(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session)
    entry = AuditLog(
        org_id=org.id,
        user_id=user.id,
        action="dataset.created",
        resource_type="dataset",
        resource_id=str(uuid.uuid4()),
    )
    db_session.add(entry)
    db_session.flush()
    assert db_session.get(AuditLog, entry.id).action == "dataset.created"
