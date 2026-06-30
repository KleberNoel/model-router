from datetime import datetime
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import ApiKey, Tenant, TenantMembership, User
from app.security import api_key_prefix, decode_access_token, hash_secret, is_api_key

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    auth_type: str
    tenant: Tenant
    scopes: list[str]
    user: User | None = None
    membership: TenantMembership | None = None
    api_key: ApiKey | None = None


def _unauthorized(detail: str = "Unauthorized") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def get_settings_dep() -> Settings:
    return get_settings()


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AuthContext:
    token = x_api_key or (credentials.credentials if credentials else None)
    if not token:
        raise _unauthorized("Missing credentials")

    if is_api_key(token):
        try:
            prefix = api_key_prefix(token)
        except ValueError as exc:
            raise _unauthorized(str(exc)) from exc

        key_hash = hash_secret(token, settings)
        api_key = db.scalar(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.key_hash == key_hash,
                ApiKey.is_active.is_(True),
            )
        )
        if api_key is None:
            raise _unauthorized("Invalid API key")

        tenant = db.get(Tenant, api_key.tenant_id)
        if tenant is None or tenant.status != "active":
            raise _unauthorized("Tenant is inactive")

        api_key.last_used_at = datetime.utcnow()
        db.add(api_key)
        db.commit()
        return AuthContext(auth_type="api_key", tenant=tenant, scopes=api_key.scopes, api_key=api_key)

    try:
        payload = decode_access_token(settings, token)
    except Exception as exc:  # noqa: BLE001
        raise _unauthorized("Invalid access token") from exc

    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise _unauthorized("Unknown user")

    tenant = db.get(Tenant, payload.get("tenant_id"))
    if tenant is None or tenant.status != "active":
        raise _unauthorized("Tenant is inactive")

    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == tenant.id,
        )
    )
    if membership is None:
        raise _unauthorized("User is not a member of this tenant")

    scopes = payload.get("scope") or []
    return AuthContext(
        auth_type="jwt",
        user=user,
        tenant=tenant,
        membership=membership,
        scopes=scopes,
    )


def require_platform_admin(context: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if context.user is None or not context.user.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform admin required")
    return context
