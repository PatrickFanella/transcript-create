"""add durable job ownership, progress, leases, attempts, and idempotency

Revision ID: 20260711_job_lifecycle
Revises: 20260710_analytics_idx
Create Date: 2026-07-11 01:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260711_job_lifecycle"
down_revision: Union[str, None] = "20260710_analytics_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL enum additions must be committed before the value can be used.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE job_state ADD VALUE IF NOT EXISTS 'needs_attention'")

    op.add_column("jobs", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.add_column("jobs", sa.Column("canonical_source", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("stage", sa.Text(), nullable=False, server_default="queued"))
    op.add_column("jobs", sa.Column("completed_units", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("total_units", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("last_failure_summary", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("""
        UPDATE jobs
        SET owner_user_id = CASE
                WHEN meta->>'owner_user_id' ~ '^[0-9a-fA-F-]{36}$'
                THEN (meta->>'owner_user_id')::uuid
                ELSE NULL
            END,
            canonical_source = NULLIF(meta->>'normalized_url', ''),
            idempotency_key = NULLIF(meta->>'idempotency_key', '')
        """)
    op.create_index("jobs_owner_created_idx", "jobs", ["owner_user_id", "created_at"])
    op.create_index("jobs_next_attempt_idx", "jobs", ["next_attempt_at"])
    op.create_index(
        "jobs_active_canonical_unique_idx",
        "jobs",
        ["owner_user_id", "kind", "canonical_source"],
        unique=True,
        postgresql_where=sa.text(
            "owner_user_id IS NOT NULL AND canonical_source IS NOT NULL "
            "AND state NOT IN ('failed', 'completed', 'needs_attention')"
        ),
    )
    op.create_index(
        "jobs_owner_idempotency_unique_idx",
        "jobs",
        ["owner_user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL AND idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "attempt_number", name="job_attempts_job_attempt_number_key"),
    )
    op.create_index("job_attempts_job_started_idx", "job_attempts", ["job_id", "started_at"])
    op.create_index("job_attempts_lease_idx", "job_attempts", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_table("job_attempts")
    op.drop_index("jobs_owner_idempotency_unique_idx", table_name="jobs")
    op.drop_index("jobs_active_canonical_unique_idx", table_name="jobs")
    op.drop_index("jobs_owner_created_idx", table_name="jobs")
    op.drop_index("jobs_next_attempt_idx", table_name="jobs")
    for column in (
        "quarantined_at",
        "last_failure_summary",
        "next_attempt_at",
        "attempt_count",
        "cancelled_at",
        "cancellation_requested_at",
        "heartbeat_at",
        "total_units",
        "completed_units",
        "stage",
        "idempotency_key",
        "canonical_source",
        "owner_user_id",
    ):
        op.drop_column("jobs", column)
    # PostgreSQL enum values are intentionally retained for rollback safety.
