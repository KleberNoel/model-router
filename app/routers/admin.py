from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.deps import AuthContext, require_platform_admin
from app.models import ApiKey, ModelRoute, Tenant, TenantMembership, UsageLog, User
from app.schemas import (
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    CreateModelRouteRequest,
    CreateTenantRequest,
    CreateUserRequest,
    GrantMembershipRequest,
    ModelRouteResponse,
    UpdateModelRouteRequest,
)
from app.security import hash_password, hash_secret, new_api_key
from app.services.audit import log_audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/usage")
def usage_summary(
    db: Session = Depends(get_db),
    _: AuthContext = Depends(require_platform_admin),
    tenant_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    statement = select(
        UsageLog.tenant_id,
        UsageLog.model_route_id,
        UsageLog.status_code,
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total_tokens"),
        func.avg(UsageLog.latency_ms).label("average_latency_ms"),
    ).group_by(UsageLog.tenant_id, UsageLog.model_route_id, UsageLog.status_code)
    if tenant_id:
        statement = statement.where(UsageLog.tenant_id == tenant_id)
    rows = db.execute(statement.limit(max(1, min(limit, 1000)))).all()
    return [
        {
            "tenant_id": row.tenant_id,
            "model_route_id": row.model_route_id,
            "status_code": row.status_code,
            "requests": row.requests,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "total_tokens": row.total_tokens,
            "average_latency_ms": float(row.average_latency_ms or 0),
        }
        for row in rows
    ]


@router.post("/tenants")
def create_tenant(
    payload: CreateTenantRequest,
    db: Session = Depends(get_db),
    admin: AuthContext = Depends(require_platform_admin),
) -> dict:
    existing = db.scalar(select(Tenant).where(Tenant.slug == payload.slug))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant slug already exists")

    tenant = Tenant(name=payload.name, slug=payload.slug, metadata_json=payload.metadata)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    log_audit(db, actor=admin, action="tenant.create", resource_type="tenant", resource_id=tenant.id)
    return {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status}


@router.get("/tenants")
def list_tenants(
    db: Session = Depends(get_db),
    _: AuthContext = Depends(require_platform_admin),
) -> list[dict]:
    tenants = list(db.scalars(select(Tenant).order_by(Tenant.slug.asc())).all())
    return [{"id": t.id, "name": t.name, "slug": t.slug, "status": t.status} for t in tenants]


@router.post("/users")
def create_user(
    payload: CreateUserRequest,
    db: Session = Depends(get_db),
    admin: AuthContext = Depends(require_platform_admin),
) -> dict:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        is_platform_admin=payload.is_platform_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_audit(db, actor=admin, action="user.create", resource_type="user", resource_id=user.id)
    return {"id": user.id, "email": user.email, "full_name": user.full_name}


@router.post("/memberships")
def grant_membership(
    payload: GrantMembershipRequest,
    db: Session = Depends(get_db),
    admin: AuthContext = Depends(require_platform_admin),
) -> dict:
    user = db.get(User, payload.user_id)
    tenant = db.get(Tenant, payload.tenant_id)
    if user is None or tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User or tenant not found")

    existing = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == tenant.id,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Membership already exists")

    membership = TenantMembership(user_id=user.id, tenant_id=tenant.id, role=payload.role)
    db.add(membership)
    db.commit()
    log_audit(
        db,
        actor=admin,
        action="membership.create",
        resource_type="tenant_membership",
        resource_id=membership.id,
        details={"user_id": user.id, "tenant_id": tenant.id, "role": payload.role},
    )
    return {"status": "ok", "user_id": user.id, "tenant_id": tenant.id, "role": payload.role}


@router.post("/model-routes", response_model=ModelRouteResponse)
def create_model_route(
    payload: CreateModelRouteRequest,
    db: Session = Depends(get_db),
    admin: AuthContext = Depends(require_platform_admin),
) -> ModelRoute:
    existing = db.scalar(select(ModelRoute).where(ModelRoute.name == payload.name))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Model route already exists")

    route = ModelRoute(
        name=payload.name,
        description=payload.description,
        upstream_base_url=payload.upstream_base_url,
        upstream_model_name=payload.upstream_model_name,
        upstream_headers_json=payload.upstream_headers,
        allowed_tenant_ids=payload.allowed_tenant_ids,
        max_context_tokens=payload.max_context_tokens,
        system_prompt=payload.system_prompt,
        capabilities_json=payload.capabilities,
        is_active=payload.is_active,
    )
    db.add(route)
    db.commit()
    db.refresh(route)
    log_audit(db, actor=admin, action="model_route.create", resource_type="model_route", resource_id=route.id)
    return route


@router.get("/model-routes", response_model=list[ModelRouteResponse])
def list_model_routes(
    db: Session = Depends(get_db),
    _: AuthContext = Depends(require_platform_admin),
) -> list[ModelRoute]:
    return list(db.scalars(select(ModelRoute).order_by(ModelRoute.name.asc())).all())


@router.patch("/model-routes/{route_id}", response_model=ModelRouteResponse)
def update_model_route(
    route_id: str,
    payload: UpdateModelRouteRequest,
    db: Session = Depends(get_db),
    admin: AuthContext = Depends(require_platform_admin),
) -> ModelRoute:
    route = db.get(ModelRoute, route_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model route not found")

    fields = {
        "description": payload.description,
        "upstream_base_url": payload.upstream_base_url,
        "upstream_model_name": payload.upstream_model_name,
        "upstream_headers_json": payload.upstream_headers,
        "allowed_tenant_ids": payload.allowed_tenant_ids,
        "max_context_tokens": payload.max_context_tokens,
        "system_prompt": payload.system_prompt,
        "capabilities_json": payload.capabilities,
        "is_active": payload.is_active,
    }
    for name, value in fields.items():
        if value is not None:
            setattr(route, name, value)
    db.add(route)
    db.commit()
    db.refresh(route)
    log_audit(db, actor=admin, action="model_route.update", resource_type="model_route", resource_id=route.id)
    return route


@router.post("/api-keys", response_model=CreateApiKeyResponse)
def create_api_key(
    payload: CreateApiKeyRequest,
    db: Session = Depends(get_db),
    admin: AuthContext = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
) -> CreateApiKeyResponse:
    tenant = db.get(Tenant, payload.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    raw_key, prefix = new_api_key()
    api_key = ApiKey(
        tenant_id=tenant.id,
        created_by_user_id=admin.user.id if admin.user else None,
        name=payload.name,
        key_prefix=prefix,
        key_hash=hash_secret(raw_key, settings),
        scopes=payload.scopes,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    log_audit(db, actor=admin, action="api_key.create", resource_type="api_key", resource_id=api_key.id)
    return CreateApiKeyResponse(
        api_key=raw_key,
        key_prefix=api_key.key_prefix,
        tenant_id=tenant.id,
        name=api_key.name,
        scopes=api_key.scopes,
    )
