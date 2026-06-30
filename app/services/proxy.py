import uuid
from collections.abc import AsyncIterator
import json
from pathlib import Path

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ModelRoute


_COPILOT_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


def _copilot_headers() -> dict[str, str]:
    data = json.loads(_COPILOT_AUTH_PATH.read_text())
    token = data["github-copilot"]["refresh"]
    return {
        "Authorization": f"Bearer {token}",
        "Editor-Version": "vscode/1.104.1",
        "User-Agent": "HermesAgent/1.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-edits",
    }


def _upstream_headers(route: ModelRoute, *, accept: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
        "X-Request-ID": str(uuid.uuid4()),
    }
    if isinstance(route.upstream_headers_json, dict) and route.upstream_headers_json:
        headers.update({str(k): str(v) for k, v in route.upstream_headers_json.items()})
    if route.upstream_base_url.rstrip("/").lower().startswith("https://api.githubcopilot.com"):
        headers.update(_copilot_headers())
    return headers


def list_accessible_models(db: Session, tenant_id: str) -> list[ModelRoute]:
    routes = list(db.scalars(select(ModelRoute).where(ModelRoute.is_active.is_(True))).all())
    return [route for route in routes if not route.allowed_tenant_ids or tenant_id in route.allowed_tenant_ids]


def resolve_model_route(db: Session, tenant_id: str, public_name: str) -> ModelRoute:
    route = db.scalar(
        select(ModelRoute).where(ModelRoute.name == public_name, ModelRoute.is_active.is_(True))
    )
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown model")
    if route.allowed_tenant_ids and tenant_id not in route.allowed_tenant_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Model not allowed for tenant")
    return route


def openai_model_list(routes: list[ModelRoute]) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": route.name,
                "object": "model",
                "owned_by": "model-router",
                "context_window": route.max_context_tokens,
            }
            for route in routes
        ],
    }


def extract_usage(payload: dict) -> tuple[int | None, int | None, int | None]:
    usage = payload.get("usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")


def upstream_url(route: ModelRoute, path: str) -> str:
    return f"{route.upstream_base_url.rstrip('/')}/{path.lstrip('/')}"


async def proxy_json(
    *,
    route: ModelRoute,
    path: str,
    payload: dict,
    settings: Settings,
    upstream_base_url: str | None = None,
) -> tuple[int, dict | str, str]:
    request_id = str(uuid.uuid4())
    headers = _upstream_headers(route, accept="application/json")
    headers["X-Request-ID"] = request_id
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        verify=settings.upstream_verify_tls,
    ) as client:
        target_base_url = upstream_base_url or route.upstream_base_url
        response = await client.post(
            f"{target_base_url.rstrip('/')}/{path.lstrip('/')}",
            json=payload,
            headers=headers,
        )

    content_type = response.headers.get("content-type", "application/json")
    if "application/json" in content_type:
        return response.status_code, response.json(), request_id
    return response.status_code, response.text, request_id


async def proxy_stream(
    *,
    route: ModelRoute,
    path: str,
    payload: dict,
    settings: Settings,
    upstream_base_url: str | None = None,
) -> tuple[httpx.Response, AsyncIterator[bytes], str]:
    request_id = str(uuid.uuid4())
    headers = _upstream_headers(route, accept="text/event-stream")
    headers["X-Request-ID"] = request_id
    client = httpx.AsyncClient(timeout=None, verify=settings.upstream_verify_tls)
    target_base_url = upstream_base_url or route.upstream_base_url
    response = await client.send(
        client.build_request(
            "POST",
            f"{target_base_url.rstrip('/')}/{path.lstrip('/')}",
            json=payload,
            headers=headers,
        ),
        stream=True,
    )

    async def iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return response, iterator(), request_id
