from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import bindparam, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.schemas import ArchiveIntelligenceResponse, ArchivePerson, ArchiveVideoTag, VideoInfo


@dataclass
class _PersonFacet:
    person: ArchivePerson
    count: int = 0
    roles: set[str] = field(default_factory=set)


@dataclass
class _TagFacet:
    tag: ArchiveVideoTag
    count: int = 0


def _video_key(video: VideoInfo) -> str | None:
    return str(video.id) if video.id else None


def _collect_unique_videos(response: ArchiveIntelligenceResponse) -> list[VideoInfo]:
    seen: set[str] = set()
    videos: list[VideoInfo] = []

    def add(video: VideoInfo | None) -> None:
        if video is None:
            return
        key = _video_key(video)
        if key is None or key in seen:
            return
        seen.add(key)
        videos.append(video)

    scoped_count = 0
    for period in response.periods:
        for video in period.videos:
            add(video)
            scoped_count += 1
        for moment in period.evidence:
            add(moment.video)
            scoped_count += 1
        for topic in period.top_topics:
            for moment in topic.evidence:
                add(moment.video)
                scoped_count += 1

    if scoped_count > 0:
        return videos

    for video in response.summary.recent_videos:
        add(video)
    for topic in response.topic_cards:
        for moment in topic.evidence:
            add(moment.video)

    return videos


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _topic_catalog_facets(db, slugs: list[str]) -> tuple[list[ArchivePerson], list[ArchiveVideoTag]]:
    if db is None or not slugs:
        return [], []
    params = {"slugs": slugs}
    try:
        people_rows = (
            db.execute(
                text("""
                SELECT slug, display_name, aliases, description, default_role, sort_order
                FROM archive_people
                WHERE status = 'published' AND slug IN :slugs
                """).bindparams(bindparam("slugs", expanding=True)),
                params,
            )
            .mappings()
            .all()
        )
        tag_rows = (
            db.execute(
                text("""
                SELECT slug, label, kind, description, sort_order
                FROM archive_video_tags
                WHERE status = 'published' AND slug IN :slugs
                """).bindparams(bindparam("slugs", expanding=True)),
                params,
            )
            .mappings()
            .all()
        )
    except (OperationalError, ProgrammingError):
        db.rollback()
        return [], []

    people_by_slug = {
        str(row["slug"]): ArchivePerson(
            slug=str(row["slug"]),
            display_name=str(row["display_name"]),
            aliases=_string_list(row.get("aliases")),
            description=row.get("description"),
            default_role=row.get("default_role"),
            sort_order=int(row.get("sort_order") or 0),
        )
        for row in people_rows
    }
    tags_by_slug = {
        str(row["slug"]): ArchiveVideoTag(
            slug=str(row["slug"]),
            label=str(row["label"]),
            kind=str(row.get("kind") or "category"),
            description=row.get("description"),
            sort_order=int(row.get("sort_order") or 0),
        )
        for row in tag_rows
    }
    return (
        [people_by_slug[slug] for slug in slugs if slug in people_by_slug],
        [tags_by_slug[slug] for slug in slugs if slug in tags_by_slug],
    )


def attach_archive_facets(response: ArchiveIntelligenceResponse, db=None) -> ArchiveIntelligenceResponse:
    people_by_slug: dict[str, _PersonFacet] = {}
    tags_by_slug: dict[str, _TagFacet] = {}

    for video in _collect_unique_videos(response):
        for person in video.people:
            if not person.slug:
                continue
            entry = people_by_slug.setdefault(
                person.slug,
                _PersonFacet(person=person.model_copy(update={"role": None})),
            )
            entry.count += 1
            if person.role:
                entry.roles.add(person.role)

        for tag in video.tags:
            if not tag.slug:
                continue
            entry = tags_by_slug.setdefault(tag.slug, _TagFacet(tag=tag))
            entry.count += 1

    topic_slugs = list(dict.fromkeys(topic.slug for topic in response.topic_cards if topic.slug))
    catalog_people, catalog_tags = _topic_catalog_facets(db, topic_slugs)
    for person in catalog_people:
        people_by_slug.setdefault(person.slug, _PersonFacet(person=person, count=1))
    for tag in catalog_tags:
        tags_by_slug.setdefault(tag.slug, _TagFacet(tag=tag, count=1))

    people = []
    for _slug, entry in sorted(
        people_by_slug.items(),
        key=lambda item: (
            -item[1].count,
            item[1].person.sort_order if item[1].person.sort_order is not None else 0,
            str(item[1].person.display_name).casefold(),
            item[0],
        ),
    )[:12]:
        person = entry.person
        roles = entry.roles
        role = next(iter(roles)) if len(roles) == 1 else None
        people.append(person.model_copy(update={"role": role}))

    tags = [
        entry.tag
        for slug, entry in sorted(
            tags_by_slug.items(),
            key=lambda item: (
                -item[1].count,
                item[1].tag.sort_order if item[1].tag.sort_order is not None else 0,
                str(item[1].tag.label).casefold(),
                item[0],
            ),
        )[:12]
    ]

    return response.model_copy(update={"people": people, "tags": tags})
