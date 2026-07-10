"""Public model validation contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from transcript_create_client.models import HighlightRange, SearchHit


@pytest.mark.parametrize(("start", "end"), [(3, 3), (4, 3)])
def test_highlight_range_requires_positive_width(start: int, end: int) -> None:
    with pytest.raises(ValidationError, match="greater than start"):
        HighlightRange(start=start, end=end)


@pytest.mark.parametrize("snippet", ["A\U0001f600B", "e\u0301"])
def test_search_hit_rejects_highlight_beyond_unicode_code_point_length(snippet: str) -> None:
    with pytest.raises(ValidationError, match="snippet length"):
        SearchHit(
            id=1,
            video_id=uuid4(),
            start_ms=0,
            end_ms=1000,
            snippet=snippet,
            highlights=[{"start": 0, "end": len(snippet) + 1}],
        )


def test_search_hit_defaults_to_no_highlights() -> None:
    hit = SearchHit(
        id=1,
        video_id=uuid4(),
        start_ms=0,
        end_ms=1000,
        snippet="plain text",
    )

    assert hit.highlights == []
