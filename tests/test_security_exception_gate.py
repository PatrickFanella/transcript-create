from datetime import date
from pathlib import Path

import pytest

from scripts.check_security_exceptions import reachable_calls, validate


def test_reachability_finds_qualified_and_aliased_calls(tmp_path: Path) -> None:
    (tmp_path / "qualified.py").write_text("import torch\ntorch.jit.script(lambda: None)\n")
    (tmp_path / "aliased.py").write_text(
        "from torch.jit import script as compile_script\ncompile_script(lambda: None)\n"
    )

    findings = reachable_calls((tmp_path,))

    assert len(findings) == 2
    assert all("torch.jit.script" in finding for finding in findings)


def test_exception_fails_on_utc_expiry(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="expired 2026-08-09"):
        validate(today=date(2026, 8, 9), roots=(tmp_path,))


def test_exception_is_valid_before_expiry_when_unreachable(tmp_path: Path) -> None:
    (tmp_path / "safe.py").write_text("import torch\nprint(torch.__version__)\n")

    validate(today=date(2026, 8, 8), roots=(tmp_path,))
