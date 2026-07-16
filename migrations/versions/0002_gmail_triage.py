"""Add draft-only Gmail triage records."""

from alembic import op
import sqlalchemy as sa


revision = "0002_gmail_triage"
down_revision = "0001_platform_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_triage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=1000), nullable=True),
        sa.Column("sender", sa.String(length=500), nullable=True),
        sa.Column("is_important", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("context_matches", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("context_memory_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="inspected", nullable=False),
        sa.Column("importance_reason", sa.Text(), nullable=True),
        sa.Column("draft_body", sa.Text(), nullable=True),
        sa.Column("gmail_draft_id", sa.String(length=255), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "gmail_message_id", name="uq_gmail_triage_message"),
    )
    op.create_index("ix_gmail_triage_tenant_id", "gmail_triage", ["tenant_id"])
    op.create_index("ix_gmail_triage_user_id", "gmail_triage", ["user_id"])
    op.create_index("ix_gmail_triage_status", "gmail_triage", ["status"])
    op.create_index("ix_gmail_triage_gmail_draft_id", "gmail_triage", ["gmail_draft_id"])


def downgrade() -> None:
    op.drop_index("ix_gmail_triage_gmail_draft_id", table_name="gmail_triage")
    op.drop_index("ix_gmail_triage_status", table_name="gmail_triage")
    op.drop_index("ix_gmail_triage_user_id", table_name="gmail_triage")
    op.drop_index("ix_gmail_triage_tenant_id", table_name="gmail_triage")
    op.drop_table("gmail_triage")
