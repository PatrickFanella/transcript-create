#!/usr/bin/env python3
"""Fail-closed validation for cross-browser workflow success evidence."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EVIDENCE_KEYS = (
    "firefox-desktop",
    "webkit-desktop",
    "chromium-mobile",
    "webkit-mobile",
)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def validate_browser_evidence(root: Path, expected_sha: str) -> None:
    """Validate that *root* contains exactly one matching marker per browser leg."""
    if not SHA_PATTERN.fullmatch(expected_sha):
        raise ValueError("expected SHA must be a lowercase 40-character hexadecimal Git commit")
    if root.is_symlink():
        raise ValueError("evidence root must not be a symlink")
    if not root.is_dir():
        raise ValueError("evidence root must be an existing directory")

    root_entries = {entry.name: entry for entry in root.iterdir()}
    if set(root_entries) != set(EVIDENCE_KEYS):
        raise ValueError("evidence root must contain exactly the expected evidence directories")

    for key in EVIDENCE_KEYS:
        evidence_dir = root_entries[key]
        if evidence_dir.is_symlink():
            raise ValueError(f"evidence directory {key!r} must not be a symlink")
        if not evidence_dir.is_dir():
            raise ValueError(f"evidence entry {key!r} must be a directory")

        marker_name = f"{key}.sha"
        entries = {entry.name: entry for entry in evidence_dir.iterdir()}
        if set(entries) != {marker_name}:
            raise ValueError(f"evidence directory {key!r} must contain exactly {marker_name!r}")
        marker = entries[marker_name]
        if marker.is_symlink() or not marker.is_file():
            raise ValueError(f"marker {marker_name!r} must be a regular file, not a symlink")
        if marker.read_text(encoding="ascii") != expected_sha:
            raise ValueError(f"marker {marker_name!r} does not match the expected SHA")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="downloaded evidence root directory")
    parser.add_argument("--sha", required=True, help="expected lowercase 40-character Git commit SHA")
    args = parser.parse_args(argv)
    try:
        validate_browser_evidence(args.root, args.sha)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"browser evidence validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
