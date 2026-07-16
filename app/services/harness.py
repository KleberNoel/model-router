from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import AgentProfile, AgentRun, MemoryItem, OutboxEvent, RunEvent, TodoItem


def _enqueue(
    db: Session,
    *,
    tenant_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
) -> OutboxEvent:
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload_json=payload,
    )
    db.add(event)
    db.flush()
    return event


def create_profile(
    db: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    name: str,
    runtime: str,
    model_route: str,
    workspace: str | None,
    tools: list[str],
    permission_mode: str,
    fallback_route: str | None = None,
    memory_scope: str = "project",
) -> AgentProfile:
    profile = AgentProfile(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name,
        runtime=runtime,
        model_route=model_route,
        fallback_route=fallback_route,
        workspace=workspace,
        tools_json=tools,
        permission_mode=permission_mode,
        memory_scope=memory_scope,
    )
    db.add(profile)
    db.flush()
    return profile


def create_run(db: Session, *, profile: AgentProfile, user_id: str | None, input_text: str) -> AgentRun:
    run = AgentRun(
        tenant_id=profile.tenant_id,
        user_id=user_id,
        profile_id=profile.id,
        runtime=profile.runtime,
        model_route=profile.model_route,
        input_text=input_text,
    )
    db.add(run)
    db.flush()
    db.add(
        RunEvent(
            run_id=run.id,
            sequence=1,
            event_type="run.queued",
            payload_json={"profile_id": profile.id, "runtime": profile.runtime},
        )
    )
    return run


def _next_sequence(db: Session, run_id: str) -> int:
    latest = db.scalar(select(RunEvent.sequence).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence.desc()).limit(1))
    return (latest or 0) + 1


def claim_run(db: Session, run_id: str) -> AgentRun:
    result = db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "queued")
        .values(status="running", started_at=datetime.utcnow())
    )
    if result.rowcount != 1:
        raise LookupError("Queued run not found")
    run = db.get(AgentRun, run_id)
    if run is None:
        raise LookupError("Run not found")
    db.add(
        RunEvent(
            run_id=run.id,
            sequence=_next_sequence(db, run.id),
            event_type="run.started",
            payload_json={"runtime": run.runtime, "model_route": run.model_route},
        )
    )
    db.flush()
    return run


def finish_run(db: Session, run_id: str, *, output_text: str | None = None, error_message: str | None = None) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise LookupError("Run not found")
    if run.status in {"completed", "failed"}:
        return run
    run.status = "failed" if error_message else "completed"
    run.output_text = output_text
    run.error_message = error_message
    run.finished_at = datetime.utcnow()
    event_type = "run.failed" if error_message else "run.completed"
    payload = {"error": error_message} if error_message else {"output_length": len(output_text or "")}
    db.add(run)
    db.add(
        RunEvent(
            run_id=run.id,
            sequence=_next_sequence(db, run.id),
            event_type=event_type,
            payload_json=payload,
        )
    )
    db.flush()
    return run


def create_todo(
    db: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    title: str,
    due_at: datetime | None = None,
    notes: str | None = None,
) -> TodoItem:
    todo = TodoItem(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        due_at=due_at,
        notes=notes,
    )
    db.add(todo)
    db.flush()
    _enqueue(
        db,
        tenant_id=tenant_id,
        event_type="todo.created",
        aggregate_type="todo",
        aggregate_id=todo.id,
        payload=_todo_payload(todo),
    )
    return todo


def complete_todo(db: Session, todo_id: str, *, tenant_id: str, user_id: str | None) -> TodoItem:
    todo = db.scalar(
        select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.tenant_id == tenant_id,
            or_(TodoItem.user_id.is_(None), TodoItem.user_id == user_id) if user_id else True,
        )
    )
    if todo is None:
        raise LookupError("TODO not found")
    todo.completed = True
    db.add(todo)
    db.flush()
    _enqueue(
        db,
        tenant_id=tenant_id,
        event_type="todo.updated",
        aggregate_type="todo",
        aggregate_id=todo.id,
        payload=_todo_payload(todo),
    )
    return todo


def update_todo(
    db: Session,
    todo_id: str,
    *,
    tenant_id: str,
    user_id: str | None,
    title: str | None = None,
    completed: bool | None = None,
    due_at: datetime | None = None,
    notes: str | None = None,
) -> TodoItem:
    todo = db.scalar(
        select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.tenant_id == tenant_id,
            or_(TodoItem.user_id.is_(None), TodoItem.user_id == user_id) if user_id else True,
        )
    )
    if todo is None:
        raise LookupError("TODO not found")
    if title is not None:
        todo.title = title
    if completed is not None:
        todo.completed = completed
    if due_at is not None:
        todo.due_at = due_at
    if notes is not None:
        todo.notes = notes
    db.add(todo)
    db.flush()
    _enqueue(
        db,
        tenant_id=tenant_id,
        event_type="todo.updated",
        aggregate_type="todo",
        aggregate_id=todo.id,
        payload=_todo_payload(todo),
    )
    return todo


def create_memory(
    db: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    scope: str,
    subject: str,
    content: str,
    source: str = "agent",
    importance: int = 50,
) -> MemoryItem:
    memory = MemoryItem(
        tenant_id=tenant_id,
        user_id=user_id,
        scope=scope,
        subject=subject,
        content=content,
        source=source,
        importance=importance,
    )
    db.add(memory)
    db.flush()
    return memory


def search_memories(
    db: Session,
    *,
    tenant_id: str,
    query: str,
    user_id: str | None = None,
    limit: int = 20,
) -> list[MemoryItem]:
    pattern = f"%{query.strip()}%"
    owner_filter = True if user_id is None else (MemoryItem.user_id.is_(None) | (MemoryItem.user_id == user_id))
    statement = (
        select(MemoryItem)
        .where(
            MemoryItem.tenant_id == tenant_id,
            owner_filter,
            (MemoryItem.subject.ilike(pattern) | MemoryItem.content.ilike(pattern)),
        )
        .order_by(MemoryItem.importance.desc(), MemoryItem.updated_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return list(db.scalars(statement).all())


def _todo_payload(todo: TodoItem) -> dict:
    return {
        "id": todo.id,
        "title": todo.title,
        "completed": todo.completed,
        "due_at": todo.due_at.isoformat() if todo.due_at else None,
        "notes": todo.notes,
        "external_provider": todo.external_provider,
        "external_id": todo.external_id,
    }
