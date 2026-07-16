from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AuthContext, require_scope
from app.models import AgentProfile, AgentRun, MemoryItem, RunEvent, TodoItem
from app.schemas import (
    AgentProfileRequest,
    AgentProfileResponse,
    AgentRunRequest,
    AgentRunCompletionRequest,
    AgentRunResponse,
    MemoryRequest,
    MemoryResponse,
    TodoRequest,
    TodoResponse,
    TodoUpdateRequest,
)
from app.services.google_sync import sync_google_tasks
from app.services.google_oauth import finish_google_oauth, start_google_oauth
from app.services.harness import claim_run, complete_todo, create_memory, create_profile, create_run, create_todo, finish_run, search_memories, update_todo
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["harness"])


def _user_id(context: AuthContext) -> str | None:
    return context.user.id if context.user else None


@router.get("/integrations/google/start")
def google_start(
    context: AuthContext = Depends(require_scope("integrations:google")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if context.user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google OAuth requires a user session")
    return RedirectResponse(start_google_oauth(db, tenant_id=context.tenant.id, user_id=context.user.id, settings=settings))


@router.get("/integrations/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    error = request.query_params.get("error")
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Google OAuth denied: {error}")
    code = request.query_params.get("code")
    raw_state = request.query_params.get("state")
    if not code or not raw_state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Google OAuth callback parameters")
    connection = finish_google_oauth(db, code=code, raw_state=raw_state, settings=settings)
    return {"status": "connected", "email": connection.email or ""}


def _profile_visibility(context: AuthContext):
    user_id = _user_id(context)
    return (
        AgentProfile.tenant_id == context.tenant.id,
        or_(AgentProfile.user_id.is_(None), AgentProfile.user_id == user_id) if user_id else AgentProfile.user_id.is_(None),
    )


@router.get("/profiles", response_model=list[AgentProfileResponse])
def list_profiles(
    context: AuthContext = Depends(require_scope("agent:read")),
    db: Session = Depends(get_db),
) -> list[AgentProfile]:
    return list(db.scalars(select(AgentProfile).where(*_profile_visibility(context)).order_by(AgentProfile.name)).all())


@router.post("/profiles", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
def add_profile(
    payload: AgentProfileRequest,
    context: AuthContext = Depends(require_scope("agent:write")),
    db: Session = Depends(get_db),
) -> AgentProfile:
    profile = create_profile(
        db,
        tenant_id=context.tenant.id,
        user_id=_user_id(context),
        name=payload.name,
        runtime=payload.runtime,
        model_route=payload.model_route,
        fallback_route=payload.fallback_route,
        workspace=payload.workspace,
        tools=payload.tools,
        permission_mode=payload.permission_mode,
        memory_scope=payload.memory_scope,
    )
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/runs", response_model=list[AgentRunResponse])
def list_runs(
    context: AuthContext = Depends(require_scope("agent:read")),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[AgentRun]:
    statement = select(AgentRun).where(AgentRun.tenant_id == context.tenant.id)
    if context.user:
        statement = statement.where(AgentRun.user_id == context.user.id)
    return list(db.scalars(statement.order_by(AgentRun.created_at.desc()).limit(limit)).all())


@router.post("/runs", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
def add_run(
    payload: AgentRunRequest,
    context: AuthContext = Depends(require_scope("agent:write")),
    db: Session = Depends(get_db),
) -> AgentRun:
    profile = db.scalar(select(AgentProfile).where(AgentProfile.id == payload.profile_id, *_profile_visibility(context)))
    if profile is None or not profile.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent profile not found")
    run = create_run(db, profile=profile, user_id=_user_id(context), input_text=payload.input_text)
    db.commit()
    db.refresh(run)
    return run


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_run(
    run_id: str,
    context: AuthContext = Depends(require_scope("agent:read")),
    db: Session = Depends(get_db),
) -> AgentRun:
    statement = select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == context.tenant.id)
    if context.user:
        statement = statement.where(AgentRun.user_id == context.user.id)
    run = db.scalar(statement)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/runs/{run_id}/events")
def get_run_events(
    run_id: str,
    context: AuthContext = Depends(require_scope("agent:read")),
    db: Session = Depends(get_db),
) -> list[dict]:
    run = get_run(run_id, context, db)
    events = db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.sequence)).all()
    return [
        {
            "id": event.id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "payload": event.payload_json,
            "created_at": event.created_at,
        }
        for event in events
    ]


@router.post("/runs/{run_id}/claim", response_model=AgentRunResponse)
def claim_agent_run(
    run_id: str,
    context: AuthContext = Depends(require_scope("agent:write")),
    db: Session = Depends(get_db),
) -> AgentRun:
    run = get_run(run_id, context, db)
    try:
        run = claim_run(db, run.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run is not queued") from exc
    db.commit()
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/complete", response_model=AgentRunResponse)
def complete_agent_run(
    run_id: str,
    payload: AgentRunCompletionRequest,
    context: AuthContext = Depends(require_scope("agent:write")),
    db: Session = Depends(get_db),
) -> AgentRun:
    run = get_run(run_id, context, db)
    try:
        run = finish_run(db, run.id, output_text=payload.output_text, error_message=payload.error_message)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    db.commit()
    db.refresh(run)
    return run


@router.get("/memories", response_model=list[MemoryResponse])
def list_memories(
    context: AuthContext = Depends(require_scope("memory:read")),
    db: Session = Depends(get_db),
    query: str = Query(default="", max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[MemoryItem]:
    if query:
        return search_memories(db, tenant_id=context.tenant.id, user_id=_user_id(context), query=query, limit=limit)
    statement = select(MemoryItem).where(MemoryItem.tenant_id == context.tenant.id)
    if context.user:
        statement = statement.where(or_(MemoryItem.user_id.is_(None), MemoryItem.user_id == context.user.id))
    return list(db.scalars(statement.order_by(MemoryItem.updated_at.desc()).limit(limit)).all())


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
def add_memory(
    payload: MemoryRequest,
    context: AuthContext = Depends(require_scope("memory:write")),
    db: Session = Depends(get_db),
) -> MemoryItem:
    memory = create_memory(
        db,
        tenant_id=context.tenant.id,
        user_id=_user_id(context),
        scope=payload.scope,
        subject=payload.subject,
        content=payload.content,
        source=payload.source,
        importance=payload.importance,
    )
    db.commit()
    db.refresh(memory)
    return memory


@router.get("/todos", response_model=list[TodoResponse])
def list_todos(
    context: AuthContext = Depends(require_scope("todos:read")),
    db: Session = Depends(get_db),
    include_completed: bool = False,
) -> list[TodoItem]:
    statement = select(TodoItem).where(TodoItem.tenant_id == context.tenant.id)
    if context.user:
        statement = statement.where(or_(TodoItem.user_id.is_(None), TodoItem.user_id == context.user.id))
    if not include_completed:
        statement = statement.where(TodoItem.completed.is_(False))
    return list(db.scalars(statement.order_by(TodoItem.due_at, TodoItem.created_at)).all())


@router.post("/todos", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
def add_todo(
    payload: TodoRequest,
    context: AuthContext = Depends(require_scope("todos:write")),
    db: Session = Depends(get_db),
) -> TodoItem:
    todo = create_todo(
        db,
        tenant_id=context.tenant.id,
        user_id=_user_id(context),
        title=payload.title,
        due_at=payload.due_at,
        notes=payload.notes,
    )
    db.commit()
    db.refresh(todo)
    return todo


@router.post("/todos/{todo_id}/complete", response_model=TodoResponse)
def mark_todo_complete(
    todo_id: str,
    context: AuthContext = Depends(require_scope("todos:write")),
    db: Session = Depends(get_db),
) -> TodoItem:
    try:
        todo = complete_todo(db, todo_id, tenant_id=context.tenant.id, user_id=_user_id(context))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TODO not found") from exc
    db.commit()
    db.refresh(todo)
    return todo


@router.patch("/todos/{todo_id}", response_model=TodoResponse)
def edit_todo(
    todo_id: str,
    payload: TodoUpdateRequest,
    context: AuthContext = Depends(require_scope("todos:write")),
    db: Session = Depends(get_db),
) -> TodoItem:
    try:
        todo = update_todo(
            db,
            todo_id,
            tenant_id=context.tenant.id,
            user_id=_user_id(context),
            title=payload.title,
            completed=payload.completed,
            due_at=payload.due_at,
            notes=payload.notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TODO not found") from exc
    db.commit()
    db.refresh(todo)
    return todo


@router.post("/integrations/google/tasks/sync")
def sync_tasks(
    context: AuthContext = Depends(require_scope("integrations:google")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    if context.user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google Tasks sync requires a user session")
    return sync_google_tasks(db, tenant_id=context.tenant.id, user_id=context.user.id, settings=settings)
