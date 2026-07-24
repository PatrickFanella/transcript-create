# OAuth, Accounts, and RBAC Implementation Plan

> **For agentic workers:** Execute this plan task-by-task. Recommended path:
> dispatch a fresh subagent per task, review each result with `review-quality`,
> then continue. For complex multi-agent splits, use
> `parallel-feature-development`, `team-composition-patterns`, and
> `team-communication-protocols`. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Deliver production-ready Google and Twitch sign-in, provider-neutral linked identities, self-service account lifecycle controls, and user/moderator/admin authorization.

**Architecture:** Keep `users` as the canonical HasanAra account and move external identities into a one-to-many `user_identities` table keyed by `(provider, subject)`. Centralize OAuth provider metadata and account mutations in focused backend modules; keep opaque server-side sessions and the existing `/auth/me` compatibility envelope. Add authenticated `/account/*` endpoints and a React account page, while retaining audit records with `user_id = NULL` after deletion.

**Tech Stack:** FastAPI, Authlib, SQLAlchemy text queries, PostgreSQL, Alembic, React 19, TypeScript, Ky, Vitest, Playwright, pytest.

**Approved scope:**

- Google and Twitch now; provider registry designed for additional OIDC/OAuth adapters later.
- Anyone with a valid configured provider identity may create an account; default role is `user`.
- Editable display name and avatar URL.
- Link and unlink provider identities without automatic email-based account merging.
- List active sessions, revoke other sessions, and log out all sessions.
- Delete the account and owned private data with explicit confirmation.
- Roles are `user`, `moderator`, and `admin`; plan/entitlement remains separate from authorization.
- No password registration, password reset, provider access-token persistence, or automatic cross-account merging.

**Security invariants:**

1. Provider `sub`/user ID, never email, is the external identity key.
2. OAuth state and OIDC nonce are single-use, provider-bound, intent-bound, and validated.
3. Linking requires an existing authenticated session before redirect and at callback.
4. A provider identity can belong to only one user; collisions return `409` and never merge accounts.
5. An account cannot unlink its final login identity.
6. Role changes are admin-only; an admin cannot demote the final active admin.
7. Account deletion revokes sessions and API keys before deleting the user and clears the cookie.
8. Audit logs retain operational history but lose the deleted account foreign key through `ON DELETE SET NULL`.
9. Cookie-authenticated unsafe requests require both an approved `Origin` and a session-bound CSRF header.
10. OAuth request state is authoritative in PostgreSQL, not in the client-side signed session cookie.
11. Database session records contain only SHA-256 token hashes; a database read cannot directly replay a session.
12. Email never grants a role dynamically; bootstrap administration is bound to provider plus subject.

---

## File Structure

**Create**

- `app/auth/__init__.py` — auth package boundary.
- `app/auth/providers.py` — provider registry and normalized provider profile contract.
- `app/accounts.py` — account/identity/session persistence operations.
- `app/csrf.py` — Origin validation and session-bound CSRF token creation/verification.
- `app/routes/account.py` — authenticated profile, identity, session, and deletion endpoints.
- `alembic/versions/20260714_0100_add_linked_identities_and_moderator.py` — linked-identity migration, backfill, role constraint, session metadata.
- `tests/test_accounts.py` — persistence and collision tests.
- `tests/test_routes_account.py` — account endpoint, deletion-concurrency, and CSRF tests.
- `frontend/src/routes/AccountPage.tsx` — account settings UI.
- `frontend/src/tests/AccountPage.test.tsx` — account UI behavior tests.

**Modify**

- `sql/schema.sql` — fresh-install parity for identities, role constraint, hash-only sessions/activity, durable job ownership, complete source-deletion ownership, and deletion foreign keys.
- `app/policy.py` — user/moderator/admin vocabulary and capabilities.
- `app/security.py` — exported moderator role and hierarchy enforcement.
- `app/common/session.py` — hashed session-token lookup, refresh, and cookie behavior.
- `app/middleware.py` — retain OAuth cookie only for Authlib compatibility; PostgreSQL is authoritative.
- `app/audit.py` — link/unlink/profile/session/role/deletion actions.
- `app/routes/auth.py` — provider-neutral login/callback, hardened state/nonce, identity lookup.
- `app/routes/admin.py` — admin-only role mutation endpoint and final-admin guard.
- `app/routes/jobs.py` — plan/entitlement-based quota checks and ownership-write consistency, independent of authorization roles.
- `app/routes/events.py` — central CSRF coverage for `/events` and `/events/batch` mutations.
- `app/main.py` — register account router.
- `app/settings.py` — provider enablement checks and OAuth flow settings.
- `frontend/src/services/auth.tsx` — typed account refresh and generic provider login/link helpers.
- `frontend/src/services/api.ts` — account endpoint client functions if this file remains the hand-authored facade.
- `frontend/src/main.tsx` — `/account` route.
- `frontend/src/routes/AppLayout.tsx` — account navigation.
- `frontend/src/routes/admin/AdminUsers.tsx` — role display/change controls.
- `frontend/src/types/api.ts` — account DTOs used by handwritten frontend code.
- `frontend/src/types/generated/api.ts` — regenerate from OpenAPI.
- `tests/test_oauth_security.py` — state, nonce, intent, collision, and callback tests.
- `tests/test_routes_auth.py` — `/auth/me`, sign-in, session, and compatibility tests.
- `tests/test_security.py` — moderator hierarchy/capability tests.
- `tests/test_migrations.py` — identity backfill and role constraint tests.
- `frontend/src/tests/auth.test.tsx` — new auth context methods.
- `frontend/src/tests/AppLayout.test.tsx` — account navigation.
- `frontend/src/tests/AdminLayoutAccess.test.tsx` — moderator/admin boundaries.
- `docs/authentication.md` — current provider setup and account lifecycle.
- `.env.example` and `.env.prod.example` if present — exact provider variables without secrets.

---

### Task 1: Add the linked-identity schema and migration

**Files:**
- Create: `alembic/versions/20260714_0100_add_linked_identities_and_moderator.py`
- Modify: `sql/schema.sql:20-30, 193-220, and add the missing source_deletions definition near the account/ownership tables`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write migration tests that fail before the revision exists**

Add assertions that upgrading from `20260712_opinions` creates and backfills identities, preserves user IDs, accepts `moderator`, rejects unknown roles, backfills nullable session token hashes while retaining legacy plaintext tokens during the expand phase, creates server-side OAuth request storage, and adds `ON DELETE SET NULL` foreign keys for `jobs.owner_user_id` plus `source_deletions.owner_user_id` and `source_deletions.deleted_by_user_id`. Seed valid and orphan UUID ownership values before upgrade; assert valid values remain and every orphan is normalized to `NULL` before the foreign keys are added. Assert fresh-schema parity includes `jobs.owner_user_id` and the complete `source_deletions` structure (`id`, `video_id`, `youtube_id`, both nullable user fields, `deleted_at`, `backup_exclusion_until`, and unique `video_id`) with the same foreign-key actions. Do not create, apply, or test the later `0200` contract migration in this task.

```python
def test_linked_identity_migration_backfills_legacy_oauth(alembic_connection):
    alembic_connection.execute(sa.text("""
        INSERT INTO users (id, email, name, oauth_provider, oauth_subject, role)
        VALUES ('00000000-0000-0000-0000-000000000123', 'person@example.com',
                'Person', 'google', 'google-subject', 'user')
    """))
    command.upgrade(alembic_cfg, "20260714_linked_identities")
    row = alembic_connection.execute(sa.text("""
        SELECT user_id, provider, subject, provider_email
        FROM user_identities
        WHERE provider = 'google' AND subject = 'google-subject'
    """)).mappings().one()
    assert str(row["user_id"]) == "00000000-0000-0000-0000-000000000123"
    assert row["provider_email"] == "person@example.com"


def test_users_role_constraint_accepts_moderator_and_rejects_unknown(alembic_connection):
    command.upgrade(alembic_cfg, "20260714_linked_identities")
    alembic_connection.execute(sa.text("""
        INSERT INTO users (id, role)
        VALUES ('00000000-0000-0000-0000-000000000124', 'moderator')
    """))
    with pytest.raises(sa.exc.IntegrityError):
        alembic_connection.execute(sa.text("""
            INSERT INTO users (id, role)
            VALUES ('00000000-0000-0000-0000-000000000125', 'owner')
        """))
```

- [ ] **Step 2: Run the focused migration tests and confirm failure**

Run: `python3 -m pytest tests/test_migrations.py -q`

Expected: failure because revision `20260714_linked_identities` and `user_identities` do not exist.

- [ ] **Step 3: Implement the expand Alembic revision**

Set `revision = "20260714_linked_identities"` and `down_revision = "20260712_opinions"`, then execute this schema shape:

```sql
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
WHERE oauth_provider IS NOT NULL AND oauth_subject IS NOT NULL
ON CONFLICT (provider, subject) DO NOTHING;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
UPDATE users SET role = 'user' WHERE role NOT IN ('user', 'moderator', 'admin');
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('user', 'moderator', 'admin'));

ALTER TABLE sessions ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE sessions ADD COLUMN token_hash char(64);
UPDATE sessions SET token_hash = encode(digest(token, 'sha256'), 'hex');
ALTER TABLE sessions ADD CONSTRAINT sessions_token_hash_key UNIQUE (token_hash);
ALTER TABLE sessions ALTER COLUMN token DROP NOT NULL;

-- Normalize pre-existing orphan UUID references before adding ownership FKs.
ALTER TABLE source_deletions ALTER COLUMN deleted_by_user_id DROP NOT NULL;

UPDATE jobs SET owner_user_id = NULL
WHERE owner_user_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = jobs.owner_user_id);
UPDATE source_deletions SET owner_user_id = NULL
WHERE owner_user_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = source_deletions.owner_user_id);
UPDATE source_deletions SET deleted_by_user_id = NULL
WHERE deleted_by_user_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = source_deletions.deleted_by_user_id);

ALTER TABLE jobs
    ADD CONSTRAINT jobs_owner_user_id_fkey
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE source_deletions
    ADD CONSTRAINT source_deletions_owner_user_id_fkey
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE source_deletions
    ADD CONSTRAINT source_deletions_deleted_by_user_id_fkey
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
```

`token_hash` remains nullable in this expand revision, and `sessions.token` remains intact but nullable for the maintenance cutover; no application version may use the expand schema in mixed old/new operation. Its downgrade restores `source_deletions.deleted_by_user_id` only after removing rows where it is null, drops the ownership foreign keys, drops `oauth_requests`, clears sessions created without plaintext tokens before restoring the legacy `token NOT NULL` contract, drops the `token_hash` constraint/column and `last_seen_at`, drops `user_identities`, removes the three-role constraint, normalizes `moderator` back to `user`, and recreates a `user/admin` constraint. Keep legacy `users.oauth_provider` and `users.oauth_subject` during this rollout so backfilled records and rollback remain safe; all new runtime reads/writes move to `user_identities`.

- [ ] **Step 4: Mirror the resulting schema in `sql/schema.sql`**

Bring the full fresh-install definition to post-contract parity, not just the former `users`/`sessions` line range, using this explicit dependency order: the earlier `jobs` definition may add nullable `owner_user_id UUID`, but it must not declare an inline foreign key before `users` exists. Create `users`; then add the jobs foreign key only afterward with:

```sql
ALTER TABLE jobs ADD CONSTRAINT jobs_owner_user_id_fkey
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
```

Create `source_deletions` only after `users`, with `id`, `video_id`, `youtube_id`, nullable `owner_user_id` and `deleted_by_user_id` columns each referencing `users(id) ON DELETE SET NULL`, `deleted_at`, `backup_exclusion_until`, and unique `video_id`. Preserve the durable-job ownership indexes and add the same identity/OAuth tables, role checks, and indexes. Fresh installs define non-null `sessions.token_hash`, never `sessions.token`, matching the intended post-contract schema. Change the role comment to `user, moderator, admin`; do not reintroduce `pro` as a role because `plan='pro'` is an entitlement, not authorization.

- [ ] **Step 5: Run migration tests**

Run: `python3 -m pytest tests/test_migrations.py -q`

Expected: all expand-migration and fresh-schema parity tests pass; `20260714_linked_identities` is the only new revision and Alembic head for this task.

---

### Task 2: Establish the moderator authorization contract

**Files:**
- Modify: `app/policy.py`
- Modify: `app/security.py`
- Modify: `app/routes/jobs.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write failing hierarchy and capability tests**

```python
def test_moderator_is_between_user_and_admin():
    moderator = {"role": "moderator", "plan": "free"}
    assert get_user_role(moderator) == "moderator"
    assert has_role(moderator, ROLE_USER)
    assert has_role(moderator, ROLE_MODERATOR)
    assert not has_role(moderator, ROLE_ADMIN)


def test_pro_plan_does_not_change_authorization_role():
    assert get_user_role({"role": "user", "plan": "pro"}) == ROLE_USER
```

- [ ] **Step 2: Run and confirm the tests fail**

Run: `python3 -m pytest tests/test_security.py -q`

Expected: imports/expectations fail because `ROLE_MODERATOR` is absent and `pro` still escalates role.

- [ ] **Step 3: Implement the explicit three-role hierarchy**

Use this vocabulary in `app/policy.py`:

```python
ROLE_USER = "user"
ROLE_MODERATOR = "moderator"
ROLE_ADMIN = "admin"
ROLE_HIERARCHY = {ROLE_USER: 0, ROLE_MODERATOR: 1, ROLE_ADMIN: 2}

CAP_MODERATION_ACCESS = "moderation:access"
ROLE_CAPABILITIES = {
    ROLE_USER: BASE_CAPABILITIES,
    ROLE_MODERATOR: BASE_CAPABILITIES | {CAP_MODERATION_ACCESS, CAP_VOCABULARIES_GLOBAL},
    ROLE_ADMIN: BASE_CAPABILITIES
    | {CAP_MODERATION_ACCESS, CAP_VOCABULARIES_GLOBAL, CAP_ADMIN_ACCESS},
}
```

Update `resolve_role()` so only the stored `moderator` or `admin` value affects role. Remove the runtime `ADMIN_EMAILS` fallback from `app/common/session.py`; email is mutable provider metadata and must never grant privileges. Add `BOOTSTRAP_ADMIN_IDENTITIES` as comma-separated `provider:subject` values and apply it only inside identity sign-in after a verified provider subject is known. A `pro` plan must no longer resolve to an authorization role. Update `app/routes/jobs.py` quota checks that use `ROLE_PRO` to use the user's stored plan/entitlement (or the existing entitlement helper), never authorization role. Re-export `ROLE_MODERATOR` from `app/security.py`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_security.py -q`

Expected: all role and capability tests pass.

---

### Task 3: Centralize provider configuration and harden OAuth state/nonce

**Files:**
- Create: `app/auth/__init__.py`
- Create: `app/auth/providers.py`
- Modify: `app/audit.py`
- Modify: `app/routes/auth.py`
- Modify: `app/settings.py`
- Modify: `tests/test_oauth_security.py`

- [ ] **Step 1: Add failing provider-normalization and request-binding tests**

Cover these cases explicitly:

```python
def test_google_profile_uses_sub_as_subject():
    profile = normalize_profile("google", {
        "sub": "g-123", "email": "a@example.com", "email_verified": True,
        "name": "A", "picture": "https://example.com/a.png",
    })
    assert profile.provider == "google"
    assert profile.subject == "g-123"
    assert profile.email_verified is True


def test_oauth_login_passes_nonce_and_provider_bound_state(client, monkeypatch):
    response = client.get("/auth/login/google", follow_redirects=False)
    assert response.status_code in {302, 307}
    authorize_kwargs = captured_authorize_kwargs()
    assert authorize_kwargs["nonce"]
    assert authorize_kwargs["state"]


def test_callback_rejects_state_created_for_other_provider(client):
    begin_oauth(client, provider="google")
    response = client.get("/auth/callback/twitch?code=x&state=" + stored_state(client))
    assert response.status_code == 422


def test_callback_state_is_single_use_even_if_cookie_is_replayed(client):
    state, oauth_cookie = begin_oauth(client, provider="google")
    complete_mock_callback(client, provider="google", state=state)
    response = client.get(
        f"/auth/callback/google?code=second&state={state}",
        headers={"cookie": oauth_cookie},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run focused OAuth tests and confirm failure**

Run: `python3 -m pytest tests/test_oauth_security.py -q`

Expected: normalization symbols are absent and nonce/provider binding assertions fail.

- [ ] **Step 3: Refactor audit writes and implement the provider registry**

Before any OAuth callback or account operation uses audit logging, refactor `app/audit.py` to expose transaction-participating `write_audit_event(db, ...)`, which executes SQL but never commits or rolls back. Retain a best-effort wrapper only for callers with no surrounding unit of work. Use an explicit transaction-ownership pattern compatible with a SQLAlchemy dependency that may already autobegin: the route/service owner checks `db.in_transaction()` before starting work; if it owns a new transaction it commits once on success and rolls back on failure, while if a dependency-owned transaction already exists the designated request-boundary owner explicitly commits or rolls it back. Do not use a literal `with db.begin()` around callback/account work after dependency queries may have autobegun. Each account mutation and its audit write must share one transaction, so an audit failure rolls back the related mutation; the separately durable OAuth-request consume is defined in Step 4.

Define a normalized immutable profile and two provider definitions:

```python
@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    subject: str
    email: str | None
    email_verified: bool | None
    name: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    client_id: str
    client_secret: str
    redirect_uri: str
    server_metadata_url: str | None
    authorize_url: str | None
    access_token_url: str | None
    api_base_url: str | None
    scope: str
```

Google uses discovery and scopes `openid email profile`. Twitch uses authorization code flow, scopes `user:read:email`, and Helix `/users`; normalize Twitch `id` as subject. Keep provider-specific response parsing in `providers.py`, not route handlers. Reject empty subjects with `ValidationError`.

- [ ] **Step 4: Bind every request in server-side storage**

Store only hashes in the authoritative `oauth_requests` table and commit the insert before redirecting the browser, so the state is durable even if the process dies after issuing the redirect:

```python
db.execute(text("""
    INSERT INTO oauth_requests
        (state_hash, nonce_hash, provider, intent, link_user_id, expires_at)
    VALUES
        (:state_hash, :nonce_hash, :provider, :intent, :link_user_id,
         now() + interval '10 minutes')
"""), {
    "state_hash": sha256(state.encode()).hexdigest(),
    "nonce_hash": sha256(nonce.encode()).hexdigest(),
    "provider": provider.name,
    "intent": intent,
    "link_user_id": str(user["id"]) if intent == "link" else None,
})
db.commit()
```

Pass both `state` and `nonce` to `authorize_redirect`. The Authlib signed cookie may retain its own protocol state, but it is never accepted as proof of single use. On callback, atomically consume and commit the PostgreSQL row before completing identity/session mutations, using `UPDATE ... SET consumed_at=now() WHERE state_hash=:hash AND consumed_at IS NULL AND expires_at>now() RETURNING *`; reject and roll back when no row is returned, and require provider equality and the same authenticated user for `intent == "link"`. Google OIDC validates its returned `nonce` claim by hashing it and using `hmac.compare_digest()` against `nonce_hash`. Twitch's Helix user response has no returned nonce claim: still bind and single-use the request nonce server-side, but do not require a nonexistent Twitch nonce claim. A replayed browser cookie cannot revive a durably consumed row. Periodically delete expired rows.

Remove the ability to disable state validation in production. `OAUTH_STATE_VALIDATION=false` may remain only as a test/development compatibility option guarded by `ENVIRONMENT != 'production'`; production settings validation rejects it. Never include authorization codes, provider tokens, raw state/nonce, or full provider exceptions in logs, audit details, redirects, or API responses.

- [ ] **Step 5: Fail closed when a provider is not configured**

Add a provider-enabled check requiring both client ID and secret. Login/link endpoints for disabled providers return `503`; production settings validation continues to require HTTPS callbacks whenever either credential is present. Replace `str(e)` in callback responses/audit rows with a stable error code and provider name; detailed exceptions go only to structured server logs after redaction.

- [ ] **Step 6: Run OAuth tests**

Run: `python3 -m pytest tests/test_oauth_security.py tests/test_routes_auth.py -q`

Expected: state replay, provider mismatch, expired request, missing nonce/subject, disabled provider, and normal callback tests pass.

---

### Task 4: Implement canonical account and identity operations

**Files:**
- Create: `app/accounts.py`
- Create: `tests/test_accounts.py`
- Modify: `app/routes/auth.py`
- Modify: `app/audit.py`

- [ ] **Step 1: Write failing identity resolution tests**

```python
def test_sign_in_creates_user_and_identity(db_session):
    result = sign_in_identity(db_session, google_profile("g-1", "a@example.com"))
    assert result.created is True
    assert result.user["role"] == "user"
    assert identity_count(db_session, result.user["id"]) == 1


def test_sign_in_existing_identity_returns_same_user(db_session):
    first = sign_in_identity(db_session, google_profile("g-1", "a@example.com"))
    second = sign_in_identity(db_session, google_profile("g-1", "new@example.com"))
    assert second.user["id"] == first.user["id"]
    assert second.created is False


def test_link_collision_never_merges_by_email(db_session):
    owner = sign_in_identity(db_session, google_profile("g-1", "same@example.com"))
    other = sign_in_identity(db_session, twitch_profile("t-1", "same@example.com"))
    with pytest.raises(IdentityConflictError):
        link_identity(db_session, owner.user["id"], twitch_profile("t-1", "same@example.com"))
    assert other.user["id"] != owner.user["id"]


def test_session_database_value_cannot_replay_cookie(db_session, user_id):
    raw_token = create_session(db_session, user_id, user_agent=None, ip_address=None)
    stored = db_session.execute(text("SELECT token_hash FROM sessions")).scalar_one()
    assert stored == hashlib.sha256(raw_token.encode()).hexdigest()
    assert stored != raw_token
```

Update the session fixtures/helpers used by these account, auth, OAuth, and integration tests before the contract migration: direct inserts store only `token_hash`, and direct lookups hash the supplied raw test cookie before querying. Do not leave a compatibility fixture that inserts or selects `sessions.token` after Alembic head.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m pytest tests/test_accounts.py -q`

Expected: `app.accounts` does not exist.

- [ ] **Step 3: Implement transaction-safe account operations**

Create these public functions with SQL unique constraints as the final race-condition guard:

```python
def create_session(db, user_id: UUID | str, *, user_agent: str | None, ip_address: str | None) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.execute(text("""
        INSERT INTO sessions (user_id, token_hash, user_agent, ip_address, expires_at)
        VALUES (:user_id, :token_hash, :user_agent, :ip_address, :expires_at)
    """), {
        "user_id": str(user_id), "token_hash": token_hash,
        "user_agent": user_agent, "ip_address": ip_address,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_EXPIRE_HOURS),
    })
    return token
```

In the same module implement `sign_in_identity(db, profile)`, `link_identity(db, user_id, profile)`, `unlink_identity(db, user_id, provider)`, `list_identities(db, user_id)`, and `update_profile(db, user_id, *, name: str | None, avatar_url: str | None)` with concrete SQL covered by the preceding tests. `sign_in_identity` selects by `(provider, subject)`, updates provider metadata and `last_login_at`, and fills canonical user name/email/avatar only when those fields are null. New users are inserted with `role='user'`, except a verified `(provider, subject)` listed in `BOOTSTRAP_ADMIN_IDENTITIES` is inserted/promoted to `admin` with an audit event. `link_identity` rejects a provider already linked to the same account and returns a domain conflict when the identity belongs to another account. `unlink_identity` locks the user's identities and rejects removal when count is one.

Update every session lookup/delete/refresh in `app/common/session.py` and `app/routes/auth.py` to hash the raw cookie token before SQL comparison. Use `hmac.compare_digest` for security-sensitive token comparisons in Python. Never log either raw token or hash.

Translate uniqueness races to a stable `409 identity_conflict` API error; do not expose SQL details.

- [ ] **Step 4: Replace duplicated callback SQL in `app/routes/auth.py`**

Both callbacks must normalize the provider response, call `sign_in_identity` or `link_identity` based on the validated intent, create an opaque session only for sign-in, and redirect to:

- sign-in success: `${FRONTEND_ORIGIN}/`
- link success: `${FRONTEND_ORIGIN}/account?linked=<provider>`
- link conflict/error: `${FRONTEND_ORIGIN}/account?error=identity_conflict`

Add audit constants and events for `identity_linked`, `identity_unlinked`, and collision attempts. Audit provider name and affected user ID, never access/refresh tokens.

- [ ] **Step 5: Run account and callback tests**

Run: `python3 -m pytest tests/test_accounts.py tests/test_oauth_security.py tests/test_routes_auth.py -q`

Expected: account creation, repeat sign-in, linking, collision, final-identity guard, and both provider callbacks pass.

---

### Task 5: Add self-service account, identity, and session APIs

**Files:**
- Create: `app/csrf.py`
- Create: `app/routes/account.py`
- Create: `tests/test_routes_account.py`
- Modify: `app/main.py`
- Modify: `app/common/session.py`
- Modify: `app/audit.py`
- Modify: `app/routes/auth.py`
- Modify: `app/routes/jobs.py`
- Modify: `app/routes/admin.py`
- Modify: `app/routes/archive.py`, `app/routes/api_keys.py`, `app/routes/favorites.py`, `app/routes/saved_searches.py`, `app/routes/videos.py`, and `app/routes/vocabularies.py` (or the central router/middleware/dependency that covers their unsafe endpoints)
- Modify: `app/routes/events.py` (or the central router/middleware/dependency that covers `/events` and `/events/batch` mutations)
- Modify: route regression tests for archive, API keys, favorites, saved searches, videos, vocabularies, jobs, events, logout, account, and admin mutations
- Modify: `frontend/src/services/auth.tsx`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/tests/auth.test.tsx`

- [ ] **Step 1: Write failing endpoint tests**

Cover the exact account contract and central CSRF coverage: every unsafe cookie-authenticated route in archive, API keys, favorites, saved searches, videos, vocabularies, jobs, **events (`/events` and `/events/batch`)**, logout, account, and admin must reject a missing/invalid Origin or CSRF header. Include a regression case proving an API-key-only unsafe request is exempt only when no session cookie is present, while a request carrying both `Authorization` and a session cookie still requires CSRF.

```python
def test_get_account_returns_profile_identities_and_sessions(auth_client):
    response = auth_client.get("/account")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "user"
    assert body["identities"][0]["provider"] == "google"
    assert body["sessions"][0]["current"] is True


def test_patch_account_validates_profile(auth_client):
    assert auth_client.patch("/account", json={"name": "  "}).status_code == 422
    response = auth_client.patch("/account", json={"name": "Archive Viewer", "avatar_url": None})
    assert response.status_code == 200
    assert response.json()["user"]["name"] == "Archive Viewer"


def test_revoke_other_sessions_keeps_current(auth_client, db_session):
    response = auth_client.delete("/account/sessions", params={"keep_current": "true"})
    assert response.status_code == 200
    assert response.json()["revoked"] == 1


def test_unlink_last_identity_is_rejected(auth_client):
    response = auth_client.delete("/account/identities/google")
    assert response.status_code == 409
    assert response.json()["error"] == "last_identity"


def test_cookie_authenticated_mutation_requires_csrf(auth_client):
    response = auth_client.patch(
        "/account",
        json={"name": "Blocked"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_cookie_authenticated_mutation_accepts_origin_and_csrf(auth_client):
    token = auth_client.get("/auth/csrf").json()["csrf_token"]
    response = auth_client.patch(
        "/account",
        json={"name": "Allowed"},
        headers={"origin": settings.FRONTEND_ORIGIN, "x-csrf-token": token},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run and confirm 404 failures**

Run: `python3 -m pytest tests/test_routes_account.py -q`

Expected: `/account` routes return 404.

- [ ] **Step 3: Implement session-bound CSRF protection**

Co-land the backend enforcement and client support in this task; do not deploy broad backend CSRF enforcement before the frontend can supply its token. `GET /auth/csrf` requires a valid cookie session and returns a token derived as `HMAC-SHA256(SESSION_SECRET, raw_session_token)`. Install `require_csrf` centrally (middleware or a shared unsafe-route dependency) so it covers every cookie-authenticated `POST`, `PUT`, `PATCH`, and `DELETE` route—archive, API keys, favorites, saved searches, videos, vocabularies, jobs, **`/events`, `/events/batch`**, logout, account, and admin—including newly added routes. It must:

1. Require `Origin` to exactly equal `FRONTEND_ORIGIN` or one configured CORS origin; reject missing origins in production.
2. Reject `Sec-Fetch-Site: cross-site` when present.
3. Recompute the HMAC from the current raw cookie and compare `X-CSRF-Token` with `hmac.compare_digest()`.
4. Exempt an API-key-authorized request only when it has **no session cookie**; if a session cookie is present, require Origin and CSRF even when `Authorization` is also present.
5. Exempt provider callback GET routes because their server-side single-use state is the CSRF defense.

In the same change, the frontend fetches the CSRF token into memory after `/auth/me` succeeds and sends it through Ky's `beforeRequest` hook on unsafe methods, including logout. It clears and reacquires the in-memory token as auth state changes and never writes the token to localStorage. Add auth-service tests covering token acquisition, unsafe-method header injection, and logout.

- [ ] **Step 4: Implement typed request/response models and routes**

Expose:

```text
GET    /account
PATCH  /account
GET    /account/identities
POST   /account/identities/{provider}/link
DELETE /account/identities/{provider}
GET    /account/sessions
DELETE /account/sessions?keep_current=true
DELETE /account/sessions/{session_id}
```

All read routes use `Depends(require_auth)`; all unsafe routes additionally use `Depends(require_csrf)`. Profile rules: trimmed name length `1..100`; avatar is null or an `https` URL up to 2048 characters. Identity responses expose provider, provider email/name/avatar, created time, and last login but never subject or provider tokens. Session responses expose ID, coarse user-agent label, created/last-seen/expires timestamps, and `current`; do not expose session tokens, token hashes, or full IP addresses.

`POST /account/identities/{provider}/link` is CSRF-protected. It durably creates the validated OAuth request before responding with JSON `{ "authorization_url": string }`; it does not redirect. The authenticated frontend client posts to this endpoint, then assigns `window.location` to the returned URL. Session deletion uses `(id, user_id)` ownership in SQL. `DELETE /account/sessions?keep_current=false` also clears the cookie in its response.

- [ ] **Step 5: Update session activity safely**

When an authenticated session is refreshed, update `last_seen_at` with the expiry in the same statement. Do not write on every request; retain the existing refresh threshold behavior.

- [ ] **Step 6: Register the router and run tests**

Run: `python3 -m pytest tests/test_routes_account.py tests/test_routes_auth.py tests/test_security.py -q`

Expected: all account/session contracts and prior auth behavior pass.

---

### Task 6: Implement safe account deletion

**Files:**
- Modify: `app/accounts.py`
- Modify: `app/routes/account.py`
- Modify: `app/audit.py`
- Modify: `tests/test_accounts.py`
- Modify: `tests/test_routes_account.py`

- [ ] **Step 1: Write failing deletion tests**

```python
def test_delete_account_revokes_access_and_removes_private_data(auth_client, db_session):
    response = auth_client.request(
        "DELETE", "/account", json={"confirmation": "DELETE"}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert "tc_session=" in response.headers["set-cookie"]
    assert response_after_delete(auth_client).json()["user"] is None
    assert count_rows(db_session, "sessions") == 0
    assert count_rows(db_session, "api_keys") == 0
    assert count_rows(db_session, "favorites") == 0
    assert count_rows(db_session, "user_searches") == 0


def test_delete_account_requires_exact_confirmation(auth_client):
    response = auth_client.request("DELETE", "/account", json={"confirmation": "delete"})
    assert response.status_code == 422


def test_delete_account_anonymizes_non_fk_ownership(auth_client, db_session, user_id):
    delete_with_csrf(auth_client)
    job = db_session.execute(text("SELECT owner_user_id, meta FROM jobs")).mappings().one()
    assert job["owner_user_id"] is None
    assert "owner_user_id" not in job["meta"]
    assert "api_key_id" not in job["meta"]
    tombstone = db_session.execute(text("""
        SELECT owner_user_id, deleted_by_user_id FROM source_deletions
    """)).mappings().one()
    assert tombstone["owner_user_id"] is None
    assert tombstone["deleted_by_user_id"] is None


def test_failed_account_delete_rolls_back_audit_and_session(auth_client, db_session):
    force_delete_failure(db_session)
    response = delete_with_csrf(auth_client)
    assert response.status_code == 500
    assert current_session_exists(db_session)
    assert deletion_audit_count(db_session) == 0
```

Add concurrent-transaction regression coverage: pause deletion after it has locked the owned `jobs` and `source_deletions` rows, attempt a concurrent ownership write, then complete deletion. Assert the writer cannot commit an `owner_user_id` referring to the deleted user and that its JSON metadata cannot retain or introduce a conflicting `owner_user_id`. Run the ownership-field inventory assertion against both the migration schema and fresh schema.

Add final-admin deletion invariant coverage: deleting the sole active admin returns `409` with `{"error": "final_admin"}` and leaves the user/session intact; with two admins, concurrent deletion attempts must use the same transaction-wide admin-role lock and leave at least one admin. Verify a deletion racing a role mutation is serialized and cannot commit a state with zero admins.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m pytest tests/test_routes_account.py tests/test_accounts.py -q`

Expected: account deletion endpoint/function is absent.

- [ ] **Step 3: Implement transactional deletion of account-private records**

Add:

```python
def delete_account(db, user_id: UUID | str) -> None:
    # Use the same stable transaction advisory lock as role mutation before
    # reading/counting any admins, so delete and demote cannot strand zero admins.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('hasanara:admin-role-mutation', 0))"))
    db.execute(text("SELECT id FROM users WHERE id=:id FOR UPDATE"), {"id": str(user_id)}).one()
    if is_admin(db, user_id) and count_admins(db) == 1:
        raise FinalAdminError()
    # Lock every ownership row before anonymizing it; locking users alone does not
    # serialize concurrent writers to jobs/source_deletions.
    db.execute(text("SELECT id FROM jobs WHERE owner_user_id=:id OR meta->>'owner_user_id'=:id FOR UPDATE"), {"id": str(user_id)})
    db.execute(text("SELECT id FROM source_deletions WHERE owner_user_id=:id OR deleted_by_user_id=:id FOR UPDATE"), {"id": str(user_id)})
    db.execute(text("DELETE FROM sessions WHERE user_id=:id"), {"id": str(user_id)})
    db.execute(text("UPDATE api_keys SET revoked_at=now() WHERE user_id=:id AND revoked_at IS NULL"), {"id": str(user_id)})
    db.execute(text("""
        UPDATE jobs
        SET owner_user_id=NULL,
            meta=(COALESCE(meta, '{}'::jsonb) - 'owner_user_id' - 'api_key_id')
        WHERE owner_user_id=:id OR meta->>'owner_user_id'=:id
    """), {"id": str(user_id)})
    db.execute(text("""
        UPDATE source_deletions
        SET owner_user_id=NULL,
            deleted_by_user_id=CASE WHEN deleted_by_user_id=:id THEN NULL ELSE deleted_by_user_id END
        WHERE owner_user_id=:id OR deleted_by_user_id=:id
    """), {"id": str(user_id)})
    db.execute(text("""
        UPDATE audit_logs
        SET user_id=NULL, ip_address=NULL, user_agent=NULL, details='{}'::jsonb
        WHERE user_id=:id
    """), {"id": str(user_id)})
    db.execute(text("""
        UPDATE events SET user_id=NULL, payload='{}'::jsonb WHERE user_id=:id
    """), {"id": str(user_id)})
    db.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(user_id)})
```

Rely on declared foreign-key actions for identities, favorites, saved searches, and other account-private rows. The migration and fresh schema must give `jobs.owner_user_id` and every compatible known `source_deletions` UUID user field an `ON DELETE SET NULL` foreign key, so a post-delete ownership write cannot reference the deleted user. Explicitly anonymize `jobs.owner_user_id`, `jobs.meta`, and `source_deletions`; every job ownership writer must keep `meta.owner_user_id` consistent with the FK-backed `owner_user_id` (and remove it when that column is null), rather than treating JSON as an independent owner reference. Scrub IP, user agent, email-bearing details, and user payloads before retaining operational `events`/`audit_logs`. Stop placing email in new login audit details. Do **not** delete archived VODs merely because the submitting account is deleted. Add an integration inventory/regression assertion for every `users(id)` foreign key plus the known UUID/JSON ownership fields, including metadata/FK consistency, so deletion cannot silently break when a new user-owned field is introduced.

- [ ] **Step 4: Add deletion endpoint and audit ordering**

Use the transaction-participating `write_audit_event(db, ...)` refactor established in Task 3. `DELETE /account` accepts `{"confirmation":"DELETE"}` and runs CSRF validation, acquisition of the same stable `hasanara:admin-role-mutation` transaction advisory lock used by Task 7, final-admin validation, the deletion audit insert, privacy scrubbing, session revocation, and user deletion in one explicitly owned unit of work. If the target is the final admin, return `409 final_admin` before destructive writes. Follow the Task 3 SQLAlchemy transaction-ownership pattern rather than a literal `with db.begin()`: if this route owns a new transaction, commit it once on success and roll it back on failure; if the dependency has autobegun one, make the designated request-boundary owner explicitly commit or roll it back. The route clears the cookie only after commit and returns `{"deleted": true}`. On any failure, the transaction rolls back the audit and deletion together and retains the session. Update other transaction-owning callers so audit helpers never commit or roll back their work unexpectedly.

- [ ] **Step 5: Run privacy/deletion tests**

Run: `python3 -m pytest tests/test_routes_account.py tests/test_event_privacy.py tests/test_routes_videos.py tests/test_saved_searches.py -q`

Expected: deletion revokes access, removes private account data, preserves/anonymizes audit history, never removes public archive records, and cannot delete or concurrently demote the final admin.

---

### Task 7: Add admin role management with final-admin protection

**Files:**
- Modify: `app/routes/admin.py`
- Modify: `app/audit.py`
- Modify: `tests/test_security.py`
- Modify: `tests/integration/test_auth_flow.py`

- [ ] **Step 1: Write failing authorization and invariant tests**

```python
@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_admin_can_assign_supported_role(admin_client, target_user_id, role):
    response = admin_client.put(f"/admin/users/{target_user_id}/role", json={"role": role})
    assert response.status_code == 200
    assert response.json()["role"] == role


def test_moderator_cannot_assign_roles(moderator_client, target_user_id):
    response = moderator_client.put(
        f"/admin/users/{target_user_id}/role", json={"role": "moderator"}
    )
    assert response.status_code == 403


def test_final_admin_cannot_be_demoted(admin_client, admin_user_id):
    response = admin_client.put(f"/admin/users/{admin_user_id}/role", json={"role": "user"})
    assert response.status_code == 409
    assert response.json()["error"] == "final_admin"
```

Add a two-connection regression test with two active admins attempting to demote different admin rows concurrently; exactly one may commit, and the other must receive `409 final_admin` after the transaction-wide serialization lock is released.

- [ ] **Step 2: Run and confirm failure**

Run `tests/integration/test_auth_flow.py` plus `tests/test_security.py`.

Expected: role endpoint is absent and moderator constants may not yet be reflected in route tests.

- [ ] **Step 3: Implement admin-only role mutation**

Add `PUT /admin/users/{user_id}/role` with a literal enum of `user`, `moderator`, `admin` and CSRF enforcement for cookie sessions. At the start of the role-mutation transaction, before reading the target or counting/changing admin roles, acquire a PostgreSQL transaction advisory lock using the stable named key `hasanara:admin-role-mutation` (for example, `SELECT pg_advisory_xact_lock(hashtextextended('hasanara:admin-role-mutation', 0))`). Then lock the target row. When changing an admin to a lower role, count remaining database admins under that same serialized transaction and reject if zero would remain. This serializes concurrent demotions of different admins; a target-row lock alone is insufficient. There is no runtime email-based admin resolution; verified bootstrap identities become durable database admins and count normally.

Audit actor ID, target user ID, old role, and new role with `ACTION_ADMIN_ACTION`; never allow a user to update their role through `PATCH /account`.

- [ ] **Step 4: Run admin/security tests**

Run: `python3 -m pytest tests/test_security.py tests/integration/test_auth_flow.py -q`

Expected: supported transitions pass; unauthorized, unknown-role, nonexistent-user, and final-admin cases pass.

---

### Task 8: Build the account settings UI and role controls

**Files:**
- Create: `frontend/src/routes/AccountPage.tsx`
- Create: `frontend/src/tests/AccountPage.test.tsx`
- Modify: `frontend/src/services/auth.tsx`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/routes/AppLayout.tsx`
- Modify: `frontend/src/routes/admin/AdminUsers.tsx`
- Modify: `frontend/src/tests/auth.test.tsx`
- Modify: `frontend/src/tests/AppLayout.test.tsx`
- Modify: `frontend/src/tests/AdminLayoutAccess.test.tsx`

- [ ] **Step 1: Write failing account-page tests**

```tsx
it('shows profile, providers, sessions, and role', async () => {
  renderAccountPage({ role: 'moderator', identities: [googleIdentity], sessions: [currentSession] });
  expect(await screen.findByRole('heading', { name: /account/i })).toBeVisible();
  expect(screen.getByText('Moderator')).toBeVisible();
  expect(screen.getByText('Google')).toBeVisible();
  expect(screen.getByText(/current session/i)).toBeVisible();
});

it('requires typed confirmation before account deletion', async () => {
  renderAccountPage(accountFixture);
  await userEvent.click(screen.getByRole('button', { name: /delete account/i }));
  expect(screen.getByRole('button', { name: /confirm deletion/i })).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/type delete/i), 'DELETE');
  expect(screen.getByRole('button', { name: /confirm deletion/i })).toBeEnabled();
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm --prefix frontend run test -- src/tests/AccountPage.test.tsx src/tests/auth.test.tsx src/tests/AppLayout.test.tsx src/tests/AdminLayoutAccess.test.tsx`

Expected: account page/route and new auth helpers are absent.

- [ ] **Step 3: Extend the auth service without provider-specific duplication**

Add generic methods while preserving `login()` and `loginTwitch()` until all call sites are migrated. Consume the in-memory CSRF token acquisition, Ky unsafe-method hook, and CSRF-aware logout support already implemented in Task 5; do not defer or duplicate that client behavior here:

```ts
type OAuthProvider = 'google' | 'twitch';

type AuthState = {
  user: User | null;
  role: 'user' | 'moderator' | 'admin' | null;
  capabilities: string[];
  refresh: () => Promise<void>;
  loginWith: (provider: OAuthProvider) => void;
  linkProvider: (provider: OAuthProvider) => void;
  logout: () => Promise<void>;
};
```

`refresh()` reloads `/auth/me` after profile changes. `loginWith` navigates to `/auth/login/{provider}`. `linkProvider` uses the authenticated API client to CSRF-protected `POST /account/identities/{provider}/link`, reads `{ authorization_url }`, then assigns `window.location` to that URL; it must not use direct GET navigation or expect a server redirect for link initiation.

- [ ] **Step 4: Implement the authenticated `/account` page**

Render four accessible sections:

1. Profile form with name/avatar and inline validation.
2. Linked providers showing linked state, link action, and unlink action disabled when it is the sole identity.
3. Active sessions with current-session label, revoke action, and “log out other sessions”.
4. Danger zone requiring exact `DELETE` confirmation.

Anonymous users are redirected to `/login?next=/account`. Use query status to announce link success/collision, then remove the query parameter without reloading. After account deletion, clear auth state and navigate to `/`.

- [ ] **Step 5: Add navigation and admin role controls**

Authenticated users receive an Account navigation entry. Admin Users shows a role select containing User, Moderator, and Admin; only admins can mutate it. Moderator must not see admin navigation solely because of hierarchy—gate admin UI on `admin:access`, not `role !== 'user'`.

- [ ] **Step 6: Run frontend tests and type checking**

Run:

```bash
npm --prefix frontend run test -- src/tests/AccountPage.test.tsx src/tests/auth.test.tsx src/tests/AppLayout.test.tsx src/tests/AdminLayoutAccess.test.tsx
npm --prefix frontend run type-check
```

Expected: tests and TypeScript checks pass.

---

### Task 9: Update API contracts, provider setup docs, and deployment validation

**Files:**
- Modify: `docs/authentication.md`
- Modify: `.env.example` if present
- Modify: `.env.prod.example` if present
- Modify: `k8s/api-deployment.yaml`
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`, and `docker-compose.hasanara.yml`
- Modify: `frontend/src/types/generated/api.ts`
- Modify: `docs/api-reference.md` only if its generation instructions change

- [ ] **Step 1: Correct stale authentication documentation**

Document:

- Google app type “Web application”; exact local and production redirect URIs.
- Current Google OIDC scopes and use of `sub`; remove the obsolete “Enable Google+ API” instruction.
- Twitch application registration, exact redirect URI, and `user:read:email` scope.
- Required `SESSION_SECRET`, `FRONTEND_ORIGIN`, CORS, HTTPS, and secure-cookie behavior.
- Server-side single-use OAuth request records, session token hashing, and CSRF `Origin` plus header checks.
- Sign-in versus link flows and collision behavior.
- Profile, identity, session, deletion, and role endpoints.
- Roles `user`, `moderator`, `admin`; plans are not roles.
- Admin bootstrap by immutable `provider:subject`; `ADMIN_EMAILS` is retired and must not grant runtime privileges.
- Account deletion retention: archive records remain, private account data is removed, audit/event references are anonymized.
- Secret rotation procedure: create new provider secret, deploy it, validate login, revoke old secret; session-secret rotation invalidates CSRF tokens and in-progress OAuth compatibility state, while global logout requires session revocation or deletion.

- [ ] **Step 2: Keep environment templates secret-free and explicit**

Templates and supported deployment manifests contain secret-free values/references and exact callback examples. In `.env.example`, use the local callbacks below. In `.env.prod.example`, Kubernetes ConfigMap values, and the production Compose wiring, use `https://api.example.com/auth/callback/google` and `https://api.example.com/auth/callback/twitch` as replace-before-deploy placeholders (never localhost). Keep client IDs/secrets in environment/secret references, not manifest literals; expose `BOOTSTRAP_ADMIN_IDENTITIES` only as an empty ConfigMap/environment placeholder.

```dotenv
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback/google
OAUTH_TWITCH_CLIENT_ID=
OAUTH_TWITCH_CLIENT_SECRET=
OAUTH_TWITCH_REDIRECT_URI=http://localhost:8000/auth/callback/twitch
BOOTSTRAP_ADMIN_IDENTITIES=
```

Never place real credentials in tracked files. In `k8s/api-deployment.yaml`, add config/secret-backed environment entries for both redirect URI variables and `BOOTSTRAP_ADMIN_IDENTITIES` (with the latter sourced from a secret-free ConfigMap key); use the HTTPS API-hostname placeholders above for documented ConfigMap values. In Compose, preserve the existing `env_file` flow and explicitly wire `OAUTH_GOOGLE_REDIRECT_URI`, `OAUTH_TWITCH_REDIRECT_URI`, and `BOOTSTRAP_ADMIN_IDENTITIES` through the API service in `docker-compose.yml`, `docker-compose.prod.yml`, and the `docker-compose.hasanara.yml` deployment overlay as appropriate, so production does not silently inherit local callback URLs. Production examples use `https://` and the deployed API hostname.

- [ ] **Step 3: Regenerate and check OpenAPI types**

Run:

```bash
npm --prefix frontend run api:generate
npm --prefix frontend run api:check
```

Expected: generated types include `/account/*` and `/admin/users/{user_id}/role`; contract check exits zero.

- [ ] **Step 4: Verify docs do not claim retired behavior**

Search the documentation for `Google+ API`, `is_admin flag`, `ADMIN_EMAILS`, `pro role`, `7 days of inactivity`, raw session-token storage, and obsolete quota fields. Replace each claim with behavior verified against `app/settings.py`, `app/policy.py`, and route responses.

---

### Task 10: Full verification and production smoke checklist

**Files:**
- Create: `alembic/versions/20260714_0200_drop_plaintext_session_tokens.py`
- Modify: `tests/test_migrations.py` and every direct session fixture/helper required for the contract cutover.
- Modify only files required to fix failures uncovered by these checks.

- [ ] **Step 1: Run backend auth/account/security suites**

Before creating or applying the contract migration, confirm Tasks 4–5 have converted every runtime session consumer and **every** direct test/fixture session insert, lookup, assertion, helper, and mock from `sessions.token` to `sessions.token_hash` (hashing any raw cookie value at the boundary). This includes migration fixtures and all auth/account/integration tests, not only the newly added tests. Run the following backend suite against `20260714_linked_identities` first; no application or non-legacy-migration test may rely on a plaintext `sessions.token` column.

```bash
python3 -m pytest \
  tests/test_accounts.py \
  tests/test_routes_account.py \
  tests/test_routes_auth.py \
  tests/test_oauth_security.py \
  tests/test_security.py \
  tests/integration/test_auth_flow.py \
  tests/test_migrations.py -q
```

Expected: all pass against the expand revision.

- [ ] **Step 2: Run deletion and ownership regressions**

```bash
python3 -m pytest \
  tests/test_routes_videos.py \
  tests/test_saved_searches.py \
  tests/test_event_privacy.py \
  tests/test_middleware.py -q
```

Expected: all pass.

- [ ] **Step 3: Run frontend and contract checks**

```bash
npm --prefix frontend run test
npm --prefix frontend run type-check
npm --prefix frontend run api:check
```

Expected: all pass.

- [ ] **Step 4: Run the repository verification gate**

Run: `make verify`

Expected: exit zero. If the gate requires services, start only the documented local Compose dependencies and rerun.

- [ ] **Step 5: Perform provider smoke tests in a non-production environment**

For both Google and Twitch verify:

1. New identity creates one user and one identity.
2. Repeat sign-in reuses the same user.
3. Linking the second provider adds identity without creating a second user.
4. Attempting to link an identity owned by another user reports a collision and changes neither account.
5. Unlinking one of two identities succeeds; unlinking the final identity is blocked.
6. Logout invalidates only current session; logout-all invalidates every session.
7. Moderator can access moderation capabilities but not admin routes.
8. Account deletion clears auth and removes private account data.
9. Provider/client secrets and OAuth tokens never appear in logs, redirects, browser storage, or API responses.
10. Replaying an OAuth cookie/state fails after the first callback.
11. A cookie-authenticated unsafe request without the approved Origin and CSRF header fails.
12. Reading `sessions` from PostgreSQL yields only token hashes that cannot be used as cookies.

- [ ] **Step 6: Create, test, and apply the contract migration in an atomic maintenance rollout**

Only after Step 1 confirms all runtime consumers and direct session fixtures are hash-only, create `alembic/versions/20260714_0200_drop_plaintext_session_tokens.py` with `down_revision = "20260714_linked_identities"`. Add its migration test now: starting from `20260714_linked_identities` with hash-only fixtures, it must prove `0200` retains non-null `token_hash`, makes it `NOT NULL`, and removes `sessions.token`; its downgrade may add a nullable `token` column but must invalidate sessions because raw tokens cannot be reconstructed from hashes.

Use an atomic maintenance deployment; **old and new application versions must never overlap**. (1) Enter a maintenance window, stop/drain every old API writer, and verify no old instance can resume traffic. (2) While all writers remain drained, apply `20260714_linked_identities` and run its backfill. (3) Deploy the already-tested converted hash-only consumers while traffic remains closed. (4) Still drained, reconcile/backfill every remaining null `token_hash` from the plaintext `token` (invalidating/deleting any session that cannot be safely backfilled) and reconcile/backfill legacy identities; verify no null hashes or unbackfilled legacy identities remain. (5) Create/test `0200` as above, then apply it; it makes `token_hash` non-null and drops `sessions.token`. (6) Run migration tests and the full backend suite against Alembic head, confirming the final `sessions` table has no plaintext `token`. (7) Reopen traffic only after verification succeeds and the new application reads all backfilled hashes. No legacy callback can create a user after identity backfill because all old writers remain stopped until traffic reopens. No head/full-suite claim is valid before this consumer conversion and contract-migration sequence completes.

Rollback at any point in this hash-only rollout requires invalidating sessions: old consumers cannot authenticate sessions created by the new hash-only writer, and plaintext tokens cannot be reconstructed from hashes. Do not roll back to an old consumer while retaining live hash-only sessions; stop traffic, invalidate sessions, then restore a compatible application/schema explicitly. Do not downgrade identity schema after users have linked multiple providers unless identities are exported and operators accept loss of secondary links.

---

## Self-Review

- **Spec coverage:** Google/Twitch hardening, extensible providers, accounts, linking, profiles, sessions, deletion, and user/moderator/admin are each assigned to a task.
- **Contract safety:** Migration/backfill precedes runtime reads; `/auth/me` compatibility is retained; frontend generated contracts are checked.
- **Security:** State/nonce binding, no email merging, final-identity and final-admin guards, secret hygiene, session revocation, and anonymized audit retention are explicit.
- **YAGNI:** Password auth, provider token persistence, configurable permission editing, and automatic merging are excluded.
- **Execution policy:** No git commit or push is part of this plan; commits require separate explicit authorization.
