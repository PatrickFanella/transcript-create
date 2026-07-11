#!/usr/bin/env bash
# Compatibility wrapper for the scheduled Python maintenance command.

set -euo pipefail

exec python3 "$(dirname "$0")/maintain_event_retention.py"
