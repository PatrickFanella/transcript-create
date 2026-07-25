"""Tests for the fail-closed cross-browser evidence validator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "validate_browser_evidence.py"
SPEC = importlib.util.spec_from_file_location("validate_browser_evidence", SCRIPT_PATH)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

SHA = "a" * 40


def valid_tree(tmp_path: Path) -> Path:
    root = tmp_path / "cross-browser-evidence"
    for key in validator.EVIDENCE_KEYS:
        evidence_dir = root / key
        evidence_dir.mkdir(parents=True)
        (evidence_dir / f"{key}.sha").write_text(SHA, encoding="ascii")
    return root


def test_validate_browser_evidence_accepts_valid_tree(tmp_path: Path) -> None:
    validator.validate_browser_evidence(valid_tree(tmp_path), SHA)


def test_validate_browser_evidence_rejects_missing_marker(tmp_path: Path) -> None:
    root = valid_tree(tmp_path)
    (root / "firefox-desktop" / "firefox-desktop.sha").unlink()
    with pytest.raises(ValueError, match="exactly"):
        validator.validate_browser_evidence(root, SHA)


def test_validate_browser_evidence_rejects_wrong_sha(tmp_path: Path) -> None:
    root = valid_tree(tmp_path)
    (root / "firefox-desktop" / "firefox-desktop.sha").write_text("b" * 40, encoding="ascii")
    with pytest.raises(ValueError, match="does not match"):
        validator.validate_browser_evidence(root, SHA)


def test_validate_browser_evidence_rejects_extra_file(tmp_path: Path) -> None:
    root = valid_tree(tmp_path)
    (root / "extra").write_text("unexpected", encoding="ascii")
    with pytest.raises(ValueError, match="exactly"):
        validator.validate_browser_evidence(root, SHA)


def test_validate_browser_evidence_rejects_extra_directory(tmp_path: Path) -> None:
    root = valid_tree(tmp_path)
    (root / "extra").mkdir()
    with pytest.raises(ValueError, match="exactly"):
        validator.validate_browser_evidence(root, SHA)


def test_validate_browser_evidence_rejects_marker_symlink(tmp_path: Path) -> None:
    root = valid_tree(tmp_path)
    marker = root / "firefox-desktop" / "firefox-desktop.sha"
    target = tmp_path / "marker"
    target.write_text(SHA, encoding="ascii")
    marker.unlink()
    marker.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        validator.validate_browser_evidence(root, SHA)


def test_validate_browser_evidence_rejects_evidence_directory_symlink(tmp_path: Path) -> None:
    root = valid_tree(tmp_path)
    evidence_dir = root / "firefox-desktop"
    target = tmp_path / "evidence"
    target.mkdir()
    (target / "firefox-desktop.sha").write_text(SHA, encoding="ascii")
    for child in evidence_dir.iterdir():
        child.unlink()
    evidence_dir.rmdir()
    evidence_dir.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        validator.validate_browser_evidence(root, SHA)


def test_validate_browser_evidence_rejects_invalid_sha(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SHA"):
        validator.validate_browser_evidence(valid_tree(tmp_path), "not-a-sha")


@pytest.mark.parametrize("root_kind", ("missing", "file"))
def test_validate_browser_evidence_rejects_non_directory_or_missing_root(tmp_path: Path, root_kind: str) -> None:
    root = tmp_path / "cross-browser-evidence"
    if root_kind == "file":
        root.write_text("not a directory", encoding="ascii")
    with pytest.raises(ValueError, match="existing directory"):
        validator.validate_browser_evidence(root, SHA)
