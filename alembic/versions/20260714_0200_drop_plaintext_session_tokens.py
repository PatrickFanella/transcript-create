"""drop plaintext session tokens

Revision ID: 20260714_0200
Revises: 20260714_linked_identities
"""

import os

from alembic import op

revision = "20260714_0200"
down_revision = "20260714_linked_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Reconcile sessions before making the hash-only contract irreversible.

    Schedule this maintenance only after draining writers and long-lived session
    transactions: the ALTER/DROP operations require locks that those sessions
    can otherwise hold indefinitely.
    """
    if os.environ.get("ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION", "").strip().lower() != "true":
        raise RuntimeError(
            "20260714_0200 is an irreversible session-token contract migration. "
            "Set ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION=true only after the documented "
            "maintenance cutover has drained all database users."
        )

    op.execute("""
        WITH candidates AS (
            SELECT id,
                   CASE
                        -- In the expand state, an existing hash is the
                        -- runtime credential. A dual-populated row is valid
                        -- only when its plaintext proves that exact hash.
                        WHEN token_hash IS NOT NULL
                             AND token_hash ~ '^[0-9a-f]{64}$'
                             AND token IS NOT NULL
                             AND btrim(token) <> ''
                             AND token_hash = encode(digest(token, 'sha256'), 'hex')
                            THEN token_hash
                        WHEN token_hash IS NOT NULL
                             AND token_hash ~ '^[0-9a-f]{64}$'
                             AND token IS NULL
                            THEN token_hash
                        -- Only a hashless, nonblank plaintext credential can
                        -- be backfilled. Never replace a conflicting hash.
                        WHEN token_hash IS NULL
                             AND token IS NOT NULL
                             AND btrim(token) <> ''
                            THEN encode(digest(token, 'sha256'), 'hex')
                        ELSE NULL
                    END AS resolved_hash
            FROM sessions
        ), conflicting_hashes AS (
            SELECT resolved_hash
            FROM candidates
            WHERE resolved_hash IS NOT NULL
            GROUP BY resolved_hash
            HAVING count(*) > 1
        )
        DELETE FROM sessions AS session_row
        USING candidates
        WHERE session_row.id = candidates.id
          AND (candidates.resolved_hash IS NULL
               OR candidates.resolved_hash IN (SELECT resolved_hash FROM conflicting_hashes));

        -- Invalid credentials and every member of a resulting collision are
        -- gone before updates, so the non-deferrable unique constraint cannot
        -- be transiently violated by a hash swap or a plaintext backfill.
        UPDATE sessions
        SET token_hash = encode(digest(token, 'sha256'), 'hex')
        WHERE token_hash IS NULL
          AND token IS NOT NULL
          AND btrim(token) <> '';

        ALTER TABLE sessions ALTER COLUMN token_hash SET NOT NULL;
        ALTER TABLE sessions DROP COLUMN token;
    """)


def downgrade() -> None:
    """Restore the expand-compatible shape without pretending hashes are cookies."""
    op.execute("""
        ALTER TABLE sessions ADD COLUMN token text;
        DELETE FROM sessions;
        ALTER TABLE sessions ALTER COLUMN token_hash DROP NOT NULL;
        -- DROP COLUMN token removes the expand-state lookup index with it.
        CREATE INDEX sessions_token_idx ON sessions(token);
    """)
