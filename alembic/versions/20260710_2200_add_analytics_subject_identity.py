"""add pseudonymous analytics identity and daily aggregates

Revision ID: 20260710_analytics_identity
Revises: 20260605_topic_policy
Create Date: 2026-07-10 22:00:00.000000

This is the expand half of the analytics credential-removal rollout.  The
legacy compatibility column is intentionally left in place until every old
application pod has drained and the guarded post-deploy scrub has run.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260710_analytics_identity"
down_revision: Union[str, None] = "20260605_topic_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("analytics_subject_id", sa.CHAR(length=64), nullable=True),
    )
    op.create_check_constraint(
        "events_analytics_subject_format_check",
        "events",
        "analytics_subject_id IS NULL OR analytics_subject_id ~ '^[0-9a-f]{64}$'",
    )
    op.create_table(
        "event_daily_aggregates",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("count >= 0", name="event_daily_aggregates_count_check"),
        sa.PrimaryKeyConstraint("day", "type", name="event_daily_aggregates_pkey"),
    )


def downgrade() -> None:
    op.drop_table("event_daily_aggregates")
    op.drop_constraint(
        "events_analytics_subject_format_check",
        "events",
        type_="check",
    )
    op.drop_column("events", "analytics_subject_id")
