import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import ApiKey, ModelRoute, Tenant, TenantMembership, User
from app.routers import auth, health, openai
from app.routers import admin as admin_router
from app.security import api_key_prefix, hash_password, hash_secret
from app.services.model_manager import shutdown_llama_server_manager

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin_router.router)
app.include_router(openai.router)


def _parse_bootstrap_items(raw: str | None, *, env_name: str) -> list[dict]:
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must contain valid JSON") from exc
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"{env_name} must be a JSON array of objects")
    return items


def _tenant_by_slug(db, slug: str) -> Tenant | None:
    return db.scalar(select(Tenant).where(Tenant.slug == slug))


def _tenant_ids_from_item(db, item: dict) -> list[str]:
    if isinstance(item.get("allowed_tenant_ids"), list):
        return [str(value) for value in item["allowed_tenant_ids"]]

    slugs = item.get("allowed_tenant_slugs") or []
    if not isinstance(slugs, list):
        return []

    tenant_ids: list[str] = []
    for slug in slugs:
        tenant = _tenant_by_slug(db, str(slug))
        if tenant is not None:
            tenant_ids.append(tenant.id)
    return tenant_ids


def _bootstrap_routes(db) -> None:
    for item in _parse_bootstrap_items(
        settings.bootstrap_routes_json,
        env_name="MODEL_ROUTER_BOOTSTRAP_ROUTES_JSON",
    ):
        name = str(item.get("name") or "").strip()
        upstream_base_url = str(item.get("upstream_base_url") or "").strip()
        upstream_model_name = str(item.get("upstream_model_name") or "").strip()
        if not name or not upstream_base_url or not upstream_model_name:
            continue

        existing = db.scalar(select(ModelRoute).where(ModelRoute.name == name))
        if existing is not None:
            continue

        upstream_headers = item.get("upstream_headers") if isinstance(item.get("upstream_headers"), dict) else {}
        db.add(
            ModelRoute(
                name=name,
                description=item.get("description"),
                upstream_base_url=upstream_base_url,
                upstream_model_name=upstream_model_name,
                upstream_headers_json=upstream_headers,
                allowed_tenant_ids=_tenant_ids_from_item(db, item),
                max_context_tokens=item.get("max_context_tokens"),
                system_prompt=item.get("system_prompt"),
                is_active=bool(item.get("is_active", True)),
            )
        )

    db.commit()


def _bootstrap_api_keys(db, *, admin_user: User | None, default_tenant: Tenant | None) -> None:
    for item in _parse_bootstrap_items(
        settings.bootstrap_api_keys_json,
        env_name="MODEL_ROUTER_BOOTSTRAP_API_KEYS_JSON",
    ):
        raw_key = str(item.get("raw_key") or "").strip()
        if not raw_key:
            continue

        try:
            prefix = api_key_prefix(raw_key)
        except ValueError:
            continue

        existing = db.scalar(select(ApiKey).where(ApiKey.key_prefix == prefix))
        if existing is not None:
            continue

        tenant = None
        tenant_id = item.get("tenant_id")
        if tenant_id:
            tenant = db.get(Tenant, str(tenant_id))
        elif item.get("tenant_slug"):
            tenant = _tenant_by_slug(db, str(item["tenant_slug"]))
        else:
            tenant = default_tenant

        if tenant is None:
            continue

        scopes = item.get("scopes") if isinstance(item.get("scopes"), list) else ["chat:completions", "models:read"]
        db.add(
            ApiKey(
                tenant_id=tenant.id,
                created_by_user_id=admin_user.id if admin_user else None,
                name=str(item.get("name") or "Bootstrap API Key"),
                key_prefix=prefix,
                key_hash=hash_secret(raw_key, settings),
                scopes=[str(scope) for scope in scopes],
            )
        )

    db.commit()


def bootstrap_defaults() -> None:
    db = SessionLocal()
    try:
        admin_user = None
        tenant = None

        if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
            admin_user = db.scalar(select(User).where(User.email == settings.bootstrap_admin_email))
            if admin_user is None:
                admin_user = User(
                    email=settings.bootstrap_admin_email,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    full_name="Bootstrap Admin",
                    is_platform_admin=True,
                )
                db.add(admin_user)
                db.commit()
                db.refresh(admin_user)

        if settings.bootstrap_tenant_slug and settings.bootstrap_tenant_name:
            tenant = db.scalar(select(Tenant).where(Tenant.slug == settings.bootstrap_tenant_slug))
            if tenant is None:
                tenant = Tenant(name=settings.bootstrap_tenant_name, slug=settings.bootstrap_tenant_slug)
                db.add(tenant)
                db.commit()
                db.refresh(tenant)

        if admin_user and tenant:
            membership = db.scalar(
                select(TenantMembership).where(
                    TenantMembership.user_id == admin_user.id,
                    TenantMembership.tenant_id == tenant.id,
                )
            )
            if membership is None:
                db.add(TenantMembership(user_id=admin_user.id, tenant_id=tenant.id, role="owner"))
                db.commit()

        if (
            settings.default_model_name
            and settings.default_upstream_base_url
            and settings.default_upstream_model_name
        ):
            route = db.scalar(select(ModelRoute).where(ModelRoute.name == settings.default_model_name))
            if route is None:
                db.add(
                    ModelRoute(
                        name=settings.default_model_name,
                        upstream_base_url=settings.default_upstream_base_url,
                        upstream_model_name=settings.default_upstream_model_name,
                        allowed_tenant_ids=[tenant.id] if tenant else [],
                    )
                )
                db.commit()

        _bootstrap_routes(db)
        _bootstrap_api_keys(db, admin_user=admin_user, default_tenant=tenant)
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    bootstrap_defaults()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await shutdown_llama_server_manager()
