from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine_kwargs: dict = {"future": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_legacy_columns()


def _ensure_legacy_columns() -> None:
    """Add only the small set of non-destructive columns needed by old installs."""
    inspector = inspect(engine)
    alterations = {
        "model_routes": [("capabilities", "JSON NOT NULL DEFAULT '{}'"),],
        "users": [("google_subject", "VARCHAR(255)"),],
        "todos": [("external_metadata", "JSON NOT NULL DEFAULT '{}'"),],
    }
    statements: list[str] = []
    for table_name, columns in alterations.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, definition in columns:
            if column_name not in existing:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
