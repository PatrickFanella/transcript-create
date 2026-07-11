"""enforce api key scopes and record source deletions

Revision ID: 20260711_scopes_delete
Revises: 20260711_job_lifecycle
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_scopes_delete"
down_revision: Union[str, None] = "20260711_job_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_SCOPES = "exports:read,jobs:read,jobs:write,search:read,videos:read"


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE api_keys SET scopes=:scopes WHERE scopes IS NULL OR btrim(scopes)='' ").bindparams(
            scopes=DEFAULT_SCOPES
        )
    )
    op.alter_column("api_keys", "scopes", existing_type=sa.Text(), nullable=False, server_default=DEFAULT_SCOPES)
    op.create_table(
        "source_deletions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("youtube_id", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("backup_exclusion_until", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id"),
    )


def downgrade() -> None:
    op.drop_table("source_deletions")
    op.alter_column("api_keys", "scopes", existing_type=sa.Text(), nullable=True, server_default=None)
