#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
COMPOSE=(docker compose -p hasanara-test -f "${REPO_ROOT}/docker-compose.test.yml")
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Export these before Compose or Python can read repository .env settings.
export TEST_POSTGRES_PORT="${TEST_POSTGRES_PORT:-55432}"
export TEST_REDIS_PORT="${TEST_REDIS_PORT:-56379}"
export TEST_OPENSEARCH_PORT="${TEST_OPENSEARCH_PORT:-59200}"
export TEST_SERVICE_HOST="${TEST_SERVICE_HOST:-localhost}"
export ENVIRONMENT='test'
export SEARCH_BACKEND='postgres'
export SESSION_SECRET='verify-session-secret-2026-9d7f3a1c'
export ANALYTICS_HMAC_SECRET='verify-analytics-hmac-secret-2026-4b8e2d6f'
export FRONTEND_ORIGIN='http://localhost:5173'
# The repository-owned isolated database deliberately exercises the one-time
# destructive session contract migration.
export ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION='true'

cleanup() {
  local status=$?
  if [[ "${KEEP_TEST_SERVICES:-0}" != "1" ]]; then
    "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "${status}"
}

check_dependencies() {
  local -a missing=()
  local command_name
  for command_name in docker npm "${PYTHON_BIN}"; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      missing+=("${command_name}")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    printf 'Missing verification dependencies: %s\n' "${missing[*]}" >&2
    return 1
  fi

  local python_version
  python_version="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "${python_version}" != '3.11' ]]; then
    printf 'Python 3.11 is required; %s reports %s. Set PYTHON_BIN to a Python 3.11 executable.\n' \
      "${PYTHON_BIN}" "${python_version}" >&2
    return 1
  fi
}

trap cleanup EXIT INT TERM
check_dependencies
cd "${REPO_ROOT}"

echo 'Validating documentation and retired product contracts...'
"${PYTHON_BIN}" scripts/check_documentation.py

echo 'Starting isolated PostgreSQL, Redis, and OpenSearch services...'
"${COMPOSE[@]}" up -d --wait

export DATABASE_URL="postgresql+psycopg://postgres:postgres@${TEST_SERVICE_HOST}:${TEST_POSTGRES_PORT}/hasanara_test"
export ANALYTICS_TEST_DATABASE_URL="${DATABASE_URL}"
export REDIS_URL="redis://${TEST_SERVICE_HOST}:${TEST_REDIS_PORT}/0"
export OPENSEARCH_URL="http://${TEST_SERVICE_HOST}:${TEST_OPENSEARCH_PORT}"

echo 'Applying the complete Alembic migration history...'
"${PYTHON_BIN}" -m alembic upgrade head

echo 'Running backend verification...'
"${PYTHON_BIN}" -m compileall -q app worker scripts
"${PYTHON_BIN}" -m ruff check app worker scripts
"${PYTHON_BIN}" -m black --check app worker scripts
"${PYTHON_BIN}" -m isort --check-only app worker
"${PYTHON_BIN}" scripts/check_mypy_baseline.py
"${PYTHON_BIN}" -m pytest tests --cov=app --cov-report=term --cov-fail-under=70 -q
"${PYTHON_BIN}" -m bandit -r app worker -lll -ii -f screen

read -r -a pip_audit_ignores <<< "$("${PYTHON_BIN}" scripts/check_security_exceptions.py --pip-audit-args)"
"${PYTHON_BIN}" -m pip_audit --local --desc --skip-editable "${pip_audit_ignores[@]}"

echo 'Running frontend verification...'
npm --prefix frontend run api:check
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run type-check
npm --prefix frontend run test:coverage
npm --prefix frontend run build
npm --prefix frontend run bundle:check
"${PYTHON_BIN}" scripts/check_security_exceptions.py --npm-audit --package-dir frontend

if [[ "${VERIFY_SKIP_BROWSER:-0}" != "1" ]]; then
  echo 'Running seeded Chromium archive smoke tests...'
  npm --prefix e2e run test:critical
fi

echo 'Verification passed.'
