"""Fail a container build when the speech/diarization runtime is inconsistent."""

from __future__ import annotations

import os
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REQUIRED_DISTRIBUTIONS = {"torch", "torchaudio", "torchcodec", "pyannote.audio"}


def expected_versions() -> dict[str, str]:
    manifest = Path(os.environ.get("ML_RUNTIME_MANIFEST", "requirements-ml-runtime.txt"))
    pins = {
        name: pinned_version
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for name, separator, pinned_version in (line.partition("=="),)
        if separator
    }
    if set(pins) != REQUIRED_DISTRIBUTIONS:
        raise SystemExit(f"ML runtime manifest must pin exactly {sorted(REQUIRED_DISTRIBUTIONS)}")
    return pins


def base_version(distribution: str) -> str:
    """Return a package version without a wheel-local accelerator suffix."""
    try:
        return version(distribution).split("+", 1)[0]
    except PackageNotFoundError as exc:
        raise SystemExit(f"missing required ML package: {distribution}") from exc


def main() -> None:
    expected_by_distribution = expected_versions()
    mismatches = {
        distribution: (expected_version, base_version(distribution))
        for distribution, expected_version in expected_by_distribution.items()
        if base_version(distribution) != expected_version
    }
    if mismatches:
        details = ", ".join(
            f"{name}: expected {expected}, found {actual}" for name, (expected, actual) in sorted(mismatches.items())
        )
        raise SystemExit(f"incompatible ML runtime: {details}")

    # Import every package after version validation so shared-library and ABI
    # failures stop the image build instead of first surfacing in a worker.
    for module_name in ("torch", "torchaudio", "torchcodec", "pyannote.audio"):
        import_module(module_name)

    print("ML runtime imports and versions are compatible")


if __name__ == "__main__":
    main()
