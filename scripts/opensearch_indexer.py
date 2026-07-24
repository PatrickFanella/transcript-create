#!/usr/bin/env python3
import json
import socket
import sys
import time
from pathlib import Path
from typing import Iterable, List

import requests
from sqlalchemy import create_engine, text

# Ensure repo root on path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.logging_config import configure_logging, get_logger
from app.settings import settings

# Configure structured logging for scripts
configure_logging(
    service="script.opensearch-indexer",
    level=settings.LOG_LEVEL,
    json_format=(settings.LOG_FORMAT == "json"),
)
logger = get_logger(__name__)
CONSUMER_ID = f"{socket.gethostname()}-{__import__('os').getpid()}"


def ensure_index(name: str, recreate: bool = False):
    url = f"{settings.OPENSEARCH_URL}/{name}"
    # Rich analyzers: english stemming + synonyms, ngrams, edge ngrams, shingles
    mapping = {
        "settings": {
            "index": {"number_of_shards": 1, "number_of_replicas": 0, "max_ngram_diff": 20},
            "analysis": {
                "filter": {
                    "english_stop": {"type": "stop", "stopwords": "_english_"},
                    "english_stemmer": {"type": "stemmer", "language": "english"},
                    "english_possessive_stemmer": {"type": "stemmer", "language": "possessive_english"},
                    "synonyms_index": {"type": "synonym", "expand": True, "synonyms_path": "analysis/synonyms.txt"},
                    "synonyms_query": {
                        "type": "synonym_graph",
                        "expand": True,
                        "synonyms_path": "analysis/synonyms.txt",
                    },
                    "ngram_filter": {"type": "ngram", "min_gram": 3, "max_gram": 8},
                    "edge_ngram_filter": {"type": "edge_ngram", "min_gram": 2, "max_gram": 20},
                },
                "analyzer": {
                    "text_en_index": {
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "asciifolding",
                            "english_possessive_stemmer",
                            "english_stop",
                            "english_stemmer",
                            "synonyms_index",
                        ],
                    },
                    "text_en_query": {
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "asciifolding",
                            "english_possessive_stemmer",
                            "english_stop",
                            "english_stemmer",
                            "synonyms_query",
                        ],
                    },
                    "text_shingle": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "shingle", "english_stop", "english_stemmer"],
                    },
                    "text_ngram": {"tokenizer": "standard", "filter": ["lowercase", "asciifolding", "ngram_filter"]},
                    "text_edge": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "edge_ngram_filter"],
                    },
                },
            },
        },
        "mappings": {
            "properties": {
                "id": {"type": "long"},
                "video_id": {"type": "keyword"},
                "start_ms": {"type": "integer"},
                "end_ms": {"type": "integer"},
                "text": {
                    "type": "text",
                    "analyzer": "text_en_index",
                    "search_analyzer": "text_en_query",
                    "fields": {
                        "shingle": {"type": "text", "analyzer": "text_shingle"},
                        "ngram": {"type": "text", "analyzer": "text_ngram", "search_analyzer": "text_en_query"},
                        "edge": {"type": "text", "analyzer": "text_edge", "search_analyzer": "text_en_query"},
                        "keyword": {"type": "keyword", "ignore_above": 256},
                    },
                },
            }
        },
    }
    r = requests.head(url, timeout=10)
    if r.status_code == 200 and recreate:
        delr = requests.delete(url, timeout=30)
        delr.raise_for_status()
    elif r.status_code == 200:
        return
    r = requests.put(url, json=mapping, timeout=60)
    if r.status_code >= 400:
        logger.error("Index create failed: %s", r.text)
    r.raise_for_status()
    logger.info("Created index %s", name)


def update_index_settings(name: str, new_settings: dict):
    url = f"{settings.OPENSEARCH_URL}/{name}/_settings"
    r = requests.put(url, json={"index": new_settings}, timeout=30)
    r.raise_for_status()
    logger.info("Updated %s settings: %s", name, new_settings)


def gen_bulk_actions(index: str, rows: Iterable[dict]):
    for r in rows:
        yield {"index": {"_index": index, "_id": r["id"]}}
        yield {
            "id": r["id"],
            "video_id": str(r["video_id"]),
            "start_ms": r["start_ms"],
            "end_ms": r["end_ms"],
            "text": r["text"],
        }


def bulk_post(actions: List[dict], retries: int = 5, base_sleep: float = 0.5):
    # NDJSON bulk API with simple backoff on 429/503
    data = "\n".join(json.dumps(a, ensure_ascii=False) for a in actions) + "\n"
    url = f"{settings.OPENSEARCH_URL}/_bulk"
    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, data=data, headers={"Content-Type": "application/x-ndjson"}, timeout=120)
            if r.status_code in (429, 503):
                raise requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
            r.raise_for_status()
            j = r.json()
            if j.get("errors"):
                failures = []
                for item in j.get("items", []):
                    result = next(iter(item.values()), {})
                    status = int(result.get("status", 500))
                    if status not in {200, 201, 404, 409}:
                        failures.append(result)
                if failures:
                    raise requests.HTTPError(f"OpenSearch bulk item failures: {failures[:3]}", response=r)
            return j
        except requests.HTTPError as e:
            last_exc = e
            status = getattr(e.response, "status_code", None)
            if status in (429, 503) and attempt < retries:
                sleep_s = base_sleep * (2**attempt)
                time.sleep(sleep_s)
                continue
            raise
    if last_exc:
        raise last_exc


def claim_outbox(engine, limit: int) -> list[dict]:
    """Lease pending events in a short transaction; indexing happens afterward."""
    with engine.begin() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text("""
                    WITH candidates AS (
                        SELECT id FROM search_index_outbox
                        WHERE processed_at IS NULL AND dead_lettered_at IS NULL
                          AND available_at <= now()
                          AND (locked_at IS NULL OR locked_at < now() - interval '5 minutes')
                        ORDER BY id FOR UPDATE SKIP LOCKED LIMIT :limit
                    )
                    UPDATE search_index_outbox o SET locked_at=now(), locked_by=:consumer
                    FROM candidates c WHERE o.id=c.id
                    RETURNING o.*
                """),
                {"limit": limit, "consumer": CONSUMER_ID},
            )
            .mappings()
            .all()
        ]


def outbox_actions(rows: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for row in rows:
        index = settings.OPENSEARCH_INDEX_NATIVE if row["source"] == "native" else settings.OPENSEARCH_INDEX_YOUTUBE
        metadata = {
            "_index": index,
            "_id": row["document_id"],
            "version": row["version"],
            "version_type": "external_gte",
        }
        if row["operation"] == "delete":
            actions.append({"delete": metadata})
        else:
            actions.extend(({"index": metadata}, row["payload"]))
    return actions


def finish_outbox(engine, rows: list[dict], error: Exception | None = None) -> None:
    ids = [row["id"] for row in rows]
    if not ids:
        return
    with engine.begin() as conn:
        if error is None:
            conn.execute(
                text("""
                    UPDATE search_index_outbox SET processed_at=now(), locked_at=NULL, locked_by=NULL,
                        last_error=NULL WHERE id=ANY(CAST(:ids AS bigint[])) AND locked_by=:consumer
                """),
                {"ids": ids, "consumer": CONSUMER_ID},
            )
            conn.execute(
                text("""
                    INSERT INTO search_index_checkpoints(consumer,last_outbox_id,indexed_at)
                    VALUES (:consumer,:last_id,now())
                    ON CONFLICT (consumer) DO UPDATE SET last_outbox_id=EXCLUDED.last_outbox_id,
                        indexed_at=EXCLUDED.indexed_at, updated_at=now()
                """),
                {"consumer": CONSUMER_ID, "last_id": max(ids)},
            )
        else:
            conn.execute(
                text("""
                    UPDATE search_index_outbox SET attempt_count=attempt_count+1,
                        available_at=now() + make_interval(secs => LEAST(3600, 5 * power(2, attempt_count))::int),
                        last_error=:error, locked_at=NULL, locked_by=NULL,
                        dead_lettered_at=CASE WHEN attempt_count+1 >= 10 THEN now() ELSE NULL END
                    WHERE id=ANY(CAST(:ids AS bigint[])) AND locked_by=:consumer
                """),
                {"ids": ids, "consumer": CONSUMER_ID, "error": str(error)[:2000]},
            )


def process_outbox(engine, batch: int = 1000) -> int:
    rows = claim_outbox(engine, batch)
    if not rows:
        return 0
    try:
        bulk_post(outbox_actions(rows))
    except Exception as exc:
        finish_outbox(engine, rows, error=exc)
        raise
    finish_outbox(engine, rows)
    return len(rows)


def index_table(engine, table: str, index: str, last_id: int, batch: int, bulk_docs: int) -> int:
    with engine.connect() as conn:
        if table == "segments":
            sql = text("""
                SELECT id, video_id, start_ms, end_ms, text
                FROM segments WHERE id > :last_id
                ORDER BY id ASC LIMIT :lim
            """)
        else:
            sql = text("""
                SELECT ys.id, yt.video_id, ys.start_ms, ys.end_ms, ys.text
                FROM youtube_segments ys
                JOIN youtube_transcripts yt ON yt.id = ys.youtube_transcript_id
                WHERE ys.id > :last_id
                ORDER BY ys.id ASC LIMIT :lim
            """)
        rows = conn.execute(sql, {"last_id": last_id, "lim": batch}).mappings().all()
        if not rows:
            return 0
        # Chunk by bulk_docs (each doc corresponds to 2 actions)
        for i in range(0, len(rows), bulk_docs):
            chunk = rows[i : i + bulk_docs]
            actions = list(gen_bulk_actions(index, chunk))
            bulk_post(actions)
        return int(rows[-1]["id"])


def main(
    batch: int = 5000, source: str = "both", bulk_docs: int = 2000, recreate: bool = False, refresh_off: bool = False
):
    assert settings.SEARCH_BACKEND in ("postgres", "opensearch"), "Invalid SEARCH_BACKEND"
    ensure_index(settings.OPENSEARCH_INDEX_NATIVE, recreate=recreate)
    ensure_index(settings.OPENSEARCH_INDEX_YOUTUBE, recreate=recreate)
    eng = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    # Optionally disable refresh for faster bulk indexing
    if refresh_off:
        try:
            update_index_settings(
                settings.OPENSEARCH_INDEX_NATIVE, {"refresh_interval": "-1", "translog.durability": "async"}
            )
            update_index_settings(
                settings.OPENSEARCH_INDEX_YOUTUBE, {"refresh_interval": "-1", "translog.durability": "async"}
            )
        except Exception as e:
            logger.warning("Failed to disable refresh: %s", e)
    while True:
        processed = process_outbox(eng, batch=min(batch, bulk_docs))
        if not processed:
            break
        logger.info("Processed search outbox batch", extra={"count": processed})
    logger.info("Indexing complete")
    # Restore refresh interval
    if refresh_off:
        try:
            update_index_settings(
                settings.OPENSEARCH_INDEX_NATIVE, {"refresh_interval": "1s", "translog.durability": "request"}
            )
            update_index_settings(
                settings.OPENSEARCH_INDEX_YOUTUBE, {"refresh_interval": "1s", "translog.durability": "request"}
            )
        except Exception as e:
            logger.warning("Failed to restore refresh: %s", e)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Index Postgres data into OpenSearch")
    parser.add_argument("--batch", type=int, default=5000, help="Rows to fetch from Postgres per pass")
    parser.add_argument("--source", choices=["native", "youtube", "both"], default="both")
    parser.add_argument("--bulk-docs", type=int, default=2000, help="Documents per bulk request")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate indices before indexing")
    parser.add_argument(
        "--refresh-off", action="store_true", help="Temporarily disable index refresh during bulk indexing"
    )
    args = parser.parse_args()
    main(
        batch=args.batch,
        source=args.source,
        bulk_docs=args.bulk_docs,
        recreate=args.recreate,
        refresh_off=args.refresh_off,
    )
