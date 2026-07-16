from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AgentProfile, AgentRun, ModelRoute, OutboxEvent, RunEvent, TodoItem
from app.services.harness import (
    claim_run,
    complete_todo,
    create_profile,
    create_todo,
    create_run,
    finish_run,
    create_memory,
    search_memories,
)


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_profile_persists_runtime_policy():
    with _session() as db:
        profile = create_profile(
            db,
            tenant_id="tenant-1",
            user_id="user-1",
            name="goose-coding",
            runtime="goose",
            model_route="local/strong",
            workspace="/workspace/model-router",
            tools=["filesystem", "memory", "todos"],
            permission_mode="ask",
        )

        stored = db.get(AgentProfile, profile.id)

    assert stored is not None
    assert stored.runtime == "goose"
    assert stored.model_route == "local/strong"
    assert stored.tools_json == ["filesystem", "memory", "todos"]
    assert stored.permission_mode == "ask"


def test_create_run_snapshots_profile_configuration():
    with _session() as db:
        profile = create_profile(
            db,
            tenant_id="tenant-1",
            user_id="user-1",
            name="phone-assistant",
            runtime="hermes",
            model_route="agent/hermes",
            workspace=None,
            tools=["memory", "todos"],
            permission_mode="ask",
        )

        run = create_run(
            db,
            profile=profile,
            user_id="user-1",
            input_text="Summarize my open tasks",
        )

        stored = db.get(AgentRun, run.id)
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all())

    assert stored is not None
    assert stored.status == "queued"
    assert stored.runtime == "hermes"
    assert stored.model_route == "agent/hermes"
    assert stored.input_text == "Summarize my open tasks"
    assert events[0].event_type == "run.queued"


def test_run_claim_and_completion_are_recorded_as_events():
    with _session() as db:
        profile = create_profile(
            db,
            tenant_id="tenant-1",
            user_id=None,
            name="hermes",
            runtime="hermes",
            model_route="hermes-agent",
            workspace=None,
            tools=["memory"],
            permission_mode="ask",
        )
        run = create_run(db, profile=profile, user_id="user-1", input_text="hello")
        db.commit()

        claimed = claim_run(db, run.id)
        finish_run(db, claimed.id, output_text="world")
        db.commit()

        stored = db.get(AgentRun, run.id)
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.sequence)).all())

    assert stored is not None
    assert stored.status == "completed"
    assert stored.output_text == "world"
    assert [event.event_type for event in events] == ["run.queued", "run.started", "run.completed"]


def test_second_worker_cannot_claim_a_run_twice():
    with _session() as db:
        profile = create_profile(
            db,
            tenant_id="tenant-1",
            user_id=None,
            name="direct",
            runtime="direct",
            model_route="local/fast",
            workspace=None,
            tools=[],
            permission_mode="readonly",
        )
        run = create_run(db, profile=profile, user_id="user-1", input_text="hello")
        db.commit()
        claim_run(db, run.id)
        db.commit()

        try:
            claim_run(db, run.id)
        except LookupError:
            claimed_again = False
        else:
            claimed_again = True

    assert claimed_again is False


def test_todo_completion_updates_canonical_state_and_enqueues_sync():
    with _session() as db:
        todo = create_todo(
            db,
            tenant_id="tenant-1",
            user_id="user-1",
            title="Review router architecture",
            due_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            notes="Check the GPU queue design",
        )

        complete_todo(db, todo.id, tenant_id="tenant-1", user_id="user-1")
        stored = db.get(TodoItem, todo.id)
        events = list(db.scalars(select(OutboxEvent)).all())

    assert stored is not None
    assert stored.completed is True
    assert len(events) == 2
    assert events[-1].event_type == "todo.updated"
    assert events[-1].aggregate_id == todo.id


def test_memory_search_is_tenant_scoped():
    with _session() as db:
        create_memory(
            db,
            tenant_id="tenant-1",
            user_id="user-1",
            scope="project",
            subject="router",
            content="The router owns model lifecycle.",
        )
        create_memory(
            db,
            tenant_id="tenant-2",
            user_id="user-2",
            scope="project",
            subject="router",
            content="This must not be visible to tenant one.",
        )

        results = search_memories(db, tenant_id="tenant-1", query="router")

    assert len(results) == 1
    assert results[0].content == "The router owns model lifecycle."


def test_model_route_capabilities_are_persisted():
    with _session() as db:
        route = ModelRoute(
            name="local/strong",
            upstream_base_url="managed://llama-server",
            upstream_model_name="local-strong",
            capabilities_json={"streaming": True, "tools": True, "local": True},
        )
        db.add(route)
        db.commit()
        stored = db.get(ModelRoute, route.id)

    assert stored is not None
    assert stored.capabilities_json["tools"] is True
