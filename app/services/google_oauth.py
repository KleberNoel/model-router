import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import GoogleConnection, OAuthState
from app.services.google_sync import encrypt_credentials


def _hash_state(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def start_google_oauth(db: Session, *, tenant_id: str, user_id: str, settings: Settings) -> str:
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google OAuth is not configured")
    raw_state = secrets.token_urlsafe(32)
    db.add(
        OAuthState(
            provider="google",
            state_hash=_hash_state(raw_state),
            tenant_id=tenant_id,
            user_id=user_id,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
    )
    db.commit()
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(settings.google_oauth_scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": raw_state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def finish_google_oauth(db: Session, *, code: str, raw_state: str, settings: Settings) -> GoogleConnection:
    state = db.scalar(
        select(OAuthState).where(
            OAuthState.provider == "google",
            OAuthState.state_hash == _hash_state(raw_state),
            OAuthState.used_at.is_(None),
        )
    )
    if state is None or state.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired Google OAuth state")
    state.used_at = datetime.utcnow()
    db.add(state)

    token_response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if token_response.is_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google token exchange failed")
    credentials = token_response.json()
    credentials["expires_at"] = datetime.now(timezone.utc).timestamp() + int(credentials.get("expires_in", 3600))
    userinfo = httpx.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {credentials['access_token']}"},
        timeout=20,
    )
    if userinfo.is_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google userinfo lookup failed")
    identity = userinfo.json()
    connection = db.scalar(
        select(GoogleConnection).where(
            GoogleConnection.tenant_id == state.tenant_id,
            GoogleConnection.user_id == state.user_id,
        )
    )
    encrypted = encrypt_credentials(settings, credentials)
    if connection is None:
        connection = GoogleConnection(
            tenant_id=state.tenant_id,
            user_id=state.user_id,
            google_subject=str(identity["sub"]),
            email=identity.get("email"),
            credentials_encrypted=encrypted,
            scopes_json=settings.google_oauth_scopes,
        )
    else:
        connection.google_subject = str(identity["sub"])
        connection.email = identity.get("email")
        connection.credentials_encrypted = encrypted
        connection.scopes_json = settings.google_oauth_scopes
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection
