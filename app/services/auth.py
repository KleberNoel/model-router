from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import RefreshToken, Tenant, TenantMembership, User
from app.schemas import TenantSummary
from app.security import (
    create_access_token,
    hash_secret,
    new_refresh_token,
    verify_password,
)

DEFAULT_SCOPES = ["chat:completions", "models:read"]


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def list_user_memberships(db: Session, user_id: str) -> list[tuple[TenantMembership, Tenant]]:
    stmt = (
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user_id)
        .order_by(Tenant.slug.asc())
    )
    return list(db.execute(stmt).all())


def membership_summaries(db: Session, user_id: str) -> list[TenantSummary]:
    memberships = list_user_memberships(db, user_id)
    return [
        TenantSummary(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=tenant.status,
            role=membership.role,
        )
        for membership, tenant in memberships
    ]


def resolve_login_membership(db: Session, user: User, tenant_slug: str | None) -> tuple[TenantMembership, Tenant]:
    memberships = list_user_memberships(db, user.id)
    if not memberships:
        raise HTTPException(status_code=403, detail="User has no tenant memberships")

    if tenant_slug:
        for membership, tenant in memberships:
            if tenant.slug == tenant_slug:
                return membership, tenant
        raise HTTPException(status_code=404, detail="Tenant not found for user")

    if len(memberships) == 1:
        return memberships[0]

    raise HTTPException(
        status_code=409,
        detail={
            "message": "tenant selection required",
            "available_tenants": [summary.model_dump() for summary in membership_summaries(db, user.id)],
        },
    )


def _set_refresh_cookie(response: Response, settings: Settings, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path="/",
    )


def issue_session(
    db: Session,
    *,
    user: User,
    membership: TenantMembership,
    tenant: Tenant,
    request: Request,
    response: Response,
    settings: Settings,
) -> tuple[str, int]:
    access_token, expires_in = create_access_token(
        settings,
        subject=user.id,
        tenant_id=tenant.id,
        role=membership.role,
        scopes=DEFAULT_SCOPES,
    )

    refresh_token = new_refresh_token()
    expires_at = datetime.utcnow() + timedelta(days=settings.refresh_token_ttl_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            tenant_id=tenant.id,
            token_hash=hash_secret(refresh_token, settings),
            expires_at=expires_at,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    _set_refresh_cookie(response, settings, refresh_token)
    return access_token, expires_in


def rotate_refresh_session(
    db: Session,
    *,
    refresh_token: str,
    request: Request,
    response: Response,
    settings: Settings,
) -> tuple[User, TenantMembership, Tenant, str, int]:
    token_hash = hash_secret(refresh_token, settings)
    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if stored is None or stored.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if stored.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    user = db.get(User, stored.user_id)
    tenant = db.get(Tenant, stored.tenant_id)
    if user is None or tenant is None or not user.is_active or tenant.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is no longer valid")

    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == tenant.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Membership is no longer valid")

    stored.revoked_at = datetime.utcnow()
    db.add(stored)
    db.commit()

    access_token, expires_in = issue_session(
        db,
        user=user,
        membership=membership,
        tenant=tenant,
        request=request,
        response=response,
        settings=settings,
    )
    return user, membership, tenant, access_token, expires_in


def revoke_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.refresh_cookie_name, path="/")
