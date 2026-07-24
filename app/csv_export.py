"""Safe, consistent CSV rendering for spreadsheet-facing exports."""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from collections.abc import Iterable
from typing import Any

_FORMULA_PREFIXES = frozenset("=+-@")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _neutralize_formula(value: str) -> str:
    for character in value:
        if character.isspace() or unicodedata.category(character).startswith("C"):
            continue
        if character in _FORMULA_PREFIXES:
            return "'" + value
        break
    return value


def render_csv(rows: Iterable[Iterable[Any]]) -> str:
    """Render rows with standard CSV quoting and inert spreadsheet cells."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows([_neutralize_formula(_cell_text(cell)) for cell in row] for row in rows)
    return output.getvalue()
