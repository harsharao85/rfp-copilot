"""Tests for the hard-rule engine.

These tests encode enterprise-legal constraints. Any change that breaks
them is a policy change and must be reviewed by General Counsel — not
a refactor. Do not relax these assertions to make tests pass.
"""
from __future__ import annotations

from hard_rules.rules import apply_rules
from shared.models import Tier


def test_pricing_forces_red_even_from_green() -> None:
    result = apply_rules(
        answer_text="Enterprise pricing starts at $50 per user per month.",
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.GREEN,
    )
    assert result.final_tier == Tier.RED
    assert "pricing_detected" in result.triggers


def test_compliance_claim_minimum_amber() -> None:
    result = apply_rules(
        answer_text="We hold SOC 2 Type II and ISO 27001 certifications.",
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.GREEN,
    )
    assert result.final_tier == Tier.AMBER
    assert "compliance_claim" in result.triggers


def test_unapproved_customer_forces_amber() -> None:
    result = apply_rules(
        answer_text="We have deployed for Aurora Federal Credit Union in production.",
        invoked_customers=["Aurora Federal Credit Union"],
        approved_reference_customers=["Northwind Capital", "Helix Biomedical"],
        current_tier=Tier.GREEN,
    )
    assert result.final_tier == Tier.AMBER
    assert any(t.startswith("unapproved_reference:") for t in result.triggers)


def test_approved_customer_stays_green() -> None:
    result = apply_rules(
        answer_text="Northwind Capital is a representative financial services deployment.",
        invoked_customers=["Northwind Capital"],
        approved_reference_customers=["Northwind Capital", "Helix Biomedical"],
        current_tier=Tier.GREEN,
    )
    # No other triggers, approved customer → stays green
    assert result.final_tier == Tier.GREEN
    assert result.triggers == []


def test_forward_looking_demotes_to_amber() -> None:
    result = apply_rules(
        answer_text="We will deliver FedRAMP Moderate authorization by Q3 2026.",
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.GREEN,
    )
    # Also fires compliance_claim (FedRAMP, authorization) — but minimum is AMBER
    assert result.final_tier == Tier.AMBER
    assert "forward_looking_statement" in result.triggers


def test_pricing_beats_compliance_demote() -> None:
    """When both pricing and compliance fire, pricing's RED wins."""
    result = apply_rules(
        answer_text="Our SOC 2 audited Enterprise tier is $50 per user.",
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.GREEN,
    )
    assert result.final_tier == Tier.RED


def test_red_cannot_be_promoted() -> None:
    """Starting RED stays RED regardless of what rules fire."""
    result = apply_rules(
        answer_text="A totally benign sentence with no risky language.",
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.RED,
    )
    assert result.final_tier == Tier.RED
