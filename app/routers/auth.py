from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.deps import AuthContext, get_auth_context
from app.schemas import AuthMeResponse, LoginRequest, SwitchTenantRequest, TokenResponse, UserSummary
from app.services.auth import (
    authenticate_user,
    issue_session,
    list_user_memberships,
    resolve_login_membership,
    revoke_refresh_cookie,
    rotate_refresh_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = authenticate_user(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    try:
        membership, tenant = resolve_login_membership(db, user, payload.tenant_slug)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        raise

    access_token, expires_in = issue_session(
        db,
        user=user,
        membership=membership,
        tenant=tenant,
        request=request,
        response=response,
        settings=settings,
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserSummary(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_platform_admin=user.is_platform_admin,
        ),
        tenant={
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "role": membership.role,
        },
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    user, membership, tenant, access_token, expires_in = rotate_refresh_session(
        db,
        refresh_token=refresh_token,
        request=request,
        response=response,
        settings=settings,
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserSummary(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_platform_admin=user.is_platform_admin,
        ),
        tenant={
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "role": membership.role,
        },
    )


@router.post("/logout")
def logout(response: Response, settings: Settings = Depends(get_settings)) -> dict:
    revoke_refresh_cookie(response, settings)
    return {"status": "ok"}


@router.get("/me", response_model=AuthMeResponse)
def me(context: AuthContext = Depends(get_auth_context)) -> AuthMeResponse:
    return AuthMeResponse(
        auth_type=context.auth_type,
        user=(
            UserSummary(
                id=context.user.id,
                email=context.user.email,
                full_name=context.user.full_name,
                is_platform_admin=context.user.is_platform_admin,
            )
            if context.user
            else None
        ),
        tenant={
            "id": context.tenant.id,
            "name": context.tenant.name,
            "slug": context.tenant.slug,
            "status": context.tenant.status,
            "role": context.membership.role if context.membership else None,
        },
        scopes=context.scopes,
    )


@router.get("/tenants")
def tenants(context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> list[dict]:
    if context.user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JWT login required")
    memberships = list_user_memberships(db, context.user.id)
    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "role": membership.role,
        }
        for membership, tenant in memberships
    ]


@router.post("/switch-tenant", response_model=TokenResponse)
def switch_tenant(
    payload: SwitchTenantRequest,
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if context.user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JWT login required")

    membership, tenant = resolve_login_membership(db, context.user, payload.tenant_slug)
    access_token, expires_in = issue_session(
        db,
        user=context.user,
        membership=membership,
        tenant=tenant,
        request=request,
        response=response,
        settings=settings,
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserSummary(
            id=context.user.id,
            email=context.user.email,
            full_name=context.user.full_name,
            is_platform_admin=context.user.is_platform_admin,
        ),
        tenant={
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "role": membership.role,
        },
    )
