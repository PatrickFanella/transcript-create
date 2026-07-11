"""add transactional search index outbox and freshness state

Revision ID: 20260711_search_outbox
Revises: 20260711_scopes_delete
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_search_outbox"
down_revision: Union[str, None] = "20260711_scopes_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_index_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('native','youtube')", name="search_index_outbox_source_check"),
        sa.CheckConstraint("operation IN ('upsert','delete')", name="search_index_outbox_operation_check"),
    )
    op.create_index(
        "search_index_outbox_pending_idx",
        "search_index_outbox",
        ["available_at", "id"],
        postgresql_where=sa.text("processed_at IS NULL AND dead_lettered_at IS NULL"),
    )
    op.create_table(
        "search_index_checkpoints",
        sa.Column("consumer", sa.Text(), primary_key=True),
        sa.Column("last_outbox_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION enqueue_native_search_document() RETURNS trigger AS $$
        DECLARE record_value segments%ROWTYPE; op text;
        BEGIN
            record_value := CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
            op := CASE WHEN TG_OP='DELETE' THEN 'delete' ELSE 'upsert' END;
            INSERT INTO search_index_outbox(source, document_id, video_id, operation, version, payload)
            VALUES (
                'native', record_value.id, record_value.video_id, op,
                (extract(epoch FROM clock_timestamp()) * 1000000)::bigint,
                CASE WHEN op='delete' THEN NULL ELSE jsonb_build_object(
                    'id', record_value.id, 'video_id', record_value.video_id,
                    'start_ms', record_value.start_ms, 'end_ms', record_value.end_ms, 'text', record_value.text
                ) END
            );
            RETURN record_value;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER segments_search_outbox
        AFTER INSERT OR UPDATE OR DELETE ON segments
        FOR EACH ROW EXECUTE FUNCTION enqueue_native_search_document();

        CREATE OR REPLACE FUNCTION enqueue_youtube_search_document() RETURNS trigger AS $$
        DECLARE record_value youtube_segments%ROWTYPE; source_video_id uuid; op text;
        BEGIN
            record_value := CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
            op := CASE WHEN TG_OP='DELETE' THEN 'delete' ELSE 'upsert' END;
            SELECT video_id INTO source_video_id FROM youtube_transcripts WHERE id=record_value.youtube_transcript_id;
            IF source_video_id IS NULL THEN RETURN record_value; END IF;
            INSERT INTO search_index_outbox(source, document_id, video_id, operation, version, payload)
            VALUES (
                'youtube', record_value.id, source_video_id, op,
                (extract(epoch FROM clock_timestamp()) * 1000000)::bigint,
                CASE WHEN op='delete' THEN NULL ELSE jsonb_build_object(
                    'id', record_value.id, 'video_id', source_video_id,
                    'start_ms', record_value.start_ms, 'end_ms', record_value.end_ms, 'text', record_value.text
                ) END
            );
            RETURN record_value;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER youtube_segments_search_outbox
        AFTER INSERT OR UPDATE OR DELETE ON youtube_segments
        FOR EACH ROW EXECUTE FUNCTION enqueue_youtube_search_document();

        CREATE OR REPLACE FUNCTION enqueue_youtube_transcript_deletions() RETURNS trigger AS $$
        BEGIN
            INSERT INTO search_index_outbox(source, document_id, video_id, operation, version, payload)
            SELECT 'youtube', ys.id, OLD.video_id, 'delete',
                   (extract(epoch FROM clock_timestamp()) * 1000000)::bigint + ys.id, NULL
            FROM youtube_segments ys WHERE ys.youtube_transcript_id=OLD.id;
            RETURN OLD;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER youtube_transcripts_search_outbox
        BEFORE DELETE ON youtube_transcripts
        FOR EACH ROW EXECUTE FUNCTION enqueue_youtube_transcript_deletions();
    """)

    # Existing rows become ordinary upserts so the same consumer performs the backfill.
    op.execute("""
        INSERT INTO search_index_outbox(source, document_id, video_id, operation, version, payload)
        SELECT 'native', id, video_id, 'upsert',
               (extract(epoch FROM clock_timestamp()) * 1000000)::bigint + id,
               jsonb_build_object('id', id, 'video_id', video_id, 'start_ms', start_ms, 'end_ms', end_ms, 'text', text)
        FROM segments
    """)
    op.execute("""
        INSERT INTO search_index_outbox(source, document_id, video_id, operation, version, payload)
        SELECT 'youtube', ys.id, yt.video_id, 'upsert',
               (extract(epoch FROM clock_timestamp()) * 1000000)::bigint + ys.id,
               jsonb_build_object('id', ys.id, 'video_id', yt.video_id, 'start_ms', ys.start_ms, 'end_ms', ys.end_ms, 'text', ys.text)
        FROM youtube_segments ys JOIN youtube_transcripts yt ON yt.id=ys.youtube_transcript_id
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS youtube_transcripts_search_outbox ON youtube_transcripts")
    op.execute("DROP FUNCTION IF EXISTS enqueue_youtube_transcript_deletions()")
    op.execute("DROP TRIGGER IF EXISTS youtube_segments_search_outbox ON youtube_segments")
    op.execute("DROP FUNCTION IF EXISTS enqueue_youtube_search_document()")
    op.execute("DROP TRIGGER IF EXISTS segments_search_outbox ON segments")
    op.execute("DROP FUNCTION IF EXISTS enqueue_native_search_document()")
    op.drop_table("search_index_checkpoints")
    op.drop_table("search_index_outbox")
