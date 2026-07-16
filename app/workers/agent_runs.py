"""Execute queued Hermes/direct runs through model-router.

Goose runs are intentionally excluded because Goose requires a local ACP
process, local workspace, and local approval boundary.
"""

import asyncio
import logging
import os
import time

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AgentRun
from app.services.agent_runner import completion_payload, completion_text
from app.services.harness import claim_run, finish_run

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def execute_one(run: AgentRun, *, router_url: str, api_key: str) -> None:
    db = SessionLocal()
    try:
        try:
            claimed = claim_run(db, run.id)
            db.commit()
        except LookupError:
            db.rollback()
            return

        try:
            async with httpx.AsyncClient(timeout=600) as client:
                response = await client.post(
                    f"{router_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=completion_payload(claimed),
                )
                response.raise_for_status()
                output = completion_text(response.json())
            finish_run(db, claimed.id, output_text=output)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent run failed id=%s", claimed.id)
            finish_run(db, claimed.id, error_message=str(exc))
        db.commit()
    finally:
        db.close()


async def run_once() -> int:
    api_key = os.getenv("MODEL_ROUTER_RUNNER_API_KEY", "")
    if not api_key:
        raise RuntimeError("MODEL_ROUTER_RUNNER_API_KEY is required")
    router_url = os.getenv("MODEL_ROUTER_URL", "http://model-router:4000")
    db = SessionLocal()
    try:
        runs = db.scalars(
            select(AgentRun)
            .where(AgentRun.status == "queued", AgentRun.runtime.in_(["hermes", "direct"]))
            .order_by(AgentRun.created_at)
            .limit(10)
        ).all()
        run_ids = [run.id for run in runs]
    finally:
        db.close()
    await asyncio.gather(*(execute_one(run, router_url=router_url, api_key=api_key) for run in runs))
    return len(run_ids)


def main() -> None:
    interval = int(os.getenv("MODEL_ROUTER_RUNNER_INTERVAL_SECONDS", "2"))
    while True:
        asyncio.run(run_once())
        time.sleep(interval)


if __name__ == "__main__":
    main()
