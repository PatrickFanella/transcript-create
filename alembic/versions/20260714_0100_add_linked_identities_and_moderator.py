"""add linked identities, moderator role, and hashed session metadata

Revision ID: 20260714_linked_identities
Revises: 20260712_opinions
"""

from alembic import op


revision = "20260714_linked_identities"
down_revision = "20260712_opinions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_identities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider text NOT NULL CHECK (provider IN ('google', 'twitch')),
            subject text NOT NULL,
            provider_email text,
            provider_email_verified boolean,
            provider_name text,
            provider_avatar_url text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz,
            UNIQUE (provider, subject),
            UNIQUE (user_id, provider)
        );
        CREATE INDEX user_identities_user_id_idx ON user_identities(user_id);

        INSERT INTO user_identities (
            user_id, provider, subject, provider_email, provider_name,
            provider_avatar_url, created_at, updated_at, last_login_at
        )
        SELECT id, oauth_provider, oauth_subject, email, name, avatar_url,
               created_at, updated_at, updated_at
        FROM users
        WHERE oauth_provider IN ('google', 'twitch')
          AND oauth_subject IS NOT NULL
        ON CONFLICT (provider, subject) DO NOTHING;

        ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
        UPDATE users SET role = 'user' WHERE role NOT IN ('user', 'moderator', 'admin');
        ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('user', 'moderator', 'admin'));

        ALTER TABLE sessions ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();
        ALTER TABLE sessions ADD COLUMN token_hash char(64);
        -- A duplicate legacy bearer token is ambiguous. Revoke every copy
        -- rather than arbitrarily retaining one before enforcing uniqueness.
        DELETE FROM sessions
        WHERE token IS NOT NULL
          AND token IN (
              SELECT token
              FROM sessions
              WHERE token IS NOT NULL
              GROUP BY token
              HAVING count(*) > 1
          );
        UPDATE sessions SET token_hash = encode(digest(token, 'sha256'), 'hex');
        ALTER TABLE sessions ADD CONSTRAINT sessions_token_hash_key UNIQUE (token_hash);
        ALTER TABLE sessions ALTER COLUMN token DROP NOT NULL;

        -- Normalize orphan UUIDs before adding the ownership foreign keys.
        ALTER TABLE source_deletions ALTER COLUMN deleted_by_user_id DROP NOT NULL;
        -- Cleanup inputs and state must survive deletion of the video row.
        ALTER TABLE source_deletions ADD COLUMN raw_path text;
        ALTER TABLE source_deletions ADD COLUMN wav_path text;
        ALTER TABLE source_deletions ADD COLUMN cleanup_status text NOT NULL DEFAULT 'pending'
            CHECK (cleanup_status IN ('pending', 'completed'));
        ALTER TABLE source_deletions ADD COLUMN cleanup_attempts integer NOT NULL DEFAULT 0;
        ALTER TABLE source_deletions ADD COLUMN cleanup_error text;
        ALTER TABLE source_deletions ADD COLUMN cleanup_started_at timestamptz;
        ALTER TABLE source_deletions ADD COLUMN cleanup_completed_at timestamptz;
        ALTER TABLE source_deletions ADD COLUMN cleanup_lease_until timestamptz;
        ALTER TABLE source_deletions ADD COLUMN cleanup_next_attempt_at timestamptz;
        ALTER TABLE source_deletions ADD COLUMN cleanup_lease_token uuid;
        CREATE INDEX source_deletions_cleanup_pending_idx
            ON source_deletions (cleanup_status, cleanup_next_attempt_at, cleanup_lease_until)
            WHERE cleanup_status = 'pending';
        UPDATE jobs SET owner_user_id = NULL
        WHERE owner_user_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = jobs.owner_user_id);
        UPDATE source_deletions SET owner_user_id = NULL
        WHERE owner_user_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = source_deletions.owner_user_id);
        UPDATE source_deletions SET deleted_by_user_id = NULL
        WHERE deleted_by_user_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = source_deletions.deleted_by_user_id);
        -- The FK column is authoritative; metadata is only a compatibility mirror.
        UPDATE jobs
        SET meta = CASE WHEN owner_user_id IS NULL
                        THEN COALESCE(meta, '{}'::jsonb) - 'owner_user_id'
                        ELSE jsonb_set(COALESCE(meta, '{}'::jsonb), '{owner_user_id}', to_jsonb(owner_user_id::text))
                   END;

        ALTER TABLE jobs ADD CONSTRAINT jobs_owner_user_id_fkey
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
        ALTER TABLE source_deletions ADD CONSTRAINT source_deletions_owner_user_id_fkey
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
        ALTER TABLE source_deletions ADD CONSTRAINT source_deletions_deleted_by_user_id_fkey
            FOREIGN KEY (deleted_by_user_id) REFERENCES users(id) ON DELETE SET NULL;

        CREATE TABLE oauth_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            state_hash char(64) NOT NULL UNIQUE,
            nonce_hash char(64) NOT NULL,
            provider text NOT NULL CHECK (provider IN ('google', 'twitch')),
            intent text NOT NULL CHECK (intent IN ('login', 'link')),
            link_user_id uuid REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            CHECK ((intent = 'login' AND link_user_id IS NULL)
                OR (intent = 'link' AND link_user_id IS NOT NULL))
        );
        CREATE INDEX oauth_requests_expiry_idx ON oauth_requests(expires_at);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE source_deletions DROP CONSTRAINT source_deletions_deleted_by_user_id_fkey;
        ALTER TABLE source_deletions DROP CONSTRAINT source_deletions_owner_user_id_fkey;
        ALTER TABLE jobs DROP CONSTRAINT jobs_owner_user_id_fkey;
        DROP TABLE oauth_requests;

        DELETE FROM sessions WHERE token IS NULL;
        ALTER TABLE sessions ALTER COLUMN token SET NOT NULL;
        ALTER TABLE sessions DROP CONSTRAINT sessions_token_hash_key;
        ALTER TABLE sessions DROP COLUMN token_hash;
        ALTER TABLE sessions DROP COLUMN last_seen_at;

        DROP TABLE user_identities;
        ALTER TABLE users DROP CONSTRAINT users_role_check;
        UPDATE users SET role = 'user' WHERE role = 'moderator';
        ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('user', 'admin'));

        DELETE FROM source_deletions WHERE deleted_by_user_id IS NULL;
        DROP INDEX source_deletions_cleanup_pending_idx;
        ALTER TABLE source_deletions DROP COLUMN cleanup_lease_token;
        ALTER TABLE source_deletions DROP COLUMN cleanup_next_attempt_at;
        ALTER TABLE source_deletions DROP COLUMN cleanup_lease_until;
        ALTER TABLE source_deletions DROP COLUMN cleanup_completed_at;
        ALTER TABLE source_deletions DROP COLUMN cleanup_started_at;
        ALTER TABLE source_deletions DROP COLUMN cleanup_error;
        ALTER TABLE source_deletions DROP COLUMN cleanup_attempts;
        ALTER TABLE source_deletions DROP COLUMN cleanup_status;
        ALTER TABLE source_deletions DROP COLUMN wav_path;
        ALTER TABLE source_deletions DROP COLUMN raw_path;
        ALTER TABLE source_deletions ALTER COLUMN deleted_by_user_id SET NOT NULL;
    """)
