#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <image-ref>" >&2
    exit 2
fi

IMAGE_REF="$1"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.72.0}"
TRIVY_CACHE_DIR="${TRIVY_CACHE_DIR:-${TMPDIR:-/tmp}/hasanara-trivy-cache}"
mkdir -p "${TRIVY_CACHE_DIR}"

docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${TRIVY_CACHE_DIR}:/root/.cache/" \
    "${TRIVY_IMAGE}" image \
    --scanners vuln \
    --pkg-types library \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    "${IMAGE_REF}"
