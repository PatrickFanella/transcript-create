"""add append-only citation-backed opinion history

Revision ID: 20260712_opinions
Revises: 20260711_search_outbox
"""

from alembic import op

revision = "20260712_opinions"
down_revision = "20260711_search_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE archive_opinions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_slug text NOT NULL,
            normalized_claim text NOT NULL,
            status text NOT NULL DEFAULT 'candidate'
                CHECK (status IN ('candidate','published','corrected','retracted')),
            current_revision integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (subject_slug, normalized_claim)
        );
        CREATE TABLE archive_opinion_revisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            opinion_id uuid NOT NULL REFERENCES archive_opinions(id) ON DELETE CASCADE,
            revision integer NOT NULL,
            stance text NOT NULL,
            summary text NOT NULL,
            confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            model_version text NOT NULL,
            prompt_version text NOT NULL,
            time_bucket text NOT NULL,
            evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
            model_generated boolean NOT NULL DEFAULT true,
            status text NOT NULL CHECK (status IN ('candidate','published','corrected','retracted')),
            correction_reason text,
            corrected_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (opinion_id, revision)
        );
        CREATE INDEX ix_archive_opinions_subject_status
            ON archive_opinions(subject_slug, status, updated_at DESC);
        CREATE INDEX ix_archive_opinion_revisions_opinion
            ON archive_opinion_revisions(opinion_id, revision DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS archive_opinion_revisions; DROP TABLE IF EXISTS archive_opinions;")
