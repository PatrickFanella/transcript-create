"""Canonical persistence operations for accounts, identities, and sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import SessionTransaction

from .audit import ACTION_BOOTSTRAP_ADMIN_PROMOTED, write_audit_event
from .auth.providers import ProviderProfile
from .exceptions import AppError
from .settings import settings


class IdentityConflictError(AppError):
    """An external identity is already linked or cannot be linked twice."""

    def __init__(self) -> None:
        super().__init__("identity_conflict", "This provider identity is already linked", 409)


class LastIdentityError(AppError):
    def __init__(self) -> None:
        super().__init__("last_identity", "An account must retain a login identity", 409)


class FinalAdminError(AppError):
    """Deleting the last active administrator would strand administration."""

    def __init__(self) -> None:
        super().__init__("final_admin", "At least one active admin must remain", 409)


ADMIN_ROLE_MUTATION_LOCK = "hasanara:admin-role-mutation"
_prepared_deletions: set[object] = set()


@dataclass(frozen=True)
class PreparedAccountDeletion:
    """Unforgeable capability bound to one live session transaction."""

    _user_id: str
    _session: object
    _transaction: SessionTransaction
    _token: object


def lock_admin_role_mutation(db) -> None:
    """Serialize admin deletion and role changes for the current transaction."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": ADMIN_ROLE_MUTATION_LOCK},
    )


def prepare_account_deletion(db, user_id: UUID | str) -> PreparedAccountDeletion:
    """Lock and validate an account deletion before its audit record is written."""
    if db.get_nested_transaction() is not None:
        raise RuntimeError("account deletion cannot be prepared inside a savepoint")
    user_id = str(user_id)
    lock_admin_role_mutation(db)
    user = db.execute(text("SELECT id, role FROM users WHERE id=:id FOR UPDATE"), {"id": user_id}).mappings().one()
    if user["role"] == "admin":
        admin_count = db.execute(text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one()
        if admin_count == 1:
            raise FinalAdminError()

    # Lock ownership rows before rewriting them, so a concurrent ownership
    # update cannot commit stale JSON or a dangling foreign key after deletion.
    db.execute(
        text("""
        SELECT id FROM jobs
        WHERE owner_user_id=:id OR meta->>'owner_user_id'=CAST(:id AS text)
        FOR UPDATE
    """),
        {"id": user_id},
    ).all()
    db.execute(
        text("""
        SELECT id FROM source_deletions
        WHERE owner_user_id=:id OR deleted_by_user_id=:id
        FOR UPDATE
    """),
        {"id": user_id},
    ).all()
    transaction = db.get_transaction()
    if transaction is None or not transaction.is_active:
        raise RuntimeError("account deletion requires an active transaction")
    token = object()
    _prepared_deletions.add(token)
    return PreparedAccountDeletion(user_id, db, transaction, token)


def discard_prepared_account_deletion(prepared: PreparedAccountDeletion | object) -> None:
    """Invalidate an unused deletion capability after its transaction aborts."""
    if isinstance(prepared, PreparedAccountDeletion):
        _prepared_deletions.discard(prepared._token)


def delete_prepared_account(db, prepared: PreparedAccountDeletion) -> None:
    """Scrub a previously locked and validated account without committing."""
    if (
        not isinstance(prepared, PreparedAccountDeletion)
        or prepared._token not in _prepared_deletions
        or prepared._session is not db
        or db.get_transaction() is not prepared._transaction
        or not prepared._transaction.is_active
    ):
        raise RuntimeError("invalid or expired prepared account deletion")
    _prepared_deletions.discard(prepared._token)
    user_id = prepared._user_id
    db.execute(text("DELETE FROM sessions WHERE user_id=:id"), {"id": user_id})
    db.execute(
        text("""
        UPDATE api_keys SET revoked_at=now()
        WHERE user_id=:id AND revoked_at IS NULL
    """),
        {"id": user_id},
    )
    db.execute(
        text("""
        UPDATE jobs
        SET owner_user_id=NULL,
            meta=(COALESCE(meta, '{}'::jsonb) - 'owner_user_id' - 'api_key_id')
        WHERE owner_user_id=:id OR meta->>'owner_user_id'=CAST(:id AS text)
    """),
        {"id": user_id},
    )
    db.execute(
        text("""
        UPDATE source_deletions
        SET owner_user_id=CASE WHEN owner_user_id=:id THEN NULL ELSE owner_user_id END,
            deleted_by_user_id=CASE WHEN deleted_by_user_id=:id THEN NULL ELSE deleted_by_user_id END
        WHERE owner_user_id=:id OR deleted_by_user_id=:id
    """),
        {"id": user_id},
    )
    db.execute(
        text("""
        UPDATE audit_logs
        SET user_id=NULL, ip_address=NULL, user_agent=NULL, details='{}'::jsonb
        WHERE user_id=:id
    """),
        {"id": user_id},
    )
    db.execute(
        text("""
        UPDATE events SET user_id=NULL, payload='{}'::jsonb WHERE user_id=:id
    """),
        {"id": user_id},
    )
    db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})


def delete_account(db, user_id: UUID | str) -> None:
    """Safely prepare, scrub, and delete an account without committing."""
    delete_prepared_account(db, prepare_account_deletion(db, user_id))


@dataclass(frozen=True)
class SignInResult:
    user: dict
    created: bool


def session_token_hash(token: str) -> str:
    """Return the only session-token representation permitted in the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db, user_id: UUID | str, *, user_agent: str | None, ip_address: str | None) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        text("""
        INSERT INTO sessions (user_id, token_hash, user_agent, ip_address, expires_at)
        VALUES (:user_id, :token_hash, :user_agent, :ip_address, :expires_at)
    """),
        {
            "user_id": str(user_id),
            "token_hash": session_token_hash(token),
            "user_agent": user_agent,
            "ip_address": ip_address,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_EXPIRE_HOURS),
        },
    )
    return token


def _identity_values(profile: ProviderProfile) -> dict[str, object]:
    return {
        "provider": profile.provider,
        "subject": profile.subject,
        "email": profile.email,
        "email_verified": profile.email_verified,
        "name": profile.name,
        "avatar_url": profile.avatar_url,
    }


def _user(db, user_id: UUID | str) -> dict:
    row = db.execute(text("SELECT * FROM users WHERE id=:user_id"), {"user_id": str(user_id)}).mappings().one()
    return dict(row)


def _is_bootstrap_admin(profile: ProviderProfile) -> bool:
    return f"{profile.provider}:{profile.subject}" in settings.BOOTSTRAP_ADMIN_IDENTITIES


def _promote_bootstrap_admin(db, user_id: UUID | str, profile: ProviderProfile) -> None:
    """Promote exactly once, including identities backfilled before bootstrap."""
    if not _is_bootstrap_admin(profile):
        return
    promoted = (
        db.execute(
            text("""
        UPDATE users SET role='admin', updated_at=now()
        WHERE id=:user_id AND role IN ('user', 'moderator')
        RETURNING id
    """),
            {"user_id": str(user_id)},
        )
        .mappings()
        .first()
    )
    if promoted:
        write_audit_event(
            db,
            ACTION_BOOTSTRAP_ADMIN_PROMOTED,
            user_id=promoted["id"],
            details={"provider": profile.provider},
        )


def sign_in_identity(db, profile: ProviderProfile) -> SignInResult:
    """Resolve an immutable provider subject to one canonical user account."""
    values = _identity_values(profile)
    identity = (
        db.execute(
            text("""
        SELECT user_id FROM user_identities
        WHERE provider=:provider AND subject=:subject
    """),
            values,
        )
        .mappings()
        .first()
    )
    if identity:
        _promote_bootstrap_admin(db, identity["user_id"], profile)
        db.execute(
            text("""
            UPDATE user_identities
            SET provider_email=:email, provider_email_verified=:email_verified,
                provider_name=:name, provider_avatar_url=:avatar_url,
                updated_at=now(), last_login_at=now()
            WHERE provider=:provider AND subject=:subject
        """),
            values,
        )
        db.execute(
            text("""
            UPDATE users
            SET email=COALESCE(email, :email), name=COALESCE(name, :name),
                avatar_url=COALESCE(avatar_url, :avatar_url), updated_at=now()
            WHERE id=:user_id
        """),
            {**values, "user_id": str(identity["user_id"])},
        )
        return SignInResult(_user(db, identity["user_id"]), created=False)

    # The subject uniqueness constraint is the final guard when concurrent
    # callbacks create the same identity.  A savepoint preserves the caller's
    # surrounding transaction when that guard fires.
    try:
        with db.begin_nested():
            user = (
                db.execute(
                    text("""
                INSERT INTO users (email, name, avatar_url, role)
                VALUES (:email, :name, :avatar_url, :role)
                RETURNING *
            """),
                    {**values, "role": "user"},
                )
                .mappings()
                .one()
            )
            db.execute(
                text("""
                INSERT INTO user_identities
                    (user_id, provider, subject, provider_email, provider_email_verified,
                     provider_name, provider_avatar_url, last_login_at)
                VALUES
                    (:user_id, :provider, :subject, :email, :email_verified,
                     :name, :avatar_url, now())
            """),
                {**values, "user_id": str(user["id"])},
            )
            _promote_bootstrap_admin(db, user["id"], profile)
            return SignInResult(_user(db, user["id"]), created=True)
    except IntegrityError:
        identity = (
            db.execute(
                text("""
            SELECT user_id FROM user_identities
            WHERE provider=:provider AND subject=:subject
        """),
                values,
            )
            .mappings()
            .first()
        )
        if not identity:
            raise
        return sign_in_identity(db, profile)


def link_identity(db, user_id: UUID | str, profile: ProviderProfile) -> dict:
    """Link a provider identity to the authenticated canonical account."""
    values = _identity_values(profile)
    existing = (
        db.execute(
            text("""
        SELECT user_id FROM user_identities WHERE provider=:provider AND subject=:subject
    """),
            values,
        )
        .mappings()
        .first()
    )
    if existing:
        raise IdentityConflictError()
    try:
        with db.begin_nested():
            identity = (
                db.execute(
                    text("""
                INSERT INTO user_identities
                    (user_id, provider, subject, provider_email, provider_email_verified,
                     provider_name, provider_avatar_url, last_login_at)
                VALUES
                    (:user_id, :provider, :subject, :email, :email_verified,
                     :name, :avatar_url, now())
                RETURNING *
            """),
                    {**values, "user_id": str(user_id)},
                )
                .mappings()
                .one()
            )
            return dict(identity)
    except IntegrityError as exc:
        # Both identity uniqueness constraints intentionally have the same
        # public result: a provider cannot be linked twice to an account.
        raise IdentityConflictError() from exc


def unlink_identity(db, user_id: UUID | str, provider: str) -> None:
    identities = (
        db.execute(
            text("""
        SELECT id, provider FROM user_identities WHERE user_id=:user_id FOR UPDATE
    """),
            {"user_id": str(user_id)},
        )
        .mappings()
        .all()
    )
    if len(identities) <= 1:
        raise LastIdentityError()
    db.execute(
        text("""
        DELETE FROM user_identities WHERE user_id=:user_id AND provider=:provider
    """),
        {"user_id": str(user_id), "provider": provider},
    )


def list_identities(db, user_id: UUID | str) -> list[dict]:
    rows = (
        db.execute(
            text("""
        SELECT * FROM user_identities WHERE user_id=:user_id ORDER BY created_at
    """),
            {"user_id": str(user_id)},
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def update_profile(db, user_id: UUID | str, *, name: str | None, avatar_url: str | None) -> dict:
    row = (
        db.execute(
            text("""
        UPDATE users SET name=:name, avatar_url=:avatar_url, updated_at=now()
        WHERE id=:user_id RETURNING *
    """),
            {"user_id": str(user_id), "name": name, "avatar_url": avatar_url},
        )
        .mappings()
        .one()
    )
    return dict(row)
