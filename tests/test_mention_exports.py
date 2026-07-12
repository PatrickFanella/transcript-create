import csv
import io
from types import SimpleNamespace

from app.search.mention_exports import mention_collection, render_mention_export


def test_mention_collection_forwards_video_id_separately(monkeypatch):
    video = SimpleNamespace(id="video-1", youtube_id="yt-1", title="Episode")
    moment = SimpleNamespace(start_ms=12000, end_ms=18000, snippet="rent", source="whisper")
    captured = {}

    def grouped_search(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(groups=[SimpleNamespace(video=video, moments=[moment])])

    monkeypatch.setattr("app.search.mention_exports.crud.get_grouped_search", grouped_search)
    items = mention_collection(
        object(),
        q="rent",
        source="best",
        video_id="video-1",
        limit=5000,
        filters={"date_from": "2026-01-01"},
    )

    assert captured["video_id"] == "video-1"
    assert captured["filters"] == {"date_from": "2026-01-01"}
    assert items[0]["deep_link"] == "/v/video-1?t=12"


def test_exports_are_portable_and_csv_formula_safe():
    items = [
        {
            "video_id": "video-1",
            "youtube_id": "yt-1",
            "video_title": '=IMPORTXML("bad")',
            "start_ms": 12000,
            "end_ms": 18000,
            "snippet": "+malicious, but quoted",
            "source": "whisper",
            "deep_link": "/v/video-1?t=12",
        }
    ]

    csv_body, csv_type = render_mention_export(items, format="csv", frontend_origin="https://archive.example")
    rows = list(csv.reader(io.StringIO(csv_body)))
    assert rows[1][1].startswith("'=")
    assert rows[1][4].startswith("'+")
    assert rows[1][-1] == "https://archive.example/v/video-1?t=12"
    assert csv_type.startswith("text/csv")

    m3u_body, m3u_type = render_mention_export(items, format="m3u", frontend_origin="https://archive.example/")
    assert m3u_body.startswith("#EXTM3U\n")
    assert "https://archive.example/v/video-1?t=12&q=" in m3u_body
    assert m3u_type.startswith("audio/x-mpegurl")

    json_body, json_type = render_mention_export(items, format="json", frontend_origin="https://archive.example")
    assert '"video_id": "video-1"' in json_body
    assert json_type == "application/json"
