"""Create the model-router and agent-platform schema.

This baseline intentionally delegates table creation to the SQLAlchemy model
metadata so a new Postgres deployment and the existing SQLite development
deployment share one schema definition. Future changes should use explicit
Alembic revisions.
"""

from alembic import op

from app.database import Base
from app import models  # noqa: F401

revision = "0001_platform_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
