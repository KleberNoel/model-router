import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import Settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    settings: Settings,
    *,
    subject: str,
    tenant_id: str,
    role: str,
    scopes: list[str],
) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "scope": scopes,
        "jti": secrets.token_hex(16),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int((expires - now).total_seconds())


def decode_access_token(settings: Settings, token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def new_api_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    raw = f"mrk_{prefix}_{secret}"
    return raw, prefix


def hash_secret(secret_value: str, settings: Settings) -> str:
    digest = hmac.new(
        key=settings.jwt_secret_key.encode("utf-8"),
        msg=secret_value.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return digest.hexdigest()


def is_api_key(token: str) -> bool:
    return token.startswith("mrk_")


def api_key_prefix(api_key: str) -> str:
    parts = api_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "mrk":
        raise ValueError("Invalid API key format")
    return parts[1]
