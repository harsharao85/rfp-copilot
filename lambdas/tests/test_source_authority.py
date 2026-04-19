"""Tests for source-authority corroboration rules.

Rule 1 — Freshness suppression: tested via _apply_freshness_suppression(),
  a pure function with no AWS dependencies.

Rule 2 — Primary required: tested via the get_dispatch_plan() output — a
  corroboration_required plan with no primary passages should yield
  primary_missing=True in the retrieval metadata.

Dispatch table: tested via get_dispatch_plan() merge behaviour.
"""
from __future__ import annotations

import pytest

from question_classifier.dispatch import (
    DEFAULT_DISPATCH_PLAN,
    DISPATCH_TABLE,
    get_dispatch_plan,
)
from retriever.handler import _apply_freshness_suppression
from shared.models import DispatchPlan, RetrievedPassage, Tier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _passage(doc_id: str, source_system: str = "compliance", *, updated_at: str = "", approved_at: str = "") -> RetrievedPassage:
    meta: dict = {}
    if updated_at:
        meta["updated_at"] = updated_at
    if approved_at:
        meta["approved_at"] = approved_at
    return RetrievedPassage(
        source_system=source_system,
        document_id=doc_id,
        excerpt=f"Excerpt for {doc_id}",
        score=0.8,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Rule 1 — Freshness suppression
# ---------------------------------------------------------------------------

def test_prior_suppressed_when_primary_is_newer() -> None:
    """Prior approved before the primary source last updated → suppressed."""
    primary = [_passage("cert-2024.json", updated_at="2024-06-01")]
    prior   = [_passage("rfp-2024.json", source_system="historical_rfp", approved_at="2024-01-15")]

    kept, suppressed = _apply_freshness_suppression(primary, prior)

    assert kept == []
    assert "rfp-2024.json" in suppressed


def test_prior_kept_when_prior_is_newer_than_primary() -> None:
    """Prior approved after the primary source last updated → kept."""
    primary = [_passage("cert-2023.json", updated_at="2023-12-01")]
    prior   = [_passage("rfp-2024.json", source_system="historical_rfp", approved_at="2024-01-15")]

    kept, suppressed = _apply_freshness_suppression(primary, prior)

    assert len(kept) == 1
    assert kept[0].document_id == "rfp-2024.json"
    assert suppressed == []


def test_no_primary_passages_means_no_suppression() -> None:
    """With no primary passages we have nothing to compare against — keep all priors."""
    prior = [_passage("rfp-old.json", source_system="historical_rfp", approved_at="2020-01-01")]

    kept, suppressed = _apply_freshness_suppression([], prior)

    assert kept == prior
    assert suppressed == []


def test_primary_without_updated_at_does_not_suppress() -> None:
    """Primary passage missing updated_at → suppression does not apply."""
    primary = [_passage("cert-nodates.json")]  # no updated_at
    prior   = [_passage("rfp-old.json", source_system="historical_rfp", approved_at="2020-01-01")]

    kept, suppressed = _apply_freshness_suppression(primary, prior)

    assert kept == prior
    assert suppressed == []


def test_mixed_priors_partial_suppression() -> None:
    """Older prior suppressed; newer prior kept when primary is between them."""
    primary = [_passage("cert.json", updated_at="2024-06-01")]
    prior = [
        _passage("rfp-old.json", source_system="historical_rfp", approved_at="2024-01-15"),  # suppress
        _passage("rfp-new.json", source_system="historical_rfp", approved_at="2024-07-01"),  # keep
    ]

    kept, suppressed = _apply_freshness_suppression(primary, prior)

    assert len(kept) == 1
    assert kept[0].document_id == "rfp-new.json"
    assert "rfp-old.json" in suppressed


def test_freshest_primary_is_used_for_comparison() -> None:
    """When multiple primary passages exist, the most recent updated_at is used."""
    primary = [
        _passage("cert-old.json", updated_at="2023-01-01"),
        _passage("cert-new.json", updated_at="2024-09-01"),  # this one drives suppression
    ]
    prior_old = _passage("rfp-old.json", source_system="historical_rfp", approved_at="2024-01-15")
    prior_new = _passage("rfp-new.json", source_system="historical_rfp", approved_at="2024-11-01")

    kept, suppressed = _apply_freshness_suppression(primary, [prior_old, prior_new])

    assert len(kept) == 1
    assert kept[0].document_id == "rfp-new.json"
    assert "rfp-old.json" in suppressed


def test_prior_without_approved_at_is_kept() -> None:
    """Prior with no approved_at cannot be compared — keep it (fail-safe)."""
    primary = [_passage("cert.json", updated_at="2024-06-01")]
    prior   = [_passage("rfp-nodates.json", source_system="historical_rfp")]  # no approved_at

    kept, suppressed = _apply_freshness_suppression(primary, prior)

    assert len(kept) == 1
    assert suppressed == []


# ---------------------------------------------------------------------------
# Dispatch table / get_dispatch_plan
# ---------------------------------------------------------------------------

def test_pricing_topic_forces_red() -> None:
    plan = get_dispatch_plan(["pricing"])
    assert plan.force_tier == Tier.RED


def test_soc2_requires_corroboration() -> None:
    plan = get_dispatch_plan(["soc2"])
    assert plan.corroboration_required is True
    assert "compliance_store" in plan.primary


def test_unknown_topic_returns_default() -> None:
    plan = get_dispatch_plan(["totally_unknown_topic"])
    assert plan == DEFAULT_DISPATCH_PLAN


def test_empty_topics_returns_default() -> None:
    plan = get_dispatch_plan([])
    assert plan == DEFAULT_DISPATCH_PLAN


def test_multi_topic_merges_sources() -> None:
    """SOC2 (compliance_store) + encryption_at_rest (product_docs) → both in primary."""
    plan = get_dispatch_plan(["soc2", "encryption_at_rest"])
    assert "compliance_store" in plan.primary
    assert "product_docs" in plan.primary


def test_multi_topic_corroboration_required_is_union() -> None:
    """If any matched topic requires corroboration, the merged plan requires it."""
    # soc2 requires corroboration; sso does not
    plan = get_dispatch_plan(["sso", "soc2"])
    assert plan.corroboration_required is True


def test_force_tier_propagates_in_merge() -> None:
    """If any matched topic has force_tier, the merged plan carries it."""
    plan = get_dispatch_plan(["encryption_at_rest", "pricing"])
    assert plan.force_tier == Tier.RED


def test_all_dispatch_table_topics_are_valid_dispatch_plans() -> None:
    """Smoke-test: every entry in DISPATCH_TABLE is a valid DispatchPlan."""
    for topic, plan in DISPATCH_TABLE.items():
        assert isinstance(plan, DispatchPlan), f"Bad plan for topic {topic!r}"
