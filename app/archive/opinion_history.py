"""Append-only opinion history with evidence-gated publication."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from ..schemas import OpinionCandidateCreate, OpinionHistoryItem, OpinionRevisionResponse

AUTO_PUBLISH_CONFIDENCE = 0.90


def opinion_publishable(candidate: OpinionCandidateCreate) -> bool:
    return (
        candidate.confidence >= AUTO_PUBLISH_CONFIDENCE
        and bool(candidate.evidence)
        and all(evidence.excerpt.strip() and evidence.end_ms >= evidence.start_ms for evidence in candidate.evidence)
    )


def record_opinion_candidate(db, *, subject_slug: str, candidate: OpinionCandidateCreate) -> uuid.UUID:
    row = (
        db.execute(
            text("""
            INSERT INTO archive_opinions(subject_slug, normalized_claim)
            VALUES (:subject_slug, :claim)
            ON CONFLICT(subject_slug, normalized_claim) DO UPDATE SET updated_at=now()
            RETURNING id, status, current_revision
        """),
            {"subject_slug": subject_slug, "claim": candidate.normalized_claim.strip()},
        )
        .mappings()
        .one()
    )
    opinion_id = row["id"]
    revision = int(
        db.execute(
            text("SELECT COALESCE(MAX(revision), 0) + 1 FROM archive_opinion_revisions WHERE opinion_id=:id"),
            {"id": opinion_id},
        ).scalar_one()
    )
    status = "published" if opinion_publishable(candidate) else "candidate"
    db.execute(
        text("""
            INSERT INTO archive_opinion_revisions(
                opinion_id, revision, stance, summary, confidence, model_version,
                prompt_version, time_bucket, evidence, model_generated, status
            ) VALUES (
                :opinion_id, :revision, :stance, :summary, :confidence, :model_version,
                :prompt_version, :time_bucket, CAST(:evidence AS jsonb), true, :status
            )
        """),
        {
            "opinion_id": opinion_id,
            "revision": revision,
            "stance": candidate.stance,
            "summary": candidate.summary,
            "confidence": candidate.confidence,
            "model_version": candidate.model_version,
            "prompt_version": candidate.prompt_version,
            "time_bucket": candidate.time_bucket,
            "evidence": json.dumps([item.model_dump(mode="json") for item in candidate.evidence]),
            "status": status,
        },
    )
    if status == "published" or row["status"] == "candidate":
        db.execute(
            text("UPDATE archive_opinions SET status=:status,current_revision=:revision,updated_at=now() WHERE id=:id"),
            {"status": status, "revision": revision, "id": opinion_id},
        )
    return uuid.UUID(str(opinion_id))


def _revision(row: dict[str, Any]) -> OpinionRevisionResponse:
    evidence = row["evidence"] if isinstance(row["evidence"], list) else json.loads(row["evidence"] or "[]")
    return OpinionRevisionResponse(**{**row, "evidence": evidence})


def list_opinion_history(db, *, subject_slug: str, include_unpublished: bool = False) -> list[OpinionHistoryItem]:
    statuses = (
        "('candidate','published','corrected','retracted')" if include_unpublished else "('published','corrected')"
    )
    opinions = (
        db.execute(
            text(f"""
            SELECT id, subject_slug, normalized_claim, status, current_revision
            FROM archive_opinions WHERE subject_slug=:slug AND status IN {statuses}
            ORDER BY updated_at DESC
        """),
            {"slug": subject_slug},
        )
        .mappings()
        .all()
    )
    items = []
    for opinion in opinions:
        revision_rows = (
            db.execute(
                text(f"""
                SELECT revision,stance,summary,confidence,model_version,prompt_version,time_bucket,
                       evidence,model_generated,status,correction_reason,created_at
                FROM archive_opinion_revisions
                WHERE opinion_id=:id AND status IN {statuses}
                ORDER BY revision DESC
            """),
                {"id": opinion["id"]},
            )
            .mappings()
            .all()
        )
        items.append(OpinionHistoryItem(**dict(opinion), revisions=[_revision(dict(row)) for row in revision_rows]))
    return items


def revise_opinion(
    db,
    *,
    opinion_id: uuid.UUID,
    status: str,
    reason: str,
    corrected_by: uuid.UUID,
    stance: str | None = None,
    summary: str | None = None,
) -> None:
    current = (
        db.execute(
            text("""
            SELECT o.current_revision,r.* FROM archive_opinions o
            JOIN archive_opinion_revisions r ON r.opinion_id=o.id AND r.revision=o.current_revision
            WHERE o.id=:id FOR UPDATE OF o
        """),
            {"id": opinion_id},
        )
        .mappings()
        .one()
    )
    revision = int(current["current_revision"]) + 1
    db.execute(
        text("""
            INSERT INTO archive_opinion_revisions(
                opinion_id,revision,stance,summary,confidence,model_version,prompt_version,time_bucket,
                evidence,model_generated,status,correction_reason,corrected_by
            ) VALUES (
                :id,:revision,:stance,:summary,:confidence,:model_version,:prompt_version,:time_bucket,
                :evidence,false,:status,:reason,:corrected_by
            )
        """),
        {
            "id": opinion_id,
            "revision": revision,
            "stance": stance or current["stance"],
            "summary": summary or current["summary"],
            "confidence": current["confidence"],
            "model_version": current["model_version"],
            "prompt_version": current["prompt_version"],
            "time_bucket": current["time_bucket"],
            "evidence": json.dumps(current["evidence"]),
            "status": status,
            "reason": reason,
            "corrected_by": corrected_by,
        },
    )
    db.execute(
        text("UPDATE archive_opinions SET status=:status,current_revision=:revision,updated_at=now() WHERE id=:id"),
        {"status": status, "revision": revision, "id": opinion_id},
    )
