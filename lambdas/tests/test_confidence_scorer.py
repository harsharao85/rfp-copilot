"""Tests for composite confidence scoring.

The weight vector (0.45 H, 0.25 R, 0.15 C, 0.10 F, 0.05 G) is a
policy decision. If these tests break because the composite value
changed, confirm that the weights haven't drifted before updating
expected values.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from confidence_scorer.scorer import score
from shared.models import (
    GeneratedAnswer,
    PriorAnswerMatch,
    RetrievedPassage,
    Tier,
)


def _make_answer(**overrides: object) -> GeneratedAnswer:
    defaults = {
        "answer_text": "test",
        "citations": ["doc-1", "doc-2"],
        "invoked_customers": [],
        "contains_pricing": False,
        "contains_compliance_claim": False,
        "contains_forward_looking": False,
        "model_id": "mock",
        "prompt_hash": "hash",
        "response_hash": "hash",
    }
    defaults.update(overrides)
    return GeneratedAnswer(**defaults)  # type: ignore[arg-type]


def _recent(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_auth_product_with_primary_passage_is_green() -> None:
    """Phase G: auth_* classes are GREEN when any primary passage retrieves."""
    passages = [
        RetrievedPassage(
            source_system="historical_rfp", document_id="doc-1",
            excerpt="...", score=0.95, uri="s3://x",
            metadata={"updated_at": _recent(30)},
        ),
        RetrievedPassage(
            source_system="whitepaper", document_id="doc-2",  # primary
            excerpt="...", score=0.88, uri="s3://y",
            metadata={"updated_at": _recent(45)},
        ),
    ]
    composite, breakdown, tier = score(
        passages=passages, prior_matches=[], generated=_make_answer(),
        topic_class="auth_product",
    )
    assert tier == Tier.GREEN
    # Composite still computed for audit even when tier doesn't use it.
    assert breakdown.h == 0.0  # No SME H-signal on auth_* topics


def test_unclassified_strong_composite_still_caps_at_amber() -> None:
    """Phase G: unclassified never goes green regardless of composite — human
    review is required because no authoritative primary anchors the answer."""
    passages = [
        RetrievedPassage(
            source_system="historical_rfp", document_id="doc-1",
            excerpt="...", score=0.95, uri="s3://x",
            metadata={"updated_at": _recent(30)},
        ),
        RetrievedPassage(
            source_system="whitepaper", document_id="doc-2",
            excerpt="...", score=0.88, uri="s3://y",
            metadata={"updated_at": _recent(45)},
        ),
    ]
    prior = [PriorAnswerMatch(
        answer_id="a-1", question_text="q", answer_text="a",
        similarity=0.92, approved_by="sme-1",
        approved_at=datetime.now(timezone.utc) - timedelta(days=60),
        expires_on=datetime.now(timezone.utc) + timedelta(days=300),
    )]
    composite, breakdown, tier = score(
        passages=passages, prior_matches=prior, generated=_make_answer(),
        topic_class="unclassified",
    )
    assert composite >= 0.80      # would be green by composite
    assert tier == Tier.AMBER     # but capped
    assert breakdown.h == 0.92


def test_no_evidence_is_red() -> None:
    composite, _, tier = score(
        passages=[], prior_matches=[], generated=_make_answer(citations=[]),
    )
    assert tier == Tier.RED
    assert composite < 0.55


def test_expired_prior_is_halved() -> None:
    expired_prior = [PriorAnswerMatch(
        answer_id="a-1", question_text="q", answer_text="a",
        similarity=0.90, approved_by="sme-1",
        approved_at=datetime.now(timezone.utc) - timedelta(days=800),
        expires_on=datetime.now(timezone.utc) - timedelta(days=30),  # expired
    )]
    _, breakdown, _ = score(
        passages=[], prior_matches=expired_prior, generated=_make_answer(),
    )
    # Expired priors are halved
    assert abs(breakdown.h - 0.45) < 0.01


def test_weights_sum_to_one() -> None:
    # Guard against weight drift
    from shared.models import ConfidenceBreakdown
    b = ConfidenceBreakdown(h=0.0, r=0.0, c=0.0, f=0.0, g=0.0)
    assert abs((b.w_h + b.w_r + b.w_c + b.w_f + b.w_g) - 1.0) < 0.001
