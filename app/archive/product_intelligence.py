"""Citation-backed product intelligence assembled from canonical PostgreSQL search."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Literal

from sqlalchemy import text

from .. import crud
from ..schemas import (
    ArchiveEvidenceMoment,
    QuotedMoment,
    QuotedMomentsResponse,
    RelatedEpisode,
    RelatedEpisodesResponse,
    TopicTimelineBucket,
    TopicTimelineResponse,
)


def _bucket_key(value: datetime, granularity: Literal["week", "month"]) -> tuple[str, str]:
    if granularity == "week":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}", f"Week {iso.week}, {iso.year}"
    return value.strftime("%Y-%m"), value.strftime("%B %Y")


def build_topic_timeline(
    db,
    *,
    slug: str,
    granularity: Literal["week", "month"],
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


def related_episodes(db, *, video_id, limit: int = 8) -> RelatedEpisodesResponse:
    rows = (
        db.execute(
            text("""
            WITH source_features AS (
                SELECT 'tag:' || t.slug AS feature, 'Shared tag: ' || t.label AS reason
                FROM archive_video_taggings vt JOIN archive_video_tags t ON t.id=vt.tag_id
                WHERE vt.video_id=:video_id AND t.status='published'
                UNION ALL
                SELECT 'person:' || p.slug, 'Shared person: ' || p.display_name
                FROM archive_video_people vp JOIN archive_people p ON p.id=vp.person_id
                WHERE vp.video_id=:video_id AND p.status='published'
            ), candidate_features AS (
                SELECT vt.video_id, 'tag:' || t.slug AS feature
                FROM archive_video_taggings vt JOIN archive_video_tags t ON t.id=vt.tag_id
                WHERE vt.video_id<>:video_id AND t.status='published'
                UNION ALL
                SELECT vp.video_id, 'person:' || p.slug
                FROM archive_video_people vp JOIN archive_people p ON p.id=vp.person_id
                WHERE vp.video_id<>:video_id AND p.status='published'
            )
            SELECT cf.video_id, COUNT(*)::float AS score, array_agg(sf.reason ORDER BY sf.reason) AS reasons
            FROM candidate_features cf JOIN source_features sf USING(feature)
            GROUP BY cf.video_id ORDER BY score DESC, cf.video_id LIMIT :limit
        """),
            {"video_id": video_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    videos = {str(video.id): video for video in crud.get_videos_by_ids(db, [str(row["video_id"]) for row in rows])}
    return RelatedEpisodesResponse(
        items=[
            RelatedEpisode(video=videos[str(row["video_id"])], score=float(row["score"]), reasons=list(row["reasons"]))
            for row in rows
            if str(row["video_id"]) in videos
        ]
    )


def quoted_moments(db, *, video_id, limit: int = 10) -> QuotedMomentsResponse:
    rows = (
        db.execute(
            text("""
            SELECT start_ms,end_ms,COALESCE(NULLIF(text,''),'Saved transcript moment') AS snippet,
                   COUNT(*)::int AS quote_count
            FROM favorites WHERE video_id=:video_id
            GROUP BY start_ms,end_ms,COALESCE(NULLIF(text,''),'Saved transcript moment')
            ORDER BY quote_count DESC,start_ms ASC LIMIT :limit
        """),
            {"video_id": video_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return QuotedMomentsResponse(
        video_id=video_id,
        items=[QuotedMoment(**dict(row)) for row in rows],
    )
