from time import perf_counter

from sqlalchemy.orm import Session

from app.deps import AuthContext
from app.models import AuditLog, UsageLog


def log_audit(
    db: Session,
    *,
    actor: AuthContext | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            tenant_id=actor.tenant.id if actor else None,
            actor_user_id=actor.user.id if actor and actor.user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details_json=details or {},
        )
    )
    db.commit()


def log_usage(
    db: Session,
    *,
    actor: AuthContext,
    model_route_id: str | None,
    request_id: str,
    path: str,
    stream: bool,
    status_code: int,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    error_message: str | None = None,
) -> None:
    db.add(
        UsageLog(
            tenant_id=actor.tenant.id,
            user_id=actor.user.id if actor.user else None,
            api_key_id=actor.api_key.id if actor.api_key else None,
            model_route_id=model_route_id,
            request_id=request_id,
            path=path,
            stream=stream,
            status_code=status_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            error_message=error_message,
        )
    )
    db.commit()


def started_timer() -> float:
    return perf_counter()


def elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)
