from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.deps import AuthContext, get_auth_context
from app.services.audit import elapsed_ms, log_usage, started_timer
from app.services.model_manager import get_llama_server_manager
from app.services.proxy import (
    extract_usage,
    list_accessible_models,
    openai_model_list,
    proxy_json,
    proxy_stream,
    resolve_model_route,
)

router = APIRouter(tags=["openai"])


@router.get("/v1/models")
def models(context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> dict:
    routes = list_accessible_models(db, context.tenant.id)
    return openai_model_list(routes)


async def _proxy_request(
    *,
    path: str,
    request: Request,
    context: AuthContext,
    db: Session,
    settings: Settings,
) -> Response:
    payload = await request.json()
    requested_model = payload.get("model")
    if not requested_model:
        raise HTTPException(status_code=400, detail="Missing model")

    route = resolve_model_route(db, context.tenant.id, requested_model)
    started_at = started_timer()
    lease = await get_llama_server_manager().acquire(route)
    payload["model"] = route.upstream_model_name
    is_stream = bool(payload.get("stream"))

    if is_stream:
        try:
            upstream_response, iterator, request_id = await proxy_stream(
                route=route,
                path=path,
                payload=payload,
                settings=settings,
                upstream_base_url=lease.upstream_base_url,
            )
        except Exception:
            await lease.release()
            raise
        if upstream_response.status_code >= 400:
            error_body = await upstream_response.aread()
            await lease.release()
            log_usage(
                db,
                actor=context,
                model_route_id=route.id,
                request_id=request_id,
                path=f"/v1/{path}",
                stream=True,
                status_code=upstream_response.status_code,
                latency_ms=elapsed_ms(started_at),
                error_message=error_body.decode("utf-8", errors="replace"),
            )
            return Response(
                content=error_body,
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get("content-type", "application/json"),
            )

        async def wrapped_stream():
            try:
                async for chunk in iterator:
                    yield chunk
            finally:
                await lease.release()
                log_usage(
                    db,
                    actor=context,
                    model_route_id=route.id,
                    request_id=request_id,
                    path=f"/v1/{path}",
                    stream=True,
                    status_code=upstream_response.status_code,
                    latency_ms=elapsed_ms(started_at),
                )

        return StreamingResponse(
            wrapped_stream(),
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "text/event-stream"),
        )

    try:
        status_code, body, request_id = await proxy_json(
            route=route,
            path=path,
            payload=payload,
            settings=settings,
            upstream_base_url=lease.upstream_base_url,
        )
    finally:
        await lease.release()
    prompt_tokens = completion_tokens = total_tokens = None
    error_message = None
    if isinstance(body, dict):
        prompt_tokens, completion_tokens, total_tokens = extract_usage(body)
    else:
        error_message = body if status_code >= 400 else None

    log_usage(
        db,
        actor=context,
        model_route_id=route.id,
        request_id=request_id,
        path=f"/v1/{path}",
        stream=False,
        status_code=status_code,
        latency_ms=elapsed_ms(started_at),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        error_message=error_message,
    )
    if isinstance(body, dict):
        return JSONResponse(status_code=status_code, content=body)
    return Response(status_code=status_code, content=body)


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    return await _proxy_request(
        path="chat/completions",
        request=request,
        context=context,
        db=db,
        settings=settings,
    )


@router.post("/v1/completions")
async def completions(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    return await _proxy_request(
        path="completions",
        request=request,
        context=context,
        db=db,
        settings=settings,
    )
