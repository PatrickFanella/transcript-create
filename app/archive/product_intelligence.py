"""Citation-backed product intelligence assembled from canonical PostgreSQL search."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from .. import crud
from ..schemas import ArchiveEvidenceMoment, TopicTimelineBucket, TopicTimelineResponse


def _bucket_key(value: datetime, granularity: str) -> tuple[str, str]:
    if granularity == "week":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}", f"Week {iso.week}, {iso.year}"
    return value.strftime("%Y-%m"), value.strftime("%B %Y")


def build_topic_timeline(
    db,
    *,
    slug: str,
    granularity: str,
    date_from: date | None,
    date_to: date | None,
) -> TopicTimelineResponse:
    query = slug.replace("-", " ").strip()
    filters = {
        **({"date_from": date_from.isoformat()} if date_from else {}),
        **({"date_to": date_to.isoformat()} if date_to else {}),
    }
    grouped = crud.get_grouped_search(db, q=query, source="best", limit=200, filters=filters)
    buckets: dict[str, dict] = defaultdict(lambda: {"label": "", "moments": [], "videos": set()})
    for group in grouped.groups:
        uploaded_at = group.video.uploaded_at
        if uploaded_at is None:
            continue
        key, label = _bucket_key(uploaded_at, granularity)
        bucket = buckets[key]
        bucket["label"] = label
        bucket["videos"].add(str(group.video.id))
        for moment in group.moments:
            bucket["moments"].append(
                ArchiveEvidenceMoment(
                    video=group.video,
                    start_ms=moment.start_ms,
                    end_ms=moment.end_ms,
                    snippet=moment.snippet,
                    topic=query,
                )
            )
    return TopicTimelineResponse(
        topic=query,
        granularity=granularity,
        date_from=date_from,
        date_to=date_to,
        buckets=[
            TopicTimelineBucket(
                period=key,
                label=value["label"],
                mention_count=len(value["moments"]),
                episode_count=len(value["videos"]),
                evidence=value["moments"][:5],
            )
            for key, value in sorted(buckets.items())
        ],
    )
