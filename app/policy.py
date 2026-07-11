"""Central authorization vocabulary for roles, capabilities, plans, and API keys."""

from __future__ import annotations

from typing import Mapping

ROLE_USER = "user"
ROLE_PRO = "pro"
ROLE_ADMIN = "admin"

ROLE_HIERARCHY = {ROLE_USER: 0, ROLE_PRO: 1, ROLE_ADMIN: 2}

CAP_SEARCH_READ = "search:read"
CAP_VIDEOS_READ = "videos:read"
CAP_EXPORTS_READ = "exports:read"
CAP_JOBS_READ = "jobs:read"
CAP_JOBS_WRITE = "jobs:write"
CAP_VOCABULARIES_READ = "vocabularies:read"
CAP_VOCABULARIES_WRITE = "vocabularies:write"
CAP_VOCABULARIES_GLOBAL = "vocabularies:global"
CAP_ADMIN_ACCESS = "admin:access"

API_KEY_SCOPES = frozenset({CAP_SEARCH_READ, CAP_VIDEOS_READ, CAP_EXPORTS_READ, CAP_JOBS_READ, CAP_JOBS_WRITE})
DEFAULT_API_KEY_SCOPES = tuple(sorted(API_KEY_SCOPES))


def required_api_key_scope(method: str, path: str) -> str | None:
    """Map stable API surfaces to their least-privilege key scope."""
    method = method.upper()
    if path.startswith("/search"):
        return CAP_SEARCH_READ
    if path.startswith("/videos"):
        return CAP_VIDEOS_READ if method == "GET" else CAP_JOBS_WRITE
    if path.startswith("/exports"):
        return CAP_EXPORTS_READ
    if path.startswith("/jobs"):
        return CAP_JOBS_READ if method == "GET" else CAP_JOBS_WRITE
    return None


BASE_CAPABILITIES = frozenset(
    {
        CAP_SEARCH_READ,
        CAP_VIDEOS_READ,
        CAP_EXPORTS_READ,
        CAP_JOBS_READ,
        CAP_JOBS_WRITE,
        CAP_VOCABULARIES_READ,
        CAP_VOCABULARIES_WRITE,
    }
)

ROLE_CAPABILITIES = {
    ROLE_USER: BASE_CAPABILITIES,
    ROLE_PRO: BASE_CAPABILITIES,
    ROLE_ADMIN: BASE_CAPABILITIES | {CAP_VOCABULARIES_GLOBAL, CAP_ADMIN_ACCESS},
}

PLAN_ENTITLEMENTS = {
    "free": frozenset({"archive", "search", "exports", "jobs", "vocabularies"}),
    "pro": frozenset({"archive", "search", "exports", "jobs", "vocabularies"}),
    "admin": frozenset({"archive", "search", "exports", "jobs", "vocabularies", "admin"}),
}


def resolve_role(user: Mapping[str, object] | None, *, configured_admin: bool = False) -> str:
    """Resolve the effective role without allowing an unknown stored role to escalate."""
    if not user:
        return ROLE_USER
    stored_role = str(user.get("role") or "").lower()
    if configured_admin or stored_role == ROLE_ADMIN:
        return ROLE_ADMIN
    if stored_role == ROLE_PRO or str(user.get("plan") or "free").lower() == "pro":
        return ROLE_PRO
    return ROLE_USER


def capabilities_for_role(role: str) -> tuple[str, ...]:
    return tuple(sorted(ROLE_CAPABILITIES.get(role, ROLE_CAPABILITIES[ROLE_USER])))


def capabilities_for_user(user: Mapping[str, object] | None, *, configured_admin: bool = False) -> tuple[str, ...]:
    return capabilities_for_role(resolve_role(user, configured_admin=configured_admin))


def plan_entitlements(plan: str | None) -> tuple[str, ...]:
    normalized = str(plan or "free").lower()
    return tuple(sorted(PLAN_ENTITLEMENTS.get(normalized, PLAN_ENTITLEMENTS["free"])))
