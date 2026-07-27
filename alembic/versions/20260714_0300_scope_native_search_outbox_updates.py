"""scope native search outbox updates to indexed columns

Revision ID: 20260714_0300
Revises: 20260714_0200
"""

from alembic import op

revision = "20260714_0300"
down_revision = "20260714_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Do not enqueue a search update for diarization-only label writes."""
    op.execute("DROP TRIGGER IF EXISTS segments_search_outbox ON segments")
    op.execute("""
        CREATE TRIGGER segments_search_outbox
        AFTER INSERT OR DELETE OR UPDATE OF video_id, start_ms, end_ms, text ON segments
        FOR EACH ROW EXECUTE FUNCTION enqueue_native_search_document();
    """)


def downgrade() -> None:
    """Restore the original broad native segment trigger."""
    op.execute("DROP TRIGGER IF EXISTS segments_search_outbox ON segments")
    op.execute("""
        CREATE TRIGGER segments_search_outbox
        AFTER INSERT OR UPDATE OR DELETE ON segments
        FOR EACH ROW EXECUTE FUNCTION enqueue_native_search_document();
    """)
