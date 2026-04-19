"""Hard-rule engine — runs AFTER composite scoring and can only
demote tiers (never promote). These rules are enterprise-legal
constraints, not AI-safety preferences. They are versioned in this
file and reviewed annually by General Counsel.

Enforced rules (per v0.4 plan §5.7, §7):
  1. Pricing / commercial language → forced RED. Commercial desk owns.
  2. Compliance / certification claims → minimum AMBER. Compliance owns.
  3. Reference-customer names → minimum AMBER unless the graph
     confirms public_reference=true with unexpired approval.
  4. Forward-looking statements ("will deliver by Q3") → minimum AMBER.
  5. Competitor disparagement → forced RED.

The rule set version is stamped on every audit record so we know
which policy was in effect when an answer was generated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared.models import Tier

RULES_VERSION = "2026-04-14"

# Patterns. Intentionally broad — we'd rather false-positive into
# SME review than false-negative into a prospect-facing commitment.

PRICING_PATTERNS = [
    re.compile(r"\$\s?\d[\d,]*(\.\d{2})?", re.IGNORECASE),
    re.compile(r"\b(price|pricing|cost|fee|subscription|per[- ]seat|per[- ]user|annual\s+contract)\b", re.IGNORECASE),
    re.compile(r"\b(discount|rebate|volume\s+discount)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s*%\s*(off|discount)", re.IGNORECASE),
]

COMPLIANCE_PATTERNS = [
    re.compile(r"\b(SOC\s*2|SOC\s*1|ISO\s*27001|ISO\s*9001|FedRAMP|HIPAA|PCI[-\s]?DSS|GDPR|CCPA|HITRUST)\b", re.IGNORECASE),
    re.compile(r"\b(compliant|certif(y|ied|ication)|attestation|audit(ed)?)\b", re.IGNORECASE),
]

FORWARD_LOOKING_PATTERNS = [
    re.compile(r"\bwill\s+(deliver|ship|release|launch|provide|support)\b", re.IGNORECASE),
    re.compile(r"\bby\s+(Q[1-4]|end\s+of\s+year|H[12]\s+\d{4}|\d{4})\b", re.IGNORECASE),
    re.compile(r"\bon\s+our\s+roadmap\b", re.IGNORECASE),
]

SLA_PATTERNS = [
    re.compile(r"\b\d{1,2}(\.\d+)?\s*%\s+uptime\b", re.IGNORECASE),
    re.compile(r"\b(RTO|RPO|MTTR|MTBF)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(hour|minute|second|day)s?\s+(response|resolution|recovery)\b", re.IGNORECASE),
]

COMPETITOR_DISPARAGEMENT_PATTERNS = [
    re.compile(r"\bunlike\s+\w+", re.IGNORECASE),
    re.compile(r"\b(inferior|worse|outdated|legacy)\s+to\b", re.IGNORECASE),
]


@dataclass
class RuleResult:
    final_tier: Tier
    triggers: list[str]
    reviewer_required: bool


def _demote(current: Tier, minimum: Tier) -> Tier:
    """Demote tier to at least `minimum`. Order: GREEN > AMBER > RED."""
    order = {Tier.GREEN: 2, Tier.AMBER: 1, Tier.RED: 0}
    if order[minimum] < order[current]:
        return minimum
    return current


def apply_rules(
    *,
    answer_text: str,
    invoked_customers: list[str],
    approved_reference_customers: list[str],
    current_tier: Tier,
) -> RuleResult:
    """Apply hard rules and return the adjusted tier plus triggered rule names."""
    triggers: list[str] = []
    tier = current_tier

    # Rule 1: Pricing → RED
    if any(p.search(answer_text) for p in PRICING_PATTERNS):
        tier = Tier.RED
        triggers.append("pricing_detected")

    # Rule 5: Competitor disparagement → RED (applied before AMBER demotes)
    if any(p.search(answer_text) for p in COMPETITOR_DISPARAGEMENT_PATTERNS):
        tier = Tier.RED
        triggers.append("competitor_disparagement")

    # Rule 2: Compliance claim → minimum AMBER
    if any(p.search(answer_text) for p in COMPLIANCE_PATTERNS):
        tier = _demote(tier, Tier.AMBER)
        triggers.append("compliance_claim")

    # SLA claim → minimum AMBER (related to compliance + commercial risk)
    if any(p.search(answer_text) for p in SLA_PATTERNS):
        tier = _demote(tier, Tier.AMBER)
        triggers.append("sla_claim")

    # Rule 3: Reference-customer gate
    approved_lower = {c.lower() for c in approved_reference_customers}
    for customer in invoked_customers:
        if customer.lower() not in approved_lower:
            tier = _demote(tier, Tier.AMBER)
            triggers.append(f"unapproved_reference:{customer}")

    # Rule 4: Forward-looking → minimum AMBER
    if any(p.search(answer_text) for p in FORWARD_LOOKING_PATTERNS):
        tier = _demote(tier, Tier.AMBER)
        triggers.append("forward_looking_statement")

    return RuleResult(
        final_tier=tier,
        triggers=triggers,
        reviewer_required=tier != Tier.GREEN,
    )
