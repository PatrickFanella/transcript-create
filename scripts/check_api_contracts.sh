#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMP_DIR="$(mktemp -d "${ROOT}/frontend/.contract-check.XXXXXX")"
trap 'rm -rf "${TMP_DIR}"' EXIT

cd "${ROOT}"
"${PYTHON_BIN}" scripts/generate_openapi.py "${TMP_DIR}/openapi.json"
"${ROOT}/frontend/node_modules/.bin/openapi-typescript" "${TMP_DIR}/openapi.json" \
  --output "${TMP_DIR}/api.ts"
"${ROOT}/frontend/node_modules/.bin/prettier" --write "${TMP_DIR}/api.ts" >/dev/null

cmp --silent "${TMP_DIR}/openapi.json" docs/api/openapi.json || {
  echo 'OpenAPI document drifted; run npm --prefix frontend run api:generate.' >&2
  exit 1
}
cmp --silent "${TMP_DIR}/api.ts" frontend/src/types/generated/api.ts || {
  echo 'Generated frontend API types drifted; run npm --prefix frontend run api:generate.' >&2
  exit 1
}
