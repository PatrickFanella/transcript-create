"""Render complete mention collections in portable formats."""

from __future__ import annotations

import json
from urllib.parse import quote

from .. import crud
from ..csv_export import render_csv


def mention_collection(db, *, q: str, source: str, video_id: str | None, limit: int, filters: dict) -> list[dict]:
    grouped = crud.get_grouped_search(db, q=q, source=source, video_id=video_id, limit=limit, filters=filters)
    return [
        {
            "video_id": str(group.video.id),
            "youtube_id": group.video.youtube_id,
            "video_title": group.video.title or "Untitled VOD",
            "start_ms": moment.start_ms,
            "end_ms": moment.end_ms,
            "snippet": moment.snippet,
            "source": moment.source,
            "deep_link": f"/v/{group.video.id}?t={moment.start_ms // 1000}",
        }
        for group in grouped.groups
        for moment in group.moments
    ]


def render_mention_export(items: list[dict], *, format: str, frontend_origin: str) -> tuple[str, str]:
    if format == "json":
        return json.dumps({"items": items}, ensure_ascii=False, default=str), "application/json"
    if format == "csv":
        rows = [["video_id", "video_title", "start_ms", "end_ms", "snippet", "source", "deep_link"]]
        rows.extend(
            [
                item["video_id"],
                item["video_title"],
                item["start_ms"],
                item["end_ms"],
                item["snippet"],
                item["source"],
                frontend_origin.rstrip("/") + item["deep_link"],
            ]
            for item in items
        )
        return render_csv(rows), "text/csv; charset=utf-8"
    lines = ["#EXTM3U"]
    for item in items:
        title = str(item["video_title"]).replace("\n", " ").replace("\r", " ")
        lines.append(f"#EXTINF:-1,{title} @ {item['start_ms'] // 1000}s")
        lines.append(frontend_origin.rstrip("/") + item["deep_link"] + f"&q={quote(str(item['snippet'])[:120])}")
    return "\n".join(lines) + "\n", "audio/x-mpegurl; charset=utf-8"
