from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import Invite, RefreshToken, ResetToken, User, utcnow
from ..schemas import (AcceptInviteIn, ChangePasswordIn, ForgotIn, LoginIn, RefreshIn, ResetIn, TokenOut, UserOut)
from ..security import new_token, token_hash
from ..services import check_password_rules, client_ip, issue_tokens, norm_email, revoke_all_sessions

router = APIRouter(prefix="/auth", tags=["auth"])
BAD_LOGIN = "Invalid email or password"


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    st = request.app.state
    email, ip = norm_email(body.email), client_ip(request)
    ek, ik = f"email:{email}", f"ip:{ip}"
    if st.limiter.blocked(ek, st.settings.login_failures_per_email) or st.limiter.blocked(ik, st.settings.login_failures_per_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later.")
    user = db.scalar(select(User).where(User.email == email))
    ok = st.passwords.verify(user.password_hash if user else None, body.password)   # same work whether or not the account exists
    if not ok:
        st.limiter.record(ek)
        st.limiter.record(ik)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled. Ask an admin.")
    st.limiter.clear(ek)
    if st.passwords.needs_rehash(user.password_hash):
        user.password_hash = st.passwords.hash(body.password)
    user.last_login_at = utcnow()
    return issue_tokens(request, db, user)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(body.refresh_token)))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again.")
    if row.used_at is not None:
        # A token that was already exchanged came back: it may have been copied. End every session of that user.
        revoke_all_sessions(db, row.user_id)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again.")
    if row.revoked_at is not None:
        # Revoked by logout, a password change or disabling the account: a plain refusal. It must not end the user's other
        # sessions, or a device that missed a password change would sign out the one that just signed in again.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again.")
    user = db.get(User, row.user_id)
    if row.expires_at < utcnow() or user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again.")
    row.used_at = utcnow()
    return issue_tokens(request, db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshIn, db: Session = Depends(get_db)):
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(body.refresh_token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/accept-invite", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def accept_invite(body: AcceptInviteIn, request: Request, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invite).where(Invite.token_hash == token_hash(body.token)))
    if inv is None or inv.used_at is not None or inv.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is not valid any more. Ask an admin for a new one.")
    if db.scalar(select(User.id).where(User.email == inv.email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    check_password_rules(request, body.password, inv.email)
    user = User(tenant_id=inv.tenant_id, email=inv.email, password_hash=request.app.state.passwords.hash(body.password), role=inv.role, last_login_at=utcnow())
    db.add(user)
    inv.used_at = utcnow()
    db.flush()
    return issue_tokens(request, db, user)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Always answers the same, so it never says whether an email has an account. Mails a link only when SMTP is set up."""
    st = request.app.state
    email, ip = norm_email(body.email), client_ip(request)
    if st.limiter.blocked(f"forgot:{ip}", st.settings.login_failures_per_email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try again later.")
    st.limiter.record(f"forgot:{ip}")
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is not None:
        raw, h = new_token()
        db.add(ResetToken(user_id=user.id, token_hash=h, expires_at=utcnow() + timedelta(minutes=st.settings.reset_minutes)))
        db.commit()
        st.mailer.send(user.email, "Reset your Recruit Desk password",
                       f"Open this link to choose a new password (valid {st.settings.reset_minutes} minutes):\n"
                       f"{st.settings.base_url}/reset-password?token={raw}\n\nIf you did not ask for this, ignore this email.")
    return {"detail": "If that email has an account, a reset link has been sent."}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    row = db.scalar(select(ResetToken).where(ResetToken.token_hash == token_hash(body.token)))
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is not valid any more. Ask for a new one.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is not valid any more. Ask for a new one.")
    check_password_rules(request, body.password, user.email)
    user.password_hash = request.app.state.passwords.hash(body.password)
    row.used_at = utcnow()
    revoke_all_sessions(db, user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", response_model=TokenOut)
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = request.app.state
    if not st.passwords.verify(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is wrong")
    check_password_rules(request, body.new_password, user.email)
    user.password_hash = st.passwords.hash(body.new_password)
    revoke_all_sessions(db, user.id)   # every device signs in again; this one gets a fresh session below
    return issue_tokens(request, db, user)


@router.get("/public-key", response_class=PlainTextResponse)
def public_key(request: Request):
    """The key the app uses to check access tokens offline (Ed25519, PEM)."""
    return request.app.state.tokens.public_pem


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
