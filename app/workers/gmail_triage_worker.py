"""Scheduled, draft-only Gmail triage for connected users."""

import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import GoogleConnection
from app.services.gmail_triage import triage_user

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_once() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        for connection in db.scalars(select(GoogleConnection)).all():
            try:
                result = triage_user(
                    db,
                    tenant_id=connection.tenant_id,
                    user_id=connection.user_id,
                    settings=settings,
                )
                logger.info("Gmail triage user=%s result=%s", connection.user_id, result)
            except Exception:  # noqa: BLE001
                logger.exception("Gmail triage failed for user=%s", connection.user_id)
    finally:
        db.close()


def main() -> None:
    settings = get_settings()
    if settings.database_auto_create:
        init_db()
    if not settings.gmail_triage_enabled:
        raise SystemExit("Gmail triage disabled; set MODEL_ROUTER_GMAIL_TRIAGE_ENABLED=true to enable")
    while True:
        run_once()
        time.sleep(settings.gmail_triage_interval_seconds)


if __name__ == "__main__":
    main()
