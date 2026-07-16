import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import GoogleConnection
from app.services.google_sync import sync_google_tasks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_once() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        connections = db.scalars(select(GoogleConnection)).all()
        for connection in connections:
            try:
                result = sync_google_tasks(
                    db,
                    tenant_id=connection.tenant_id,
                    user_id=connection.user_id,
                    settings=settings,
                )
                logger.info("Google Tasks sync user=%s result=%s", connection.user_id, result)
            except Exception:  # noqa: BLE001
                logger.exception("Google Tasks sync failed for user=%s", connection.user_id)
    finally:
        db.close()


def main() -> None:
    settings = get_settings()
    if settings.database_auto_create:
        init_db()
    interval = settings.google_tasks_sync_interval_seconds
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":
    main()
