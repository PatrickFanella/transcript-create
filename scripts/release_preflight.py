#!/usr/bin/env python3
"""Validate a HasanAra release without exposing rendered Compose secrets."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn, Sequence

ROOT = Path(__file__).resolve().parent.parent
DIGEST_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ROLES = {"api", "ingest-cuda", "ml-cuda", "frontend", "postgres-walg", "redis"}
SERVICE_ROLES = {
    "db": "postgres-walg",
    "backup": "postgres-walg",
    "migrations": "api",
    "api": "api",
    "analytics-retention": "api",
    "summary-refresher": "api",
    "archive-intelligence-refresher": "api",
    "worker": "ingest-cuda",
    "diarization-worker": "ml-cuda",
    "frontend": "frontend",
    "redis": "redis",
}
RETIRABLE_SERVICES = {"opensearch", "dashboards", "prometheus", "grafana", "diarization-worker"}
BASE_SERVICES = set(SERVICE_ROLES) - {"diarization-worker"}
DATABASE_CLIENTS = {
    "migrations",
    "api",
    "worker",
    "analytics-retention",
    "summary-refresher",
    "archive-intelligence-refresher",
    "diarization-worker",
    "backup",
}
APPLICATION_SERVICES = DATABASE_CLIENTS - {"backup"}
SERVICE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
COMPOSE_ROW_DELIMITER = "\x1f"
COMPOSE_ONEOFF_LABELS = {"", "false", "0", "no"}
URL_UNRESERVED_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.gtx1080.yml",
    "docker-compose.hasanara.yml",
    "docker-compose.storage.yml",
    "docker-compose.pitr.yml",
    "docker-compose.release.yml",
)


class PreflightError(Exception):
    """An intentionally non-sensitive validation error."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Avoid argparse repeating arbitrary operator input in an error message."""

    def error(self, message: str) -> NoReturn:
        del message
        raise PreflightError("invalid arguments")


def fail(message: str) -> NoReturn:
    raise PreflightError(message)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("release manifest is missing or invalid")
    if not isinstance(data, dict) or set(data) != {"schema_version", "source_commit", "images", "services"}:
        fail("release manifest has an invalid schema")
    if (
        data["schema_version"] != 1
        or not isinstance(data["source_commit"], str)
        or not COMMIT_RE.fullmatch(data["source_commit"])
    ):
        fail("release manifest has an invalid schema")
    if not isinstance(data["images"], dict) or set(data["images"]) != IMAGE_ROLES:
        fail("release manifest has an invalid schema")
    if not all(isinstance(value, str) and DIGEST_RE.fullmatch(value) for value in data["images"].values()):
        fail("release manifest has an invalid image reference")
    if data["services"] != SERVICE_ROLES:
        fail("release manifest has an invalid service mapping")
    return data


def run(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def checked(command: Sequence[str], *, cwd: Path, error: str) -> subprocess.CompletedProcess[str]:
    result = run(command, cwd=cwd)
    if result.returncode:
        fail(error)
    return result


def compose_command() -> list[str]:
    command = ["docker", "compose", "--project-name", "hasanara", "--env-file", ".env.prod"]
    for compose_file in COMPOSE_FILES:
        command.extend(("--file", compose_file))
    return command


def repository_head(root: Path) -> str:
    result = checked(("git", "rev-parse", "HEAD"), cwd=root, error="cannot determine source commit")
    head = result.stdout.strip()
    if not COMMIT_RE.fullmatch(head):
        fail("cannot determine source commit")
    return head


def ensure_clean_tree(root: Path, allow_dirty: bool) -> None:
    result = checked(
        ("git", "status", "--porcelain", "--untracked-files=all"), cwd=root, error="cannot inspect source tree"
    )
    if result.stdout and not allow_dirty:
        fail("source tree is dirty; use --allow-dirty only for a local rehearsal")


def active_services(root: Path) -> set[str]:
    result = checked([*compose_command(), "config", "--services"], cwd=root, error="Compose service validation failed")
    services = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    validate_active_service_set(services)
    return services


def validate_active_service_set(services: set[str]) -> None:
    if not BASE_SERVICES <= services or not services <= set(SERVICE_ROLES):
        fail("unsupported Compose profile or active service set")


def project_services(root: Path) -> set[str]:
    result = checked(
        (
            "docker",
            "ps",
            "--all",
            "--filter",
            "label=com.docker.compose.project=hasanara",
            "--format",
            '{{.Label "com.docker.compose.service"}}\x1f{{.Label "com.docker.compose.oneoff"}}',
        ),
        cwd=root,
        error="cannot inspect existing project containers",
    )
    rows = [row.split(COMPOSE_ROW_DELIMITER) for row in result.stdout.splitlines()]
    if any(len(row) != 2 or not SERVICE_LABEL_RE.fullmatch(row[0]) for row in rows):
        fail("existing project containers have invalid service labels")
    services = [row[0] for row in rows]
    if len(services) != len(set(services)) or any(row[1].strip().lower() not in COMPOSE_ONEOFF_LABELS for row in rows):
        fail("existing project containers are not safe for release")
    return set(services)


def validate_project_services(actual: set[str], desired: set[str], allow_disabled_profile_services: bool) -> None:
    allowed = desired | RETIRABLE_SERVICES if allow_disabled_profile_services else desired
    if not actual <= allowed:
        fail("existing project containers do not match desired active services")


def parse_compose_profiles(environment: str) -> None:
    """Accept only Compose's default profile selection or diarization alone."""
    values = [
        line.partition("=")[2] for line in environment.splitlines() if line.partition("=")[0] == "COMPOSE_PROFILES"
    ]
    if len(values) > 1:
        fail("unsupported Compose profile selection")
    value = values[0].strip() if values else ""
    profiles = [] if not value else [profile.strip() for profile in value.split(",")]
    if profiles not in ([], ["diarization"]):
        fail("unsupported Compose profile selection")


def validate_requested_profiles(root: Path) -> None:
    result = checked(
        [*compose_command(), "config", "--environment"], cwd=root, error="Compose profile validation failed"
    )
    parse_compose_profiles(result.stdout)


def validate_rendered_services(rendered: dict[str, Any], services: set[str], manifest: dict[str, Any]) -> None:
    configured = rendered.get("services")
    if not isinstance(configured, dict):
        fail("Compose rendered an invalid service configuration")
    db = configured.get("db")
    db_environment = db.get("environment", {}) if isinstance(db, dict) else {}
    password = db_environment.get("POSTGRES_PASSWORD") if isinstance(db_environment, dict) else None
    if not isinstance(password, str) or password in {"", "postgres"} or not URL_UNRESERVED_RE.fullmatch(password):
        fail("Compose database password contract is invalid")
    expected_database_url = f"postgresql+psycopg://postgres:{password}@db:5432/transcripts"
    for service in services:
        details = configured.get(service)
        expected = manifest["images"][manifest["services"][service]]
        if not isinstance(details, dict) or details.get("image") != expected or "build" in details:
            fail("Compose image does not match the release manifest")
        environment = details.get("environment", {})
        if service in DATABASE_CLIENTS:
            if (
                not isinstance(environment, dict)
                or environment.get("ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION") != "false"
            ):
                fail("Compose migration safety contract is invalid")
            if environment.get("DATABASE_URL") != expected_database_url:
                fail("Compose database URL contract is invalid")
        if service == "backup" and environment.get("PGPASSWORD") != password:
            fail("Compose backup password contract is invalid")
        if service in APPLICATION_SERVICES and (
            environment.get("ENVIRONMENT") != "production" or environment.get("LOG_LEVEL") != "INFO"
        ):
            fail("Compose application environment contract is invalid")


def verify_networks(root: Path) -> None:
    for network in ("management", "dev"):
        checked(
            ("docker", "network", "inspect", network),
            cwd=root,
            error=f"required external network is missing: {network}",
        )


def verify_mount_parents(root: Path) -> None:
    # Docker Compose otherwise creates these host paths silently and can deploy to
    # empty storage after an operator typo.
    for relative in ("docker-volumes/dbdata", "docker-volumes/redis-data", "backups", "data", "cache"):
        if not (root / relative).is_dir():
            fail(f"required mount parent is missing: {relative}")


def validate(root: Path, manifest_path: Path, allow_dirty: bool, allow_disabled_profile_services: bool = False) -> None:
    manifest = load_manifest(manifest_path)
    ensure_clean_tree(root, allow_dirty)
    if repository_head(root) != manifest["source_commit"]:
        fail("release manifest source commit does not match HEAD")
    checked([*compose_command(), "config", "--quiet"], cwd=root, error="Compose configuration validation failed")
    validate_requested_profiles(root)
    services = active_services(root)
    rendered_result = checked(
        [*compose_command(), "config", "--format", "json"], cwd=root, error="Compose rendering failed"
    )
    try:
        rendered = json.loads(rendered_result.stdout)
    except json.JSONDecodeError:
        fail("Compose rendered invalid JSON")
    validate_rendered_services(rendered, services, manifest)
    validate_project_services(project_services(root), services, allow_disabled_profile_services)
    verify_networks(root)
    verify_mount_parents(root)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = SafeArgumentParser(description="Validate a release without printing Compose configuration.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "release-images.json")
    parser.add_argument("--allow-dirty", action="store_true", help="allow a dirty tree for a local rehearsal only")
    parser.add_argument("--allow-disabled-profile-services", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        validate(ROOT, args.manifest, args.allow_dirty, args.allow_disabled_profile_services)
    except PreflightError as error:
        print(f"release preflight failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        # Do not expose values from subprocesses, environment interpolation, or
        # Python tracebacks during a release check.
        print("release preflight failed: internal validation error", file=sys.stderr)
        return 1
    print("release preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
