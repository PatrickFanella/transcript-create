"""add analytics subject lookup index without blocking event writes

Revision ID: 20260710_analytics_idx
Revises: 20260710_analytics_identity
Create Date: 2026-07-10 22:10:00.000000

Index creation and removal run outside Alembic's migration transaction because
PostgreSQL requires ``CONCURRENTLY`` to execute outside a transaction block.
Keeping this operation in its own revision also prevents the autocommit boundary
from partially committing the additive table/column migration.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260710_analytics_idx"
down_revision: Union[str, None] = "20260710_analytics_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # An interrupted concurrent build can leave an invalid same-named
        # index. IF NOT EXISTS would accept that remnant and let Alembic stamp
        # the revision, so always remove it before the retry-safe rebuild.
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS events_analytics_subject_idx"))
        op.execute(
            sa.text("CREATE INDEX CONCURRENTLY events_analytics_subject_idx " "ON events (analytics_subject_id)")
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS events_analytics_subject_idx"))
