#!/usr/bin/env python3
"""Fail when mypy debt exceeds the checked-in per-file/error-code baseline."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "mypy-baseline.json"
ERROR_PATTERN = re.compile(r"^((?:app|worker)/[^:]+):\d+: error: .* \[([^]]+)]$")


def main() -> int:
    baseline = {key: int(value) for key, value in json.loads(BASELINE_PATH.read_text()).items()}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "app",
            "worker",
            "--no-error-summary",
            "--show-error-codes",
            "--no-pretty",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    counts: Counter[str] = Counter()
    unparsed_errors: list[str] = []
    for line in result.stdout.splitlines():
        if ": error:" not in line:
            continue
        match = ERROR_PATTERN.match(line)
        if match is None:
            unparsed_errors.append(line)
            continue
        counts[f"{match.group(1)}|{match.group(2)}"] += 1

    excess = {
        key: {"current": count, "allowed": baseline.get(key, 0)}
        for key, count in sorted(counts.items())
        if count > baseline.get(key, 0)
    }
    if unparsed_errors or excess:
        if unparsed_errors:
            print("Unparsed mypy errors:", *unparsed_errors, sep="\n  ", file=sys.stderr)
        if excess:
            print("Mypy baseline exceeded:", file=sys.stderr)
            for key, values in excess.items():
                print(f"  {key}: {values['current']} > {values['allowed']}", file=sys.stderr)
        return 1

    remaining = sum(counts.values())
    allowed = sum(baseline.values())
    print(f"Mypy baseline passed: {remaining} current errors, {allowed} maximum ({allowed - remaining} removed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
