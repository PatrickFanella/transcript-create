import uuid

from sqlalchemy import text

from app.search.outbox import search_freshness


def test_segment_writes_emit_versioned_upsert_and_delete_tombstone(db_session):
    job_id, video_id = uuid.uuid4(), uuid.uuid4()
    db_session.execute(
        text("INSERT INTO jobs(id,kind,input_url) VALUES (:id,'single','https://youtube.com/x')"), {"id": job_id}
    )
    db_session.execute(
        text("INSERT INTO videos(id,job_id,youtube_id) VALUES (:id,:job,'outbox-video')"),
        {"id": video_id, "job": job_id},
    )
    segment_id = db_session.execute(
        text("INSERT INTO segments(video_id,start_ms,end_ms,text) VALUES (:video,0,1000,'hello') RETURNING id"),
        {"video": video_id},
    ).scalar_one()
    upsert = (
        db_session.execute(
            text("SELECT operation, version, payload->>'text' AS text FROM search_index_outbox WHERE document_id=:id"),
            {"id": segment_id},
        )
        .mappings()
        .one()
    )
    assert upsert["operation"] == "upsert"
    assert upsert["version"] > 0
    assert upsert["text"] == "hello"

    db_session.execute(text("DELETE FROM segments WHERE id=:id"), {"id": segment_id})
    operations = (
        db_session.execute(
            text("SELECT operation FROM search_index_outbox WHERE document_id=:id ORDER BY id"), {"id": segment_id}
        )
        .scalars()
        .all()
    )
    assert operations == ["upsert", "delete"]
    assert search_freshness(db_session)["pending_documents"] == 2
