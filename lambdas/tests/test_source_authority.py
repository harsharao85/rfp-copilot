"""Tests for class-based dispatch + scorer tier logic (Phase G).

Dispatch: every topic belongs to one of four classes (auth_compliance,
auth_product, gated, unclassified). Merging picks the most-restrictive class.

Scorer: tier is class-aware — auth_* goes GREEN if any primary passage
retrieved else AMBER; unclassified is composite-scored but capped at AMBER;
gated defers to hard_rules for force_tier.
"""
from __future__ import annotations

import pytest

from confidence_scorer.scorer import score
from question_classifier.dispatch import (
    DEFAULT_DISPATCH_PLAN,
    DISPATCH_TABLE,
    TOPIC_CLASS,
    get_dispatch_plan,
)
from shared.models import (
    ConfidenceBreakdown,
    DispatchPlan,
    GeneratedAnswer,
    RetrievedPassage,
    Tier,
)


# ---------------------------------------------------------------------------
# Dispatch: class assignment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "topic,expected_class",
    [
        ("soc2",               "auth_compliance"),
        ("iso27001",           "auth_compliance"),
        ("fedramp",            "auth_compliance"),
        ("gdpr",               "auth_compliance"),
        ("dpa",                "auth_compliance"),
        ("incident_response",  "auth_compliance"),
        ("pentest",            "auth_compliance"),
        ("encryption_at_rest", "auth_product"),
        ("sso",                "auth_product"),
        ("dr_bcp",             "auth_product"),
        ("data_residency",     "auth_product"),
        ("pricing",            "gated"),
        ("customer_reference", "gated"),
    ],
)
def test_topic_class_membership(topic: str, expected_class: str) -> None:
    assert TOPIC_CLASS[topic] == expected_class
    assert DISPATCH_TABLE[topic].topic_class == expected_class


def test_pricing_still_forces_red() -> None:
    plan = get_dispatch_plan(["pricing"])
    assert plan.topic_class == "gated"
    assert plan.force_tier == Tier.RED


def test_unknown_topic_returns_default_unclassified() -> None:
    plan = get_dispatch_plan(["totally_unknown_topic"])
    assert plan == DEFAULT_DISPATCH_PLAN
    assert plan.topic_class == "unclassified"


def test_empty_topics_returns_default() -> None:
    plan = get_dispatch_plan([])
    assert plan == DEFAULT_DISPATCH_PLAN


# ---------------------------------------------------------------------------
# Dispatch: class merge priority
# ---------------------------------------------------------------------------

def test_auth_compliance_beats_auth_product_on_merge() -> None:
    plan = get_dispatch_plan(["soc2", "encryption_at_rest"])
    assert plan.topic_class == "auth_compliance"
    # Both primary sources flow through.
    assert "compliance_store" in plan.primary
    assert "product_docs" in plan.primary


def test_gated_beats_auth_on_merge() -> None:
    plan = get_dispatch_plan(["pricing", "soc2"])
    assert plan.topic_class == "gated"
    assert plan.force_tier == Tier.RED


def test_dr_bcp_pulls_compliance_as_additional_primary() -> None:
    plan = get_dispatch_plan(["dr_bcp"])
    assert plan.topic_class == "auth_product"
    assert "product_docs" in plan.primary
    assert "compliance_store" in plan.primary


def test_sso_adds_seismic_tertiary() -> None:
    plan = get_dispatch_plan(["sso"])
    assert "seismic" in plan.tertiary


def test_all_dispatch_table_topics_are_valid_plans() -> None:
    for topic, plan in DISPATCH_TABLE.items():
        assert isinstance(plan, DispatchPlan), f"Bad plan for {topic!r}"
        assert plan.topic_class in ("auth_compliance", "auth_product", "gated", "unclassified")


# ---------------------------------------------------------------------------
# Scorer: class-aware tier decision
# ---------------------------------------------------------------------------

def _make_generated(citations: list[str] | None = None) -> GeneratedAnswer:
    return GeneratedAnswer(
        answer_text="x",
        citations=citations or [],
        model_id="test",
        prompt_hash="abc",
        response_hash="def",
    )


def _primary_passage(doc_id: str = "soc2_cert_2025") -> RetrievedPassage:
    return RetrievedPassage(
        source_system="compliance", document_id=doc_id,
        excerpt="SOC 2 audit covers Security and Availability.",
        score=0.8, metadata={"updated_at": "2025-10-01"},
    )


def _prior_rfp_passage() -> RetrievedPassage:
    return RetrievedPassage(
        source_system="historical_rfp", document_id="prior_rfp_1",
        excerpt="prior answer text", score=0.7,
        metadata={"approved_at": "2024-01-01"},
    )


def test_auth_compliance_with_primary_is_green() -> None:
    composite, _, tier = score(
        passages=[_primary_passage()],
        prior_matches=[],
        generated=_make_generated(citations=["soc2_cert_2025"]),
        topic_class="auth_compliance",
    )
    assert tier == Tier.GREEN


def test_auth_compliance_without_primary_is_amber() -> None:
    # Only a prior-RFP passage, no primary compliance doc.
    composite, _, tier = score(
        passages=[_prior_rfp_passage()],
        prior_matches=[],
        generated=_make_generated(),
        topic_class="auth_compliance",
    )
    assert tier == Tier.AMBER


def test_auth_product_with_whitepaper_primary_is_green() -> None:
    p = RetrievedPassage(
        source_system="whitepaper", document_id="encryption_whitepaper",
        excerpt="AES-256-GCM at rest.", score=0.9,
        metadata={"updated_at": "2025-09-01"},
    )
    _, _, tier = score(
        passages=[p], prior_matches=[],
        generated=_make_generated(citations=["encryption_whitepaper"]),
        topic_class="auth_product",
    )
    assert tier == Tier.GREEN


def test_auth_product_with_no_passages_is_amber() -> None:
    _, _, tier = score(
        passages=[], prior_matches=[], generated=_make_generated(),
        topic_class="auth_product",
    )
    assert tier == Tier.AMBER


def test_unclassified_green_composite_is_capped_at_amber() -> None:
    """Even a perfect composite score caps at AMBER for unclassified topics
    — human review is always required when no authoritative primary anchors
    the answer."""
    # Craft inputs that would yield composite >= 0.80 normally.
    p = RetrievedPassage(
        source_system="seismic", document_id="seismic_card",
        excerpt="sales deck content", score=1.0,
        metadata={"updated_at": "2026-03-01"},
    )
    prior = _prior_rfp_passage()  # only for C contribution
    from shared.models import PriorAnswerMatch
    from datetime import datetime, timezone
    m = PriorAnswerMatch(
        answer_id="m1", question_text="q", answer_text="a", similarity=1.0,
        approved_by="sme", approved_at=datetime.now(timezone.utc),
    )
    _, breakdown, tier = score(
        passages=[p, prior],
        prior_matches=[m],
        generated=_make_generated(citations=["seismic_card", "prior_rfp_1"]),
        topic_class="unclassified",
    )
    # Composite should be high but tier capped.
    assert breakdown.composite() >= 0.55
    assert tier == Tier.AMBER


def test_unclassified_weak_composite_is_red() -> None:
    _, _, tier = score(
        passages=[], prior_matches=[], generated=_make_generated(),
        topic_class="unclassified",
    )
    assert tier == Tier.RED


def test_gated_tier_is_composite_placeholder() -> None:
    """Gated class doesn't get special tier handling in the scorer — hard_rules
    runs after and overrides with force_tier for pricing."""
    _, _, tier = score(
        passages=[], prior_matches=[], generated=_make_generated(),
        topic_class="gated",
    )
    # Empty passages → composite ~0 → RED by default.
    assert tier == Tier.RED


def test_topic_class_defaults_to_unclassified() -> None:
    """When topic_class is omitted, falls back to unclassified (capped at amber)."""
    from shared.models import PriorAnswerMatch
    from datetime import datetime, timezone
    p = RetrievedPassage(
        source_system="compliance", document_id="x", excerpt="y", score=1.0,
        metadata={"updated_at": "2026-03-01"},
    )
    m = PriorAnswerMatch(
        answer_id="m1", question_text="q", answer_text="a", similarity=1.0,
        approved_by="sme", approved_at=datetime.now(timezone.utc),
    )
    _, _, tier = score(
        passages=[p], prior_matches=[m],
        generated=_make_generated(citations=["x"]),
    )
    # Default unclassified + strong signals → would be green, capped to amber.
    assert tier == Tier.AMBER
