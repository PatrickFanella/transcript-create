"""Safe adapters for backend-provided search highlighting.

Search engines insert private sentinel strings around matches.  This module is
the only boundary that interprets those strings; API consumers receive the
original text plus Unicode-code-point offsets, never engine-generated markup.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypedDict


class HighlightRange(TypedDict):
    start: int
    end: int


# Private-use code points plus a long application-specific token make accidental
# collisions with transcript text vanishingly unlikely.  The values contain no
# HTML metacharacters, so even an unexpected parser failure cannot create markup.
HIGHLIGHT_START = "\ue000hasanara-search-5d8f3b9a-start\ue001"
HIGHLIGHT_END = "\ue000hasanara-search-5d8f3b9a-end\ue001"

POSTGRES_HEADLINE_OPTIONS = f"StartSel={HIGHLIGHT_START}, StopSel={HIGHLIGHT_END}"


def normalize_highlight_ranges(snippet: str, ranges: Iterable[Mapping[str, Any]]) -> list[HighlightRange]:
    """Clamp, sort, and merge half-open ranges for ``snippet``."""

    normalized: list[HighlightRange] = []
    for candidate in ranges:
        try:
            start = int(candidate["start"])
            end = int(candidate["end"])
        except (KeyError, TypeError, ValueError):
            continue
        start = max(0, min(start, len(snippet)))
        end = max(0, min(end, len(snippet)))
        if end <= start:
            continue
        normalized.append({"start": start, "end": end})

    normalized.sort(key=lambda item: (item["start"], item["end"]))
    merged: list[HighlightRange] = []
    for candidate in normalized:
        if not merged or candidate["start"] > merged[-1]["end"]:
            merged.append(candidate)
            continue
        merged[-1]["end"] = max(merged[-1]["end"], candidate["end"])
    return merged


def parse_highlighted_snippet(
    value: object,
    *,
    start_marker: str = HIGHLIGHT_START,
    end_marker: str = HIGHLIGHT_END,
) -> tuple[str, list[HighlightRange]]:
    """Remove engine sentinels and return plain text with safe match ranges.

    Offsets use Python's Unicode string indexing, which is defined in code
    points and therefore matches the public API contract.  Unmatched and nested
    sentinels are removed without inventing a highlight range.
    """

    marked = "" if value is None else str(value)
    if not start_marker or not end_marker or start_marker == end_marker:
        return marked, []

    output: list[str] = []
    highlights: list[HighlightRange] = []
    active_start: int | None = None
    malformed = False
    output_length = 0
    cursor = 0

    while cursor < len(marked):
        next_start = marked.find(start_marker, cursor)
        next_end = marked.find(end_marker, cursor)
        candidates = [position for position in (next_start, next_end) if position >= 0]
        if not candidates:
            tail = marked[cursor:]
            output.append(tail)
            output_length += len(tail)
            break

        marker_at = min(candidates)
        chunk = marked[cursor:marker_at]
        output.append(chunk)
        output_length += len(chunk)

        if marker_at == next_start:
            # A second start before a close makes the marker stream malformed.
            # Keep the text, but conservatively discard all derived ranges.
            if active_start is not None:
                malformed = True
            active_start = output_length
            cursor = marker_at + len(start_marker)
        else:
            if active_start is not None and output_length > active_start:
                highlights.append({"start": active_start, "end": output_length})
            elif active_start is None:
                malformed = True
            active_start = None
            cursor = marker_at + len(end_marker)

    snippet = "".join(output)
    if active_start is not None:
        malformed = True
    if malformed:
        return snippet, []
    return snippet, normalize_highlight_ranges(snippet, highlights)


def normalize_search_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize repository mappings at the database adapter boundary."""

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        existing_ranges = normalized.get("highlights") or []
        snippet, highlights = parse_highlighted_snippet(normalized.get("snippet"))
        normalized["snippet"] = snippet
        normalized["highlights"] = normalize_highlight_ranges(snippet, [*highlights, *existing_ranges])
        normalized_rows.append(normalized)
    return normalized_rows
