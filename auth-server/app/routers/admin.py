from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import Invite, ResetToken, User, utcnow
from ..schemas import InviteIn, InviteOut, InviteRow, ResetLinkOut, UserOut, UserPatch
from ..security import new_token
from ..services import active_admins, norm_email, revoke_all_sessions

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def invite(body: InviteIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    st = request.app.state
    email = norm_email(body.email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    for old in db.scalars(select(Invite).where(Invite.tenant_id == admin.tenant_id, Invite.email == email, Invite.used_at.is_(None))):
        old.used_at = utcnow()   # a new invitation replaces any open one
    raw, h = new_token()
    inv = Invite(tenant_id=admin.tenant_id, email=email, role=body.role, token_hash=h, invited_by=admin.id, expires_at=utcnow() + timedelta(days=st.settings.invite_days))
    db.add(inv)
    db.commit()
    url = f"{st.settings.base_url}/accept-invite?token={raw}"
    sent = st.mailer.send(email, "You are invited to Recruit Desk",
                          f"You have been invited to Recruit Desk as {body.role}. Open this link to choose a password "
                          f"(valid {st.settings.invite_days} days):\n{url}")
    return InviteOut(email=email, role=body.role, expires_at=inv.expires_at, invite_url=url, email_sent=sent)


@router.get("/invites", response_model=list[InviteRow])
def pending_invites(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(Invite).where(Invite.tenant_id == admin.tenant_id, Invite.used_at.is_(None), Invite.expires_at > utcnow()).order_by(Invite.id)).all()


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invite_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Withdraw an invitation of this tenant that has not been used. The link stops working at once."""
    inv = db.get(Invite, invite_id)
    if inv is None or inv.tenant_id != admin.tenant_id or inv.used_at is not None or inv.expires_at <= utcnow():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such open invitation")
    inv.used_at = utcnow()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=list[UserOut])
def users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.tenant_id == admin.tenant_id).order_by(User.id)).all()


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(user_id: int, body: UserPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None or target.tenant_id != admin.tenant_id:   # another tenant's people do not exist for this admin
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    if target.id == admin.id and (body.role not in (None, target.role) or body.is_active is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role or disable yourself")
    losing_admin = target.role == "admin" and target.is_active and (body.role == "user" or body.is_active is False)
    if losing_admin and active_admins(db, admin.tenant_id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There must be at least one active admin")
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
        if not body.is_active:
            revoke_all_sessions(db, target.id)
    db.commit()
    return target


@router.post("/users/{user_id}/reset-link", response_model=ResetLinkOut)
def reset_link(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """A password-reset link for the admin to pass on (also mailed when SMTP is set up)."""
    st = request.app.state
    target = db.get(User, user_id)
    if target is None or target.tenant_id != admin.tenant_id or not target.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active user")
    raw, h = new_token()
    row = ResetToken(user_id=target.id, token_hash=h, expires_at=utcnow() + timedelta(minutes=st.settings.reset_minutes))
    db.add(row)
    db.commit()
    url = f"{st.settings.base_url}/reset-password?token={raw}"
    sent = st.mailer.send(target.email, "Reset your Recruit Desk password",
                          f"An admin started a password reset for you. Open this link (valid {st.settings.reset_minutes} minutes):\n{url}")
    return ResetLinkOut(reset_url=url, expires_at=row.expires_at, email_sent=sent)
