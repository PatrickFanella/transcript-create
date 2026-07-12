from uuid import uuid4

from app.archive.opinion_history import opinion_publishable
from app.schemas import OpinionCandidateCreate, OpinionEvidence


def candidate(confidence: float, evidence=True):
    return OpinionCandidateCreate(
        normalized_claim="Housing should be affordable",
        stance="support",
        summary="The speaker supports affordable housing.",
        confidence=confidence,
        model_version="model-1",
        prompt_version="prompt-1",
        time_bucket="2026-Q2",
        evidence=(
            [OpinionEvidence(video_id=uuid4(), start_ms=1000, end_ms=2000, excerpt="Housing should be affordable")]
            if evidence
            else []
        ),
    )


def test_opinion_auto_publish_requires_threshold_and_direct_evidence():
    assert opinion_publishable(candidate(0.90)) is True
    assert opinion_publishable(candidate(0.899)) is False
    assert opinion_publishable(candidate(0.99, evidence=False)) is False


def test_opinion_auto_publish_rejects_invalid_evidence_range():
    item = candidate(0.99)
    item.evidence[0].end_ms = 500
    assert opinion_publishable(item) is False
