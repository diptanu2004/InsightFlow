import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, ConfigDict
from sqlalchemy.orm import Session

from insightflow_backend.auth.dependencies import get_current_user
from insightflow_backend.auth.rbac import get_membership, require_org_role
from insightflow_backend.db.models import Membership, Organization, Role, User
from insightflow_backend.db.session import get_db

router = APIRouter()

_SLUG_SANITIZE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    base = _SLUG_SANITIZE.sub("-", name.lower()).strip("-") or "org"
    return base


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug = base
    suffix = 1
    while db.query(Organization).filter(Organization.slug == slug).first() is not None:
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


class CreateOrganizationRequest(BaseModel):
    name: str


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    my_role: Role


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: uuid.UUID
    email: str
    role: Role


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: Role


class ChangeRoleRequest(BaseModel):
    role: Role


@router.post("", response_model=OrganizationOut, status_code=201)
def create_organization(
    body: CreateOrganizationRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> OrganizationOut:
    org = Organization(name=body.name, slug=_unique_slug(db, body.name))
    db.add(org)
    db.flush()
    # The creator is always the org's first Owner -- there's no other way to bootstrap
    # membership, since every other route requires an existing membership to act at all.
    db.add(Membership(user_id=user.id, org_id=org.id, role=Role.OWNER))
    db.commit()
    db.refresh(org)
    return OrganizationOut(id=org.id, name=org.name, slug=org.slug, created_at=org.created_at, my_role=Role.OWNER)


@router.get("", response_model=list[OrganizationOut])
def list_my_organizations(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[OrganizationOut]:
    memberships = db.query(Membership).filter(Membership.user_id == user.id).all()
    return [
        OrganizationOut(
            id=m.organization.id,
            name=m.organization.name,
            slug=m.organization.slug,
            created_at=m.organization.created_at,
            my_role=m.role,
        )
        for m in memberships
    ]


@router.get("/{org_id}/members", response_model=list[MemberOut])
def list_members(
    membership: Membership = Depends(require_org_role(Role.VIEWER)), db: Session = Depends(get_db)
) -> list[MemberOut]:
    members = db.query(Membership).filter(Membership.org_id == membership.org_id).all()
    return [MemberOut(user_id=m.user_id, email=m.user.email, role=m.role) for m in members]


@router.post("/{org_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    body: AddMemberRequest,
    membership: Membership = Depends(require_org_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> MemberOut:
    target_user = db.query(User).filter(User.email == body.email).first()
    if target_user is None:
        # No invite-by-email flow yet (needs transactional email, out of scope for Phase 6 --
        # see the Phase 6 plan doc): the invitee must already have an InsightFlow account.
        raise HTTPException(status_code=404, detail="no user registered with that email")

    if get_membership(db, target_user.id, membership.org_id) is not None:
        raise HTTPException(status_code=409, detail="user is already a member of this organization")

    if body.role == Role.OWNER and membership.role != Role.OWNER:
        # Only an Owner can create another Owner -- an Admin adding a member could otherwise
        # hand out org-deletion rights it doesn't itself have.
        raise HTTPException(status_code=403, detail="only an Owner can grant the Owner role")

    new_membership = Membership(user_id=target_user.id, org_id=membership.org_id, role=body.role)
    db.add(new_membership)
    db.commit()
    return MemberOut(user_id=target_user.id, email=target_user.email, role=body.role)


def _count_owners(db: Session, org_id: uuid.UUID) -> int:
    return db.query(Membership).filter(Membership.org_id == org_id, Membership.role == Role.OWNER).count()


@router.patch("/{org_id}/members/{user_id}", response_model=MemberOut)
def change_member_role(
    user_id: uuid.UUID,
    body: ChangeRoleRequest,
    membership: Membership = Depends(require_org_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> MemberOut:
    target = get_membership(db, user_id, membership.org_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user is not a member of this organization")

    if (body.role == Role.OWNER or target.role == Role.OWNER) and membership.role != Role.OWNER:
        raise HTTPException(status_code=403, detail="only an Owner can grant or change the Owner role")

    if target.role == Role.OWNER and body.role != Role.OWNER and _count_owners(db, membership.org_id) == 1:
        raise HTTPException(status_code=409, detail="cannot demote the organization's last Owner")

    target.role = body.role
    db.commit()
    return MemberOut(user_id=target.user_id, email=target.user.email, role=target.role)


@router.delete("/{org_id}/members/{user_id}", status_code=204)
def remove_member(
    user_id: uuid.UUID,
    membership: Membership = Depends(require_org_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> None:
    target = get_membership(db, user_id, membership.org_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user is not a member of this organization")

    if target.role == Role.OWNER and membership.role != Role.OWNER:
        raise HTTPException(status_code=403, detail="only an Owner can remove an Owner")

    if target.role == Role.OWNER and _count_owners(db, membership.org_id) == 1:
        raise HTTPException(status_code=409, detail="cannot remove the organization's last Owner")

    db.delete(target)
    db.commit()
