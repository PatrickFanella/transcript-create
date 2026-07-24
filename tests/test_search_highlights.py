from app.search.highlights import (
    HIGHLIGHT_END,
    HIGHLIGHT_START,
    normalize_highlight_ranges,
    normalize_search_rows,
    parse_highlighted_snippet,
)


def test_parse_highlighted_snippet_returns_plain_text_and_code_point_range():
    marked = f"🚀 {HIGHLIGHT_START}rent{HIGHLIGHT_END} is high"

    snippet, highlights = parse_highlighted_snippet(marked)

    assert snippet == "🚀 rent is high"
    assert highlights == [{"start": 2, "end": 6}]


def test_parse_highlighted_snippet_keeps_hostile_html_literal():
    hostile = '<img src=x onerror=alert(1)><script>alert("x")</script>&lt;encoded&gt;'
    marked = f"{hostile} {HIGHLIGHT_START}rent{HIGHLIGHT_END}"

    snippet, highlights = parse_highlighted_snippet(marked)

    assert snippet == f"{hostile} rent"
    assert highlights == [{"start": len(hostile) + 1, "end": len(hostile) + 5}]


def test_parse_highlighted_snippet_discards_ranges_from_malformed_markers():
    marked = f"left {HIGHLIGHT_START}outer {HIGHLIGHT_START}inner{HIGHLIGHT_END} open"

    snippet, highlights = parse_highlighted_snippet(marked)

    assert snippet == "left outer inner open"
    assert highlights == []
    assert HIGHLIGHT_START not in snippet
    assert HIGHLIGHT_END not in snippet


def test_parse_highlighted_snippet_counts_combining_characters_as_code_points():
    marked = f"e\u0301 {HIGHLIGHT_START}🚀{HIGHLIGHT_END}"

    snippet, highlights = parse_highlighted_snippet(marked)

    assert snippet == "e\u0301 🚀"
    assert highlights == [{"start": 3, "end": 4}]


def test_normalize_highlight_ranges_merges_repeated_and_overlapping_ranges():
    assert normalize_highlight_ranges(
        "abcdefghij",
        [
            {"start": 6, "end": 10},
            {"start": 2, "end": 7},
            {"start": 2, "end": 7},
            {"start": -4, "end": 1},
            {"start": 20, "end": 30},
        ],
    ) == [{"start": 0, "end": 1}, {"start": 2, "end": 10}]


def test_normalize_search_rows_keeps_raw_fallback_literal_with_empty_ranges():
    rows = normalize_search_rows([{"snippet": "literal <em>title</em>", "id": 7}])

    assert rows == [{"snippet": "literal <em>title</em>", "id": 7, "highlights": []}]
