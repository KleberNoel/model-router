"""Google Tasks synchronization primitives.

The database is authoritative for local state. Google Tasks is an external
projection that can also provide user edits, which are imported when newer.
"""

import json
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import GoogleConnection, OutboxEvent, TodoItem


def encrypt_credentials(settings: Settings, credentials: dict) -> str:
    if not settings.google_oauth_encryption_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google credential encryption is not configured")
    try:
        return Fernet(settings.google_oauth_encryption_key.encode()).encrypt(json.dumps(credentials).encode()).decode()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invalid Google credential encryption key") from exc


def decrypt_credentials(settings: Settings, encrypted: str) -> dict:
    if not settings.google_oauth_encryption_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google credential encryption is not configured")
    try:
        return json.loads(Fernet(settings.google_oauth_encryption_key.encode()).decrypt(encrypted.encode()))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not decrypt Google credentials") from exc


def _parse_google_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def task_payload(todo: TodoItem) -> dict:
    payload = {
        "title": todo.title,
        "notes": todo.notes or "",
        "status": "completed" if todo.completed else "needsAction",
    }
    if todo.due_at:
        payload["due"] = todo.due_at.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return payload


def _connection(db: Session, *, tenant_id: str, user_id: str) -> GoogleConnection:
    connection = db.scalar(
        select(GoogleConnection).where(
            GoogleConnection.tenant_id == tenant_id,
            GoogleConnection.user_id == user_id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Google account is not connected")
    return connection


def _access_token(db: Session, connection: GoogleConnection, settings: Settings) -> str:
    credentials = decrypt_credentials(settings, connection.credentials_encrypted)
    expires_at = credentials.get("expires_at", 0)
    if expires_at and expires_at > datetime.now(timezone.utc).timestamp() + 60:
        return str(credentials["access_token"])
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google connection requires reauthorization")
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    response.raise_for_status()
    refreshed = response.json()
    credentials.update(refreshed)
    credentials["expires_at"] = datetime.now(timezone.utc).timestamp() + int(refreshed.get("expires_in", 3600))
    connection.credentials_encrypted = encrypt_credentials(settings, credentials)
    db.add(connection)
    db.commit()
    return str(credentials["access_token"])


def sync_google_tasks(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    settings: Settings,
) -> dict[str, int]:
    connection = _connection(db, tenant_id=tenant_id, user_id=user_id)
    access_token = _access_token(db, connection, settings)
    headers = {"Authorization": f"Bearer {access_token}"}
    base_url = "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks"
    remote_response = httpx.get(base_url, headers=headers, params={"showCompleted": "true", "showHidden": "true"}, timeout=20)
    remote_response.raise_for_status()
    remote_tasks = remote_response.json().get("items", [])
    imported = 0
    pushed = 0

    for remote in remote_tasks:
        remote_id = str(remote.get("id", ""))
        if not remote_id:
            continue
        local = db.scalar(
            select(TodoItem).where(
                TodoItem.tenant_id == tenant_id,
                TodoItem.user_id == user_id,
                TodoItem.external_provider == "google_tasks",
                TodoItem.external_id == remote_id,
            )
        )
        remote_updated = _parse_google_time(remote.get("updated"))
        if local is None:
            local = TodoItem(
                tenant_id=tenant_id,
                user_id=user_id,
                title=str(remote.get("title") or "Untitled task"),
                completed=remote.get("status") == "completed",
                due_at=_parse_google_time(remote.get("due")),
                notes=remote.get("notes"),
                external_provider="google_tasks",
                external_id=remote_id,
                external_updated_at=remote_updated,
                external_metadata_json={"etag": remote.get("etag")},
            )
            db.add(local)
            imported += 1
        elif remote_updated and (local.external_updated_at is None or remote_updated > local.external_updated_at):
            local.title = str(remote.get("title") or local.title)
            local.completed = remote.get("status") == "completed"
            local.due_at = _parse_google_time(remote.get("due"))
            local.notes = remote.get("notes")
            local.external_updated_at = remote_updated
            local.external_metadata_json = {"etag": remote.get("etag")}
            db.add(local)
            imported += 1

    pending = db.scalars(
        select(OutboxEvent).where(
            OutboxEvent.tenant_id == tenant_id,
            OutboxEvent.event_type.in_(["todo.created", "todo.updated"]),
            OutboxEvent.status == "pending",
            OutboxEvent.aggregate_type == "todo",
        ).order_by(OutboxEvent.created_at)
    ).all()
    for event in pending:
        todo = db.get(TodoItem, event.aggregate_id)
        if todo is None or todo.user_id != user_id:
            continue
        if todo.external_provider == "google_tasks" and todo.external_id:
            response = httpx.patch(
                f"{base_url}/{todo.external_id}",
                headers={**headers, "Content-Type": "application/json"},
                json=task_payload(todo),
                timeout=20,
            )
        else:
            response = httpx.post(
                base_url,
                headers={**headers, "Content-Type": "application/json"},
                json=task_payload(todo),
                timeout=20,
            )
        response.raise_for_status()
        remote = response.json()
        todo.external_provider = "google_tasks"
        todo.external_id = str(remote["id"])
        todo.external_updated_at = _parse_google_time(remote.get("updated"))
        todo.external_metadata_json = {"etag": remote.get("etag")}
        event.status = "sent"
        event.attempts += 1
        db.add(todo)
        db.add(event)
        pushed += 1

    db.commit()
    return {"imported": imported, "pushed": pushed}
