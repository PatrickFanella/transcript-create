#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
COMPOSE=(docker compose -p hasanara-test -f "${REPO_ROOT}/docker-compose.test.yml")
PYTHON_BIN="${PYTHON_BIN:-python3}"

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

echo 'Starting isolated PostgreSQL, Redis, and OpenSearch services...'
"${COMPOSE[@]}" up -d --wait

export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:${TEST_POSTGRES_PORT:-55432}/hasanara_test"
export ANALYTICS_TEST_DATABASE_URL="${DATABASE_URL}"
export REDIS_URL="redis://localhost:${TEST_REDIS_PORT:-56379}/0"
export OPENSEARCH_URL="http://localhost:${TEST_OPENSEARCH_PORT:-59200}"
export SESSION_SECRET='local-verification-secret'
export FRONTEND_ORIGIN='http://localhost:5173'

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
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run type-check
npm --prefix frontend run test:coverage
npm --prefix frontend run build
npm --prefix frontend run bundle:check
npm --prefix frontend audit --audit-level=high

if [[ "${VERIFY_SKIP_BROWSER:-0}" != "1" ]]; then
  echo 'Running seeded Chromium archive smoke tests...'
  npm --prefix e2e run test:critical
fi

echo 'Verification passed.'
