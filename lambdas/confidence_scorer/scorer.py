"""Composite confidence scoring + class-aware tier decision (Phase G).

We do not trust LLM self-reported confidence. The composite below is
grounded in measurable signals (H, R, C, F, G) and remains the audit
record of how strong each signal was for every question.

Phase G changes the *tier* logic — it no longer comes purely from the
composite. Instead:

  auth_compliance, auth_product — GREEN if any primary passage retrieved,
    AMBER if not. sme_approved_answer isn't queried for these topics
    (no H-signal decay risk). Composite is still computed for audit.

  unclassified — composite-based tier, but *capped at AMBER regardless*.
    Human review always required when no authoritative primary anchors
    the answer.

  gated — tier is whatever composite says; hard_rules overrides to RED
    for pricing or applies the customer-reference gate.

Composite weights remain:
  H (0.45) — Prior-answer match similarity (semantic cosine from KB)
  R (0.25) — Retrieval strength (top-K mean of KB cosine scores)
  C (0.15) — Source coverage (>=2 distinct source_systems in citations)
  F (0.10) — Freshness decay of cited passages
  G (0.05) — Guardrail clean

Composite thresholds (used for UNCLASSIFIED only now):
  >= 0.80 green  →  capped to AMBER for unclassified
  0.55–0.80 amber
  < 0.55 red

Hard rules run AFTER this scorer and can only demote (pricing → RED,
compliance claim without citation → min AMBER, etc.).
"""
from __future__ import annotations

from datetime import datetime, timezone

from shared.models import (
    ConfidenceBreakdown,
    GeneratedAnswer,
    PriorAnswerMatch,
    RetrievedPassage,
    Tier,
)

GREEN_THRESHOLD = 0.80
AMBER_THRESHOLD = 0.55

# Passages with these source_system values count as "primary" for the
# auth_* class tier decision. kb_source_type → passage.source_system is
# compliance_cert → "compliance" and product_doc → "whitepaper".
_PRIMARY_SOURCE_SYSTEMS = {"compliance", "whitepaper"}


def score_h(prior_matches: list[PriorAnswerMatch]) -> float:
    """Highest prior-answer similarity, weighted down if match is expired."""
    if not prior_matches:
        return 0.0
    best = max(prior_matches, key=lambda m: m.similarity)
    if best.expires_on and best.expires_on < datetime.now(timezone.utc):
        return best.similarity * 0.5  # expired: halve the signal
    return best.similarity


def score_r(passages: list[RetrievedPassage], top_n: int = 3) -> float:
    """Mean of top-N retrieval scores (already normalized 0..1)."""
    if not passages:
        return 0.0
    top = sorted(passages, key=lambda p: p.score, reverse=True)[:top_n]
    return sum(p.score for p in top) / len(top)


def score_c(passages: list[RetrievedPassage], generated_citations: list[str]) -> float:
    """Coverage: 1.0 if >=2 distinct source_systems agree among cited passages,
    0.5 if one source only, 0.0 if no support."""
    cited = [p for p in passages if p.document_id in generated_citations]
    if not cited:
        return 0.0
    distinct_systems = {p.source_system for p in cited}
    if len(distinct_systems) >= 2:
        return 1.0
    return 0.5


def score_f(passages: list[RetrievedPassage]) -> float:
    """Freshness decay applied to the most-recent cited source.
    <90d = 1.0, <1yr = 0.7, <2yr = 0.4, else 0.2.
    Passages must carry an 'updated_at' metadata attribute (ISO date)."""
    if not passages:
        return 0.2
    now = datetime.now(timezone.utc)
    best = 0.2
    for p in passages:
        raw = p.metadata.get("updated_at")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        age_days = (now - dt).days
        if age_days < 90:
            best = max(best, 1.0)
        elif age_days < 365:
            best = max(best, 0.7)
        elif age_days < 730:
            best = max(best, 0.4)
    return best


def score_g(guardrail_flags: list[str]) -> float:
    """1.0 if no Guardrail/policy flags fired, 0.0 if any did."""
    return 0.0 if guardrail_flags else 1.0


def _composite_tier(composite: float) -> Tier:
    if composite >= GREEN_THRESHOLD:
        return Tier.GREEN
    if composite >= AMBER_THRESHOLD:
        return Tier.AMBER
    return Tier.RED


def score(
    *,
    passages: list[RetrievedPassage],
    prior_matches: list[PriorAnswerMatch],
    generated: GeneratedAnswer,
    guardrail_flags: list[str] | None = None,
    topic_class: str = "unclassified",
) -> tuple[float, ConfidenceBreakdown, Tier]:
    guardrail_flags = guardrail_flags or []
    breakdown = ConfidenceBreakdown(
        h=score_h(prior_matches),
        r=score_r(passages),
        c=score_c(passages, generated.citations),
        f=score_f(passages),
        g=score_g(guardrail_flags),
    )
    composite = breakdown.composite()

    if topic_class in ("auth_compliance", "auth_product"):
        # Primary-backed topic. GREEN if any authoritative passage retrieved,
        # AMBER if the primary source returned nothing. Composite is still
        # computed above for audit/telemetry but doesn't decide the tier.
        has_primary = any(p.source_system in _PRIMARY_SOURCE_SYSTEMS for p in passages)
        tier = Tier.GREEN if has_primary else Tier.AMBER
    elif topic_class == "gated":
        # hard_rules will force RED for pricing or apply the customer-reference
        # gate. Return the composite-based tier as a placeholder.
        tier = _composite_tier(composite)
    else:
        # unclassified — composite-based tier, capped at AMBER. No authoritative
        # anchor, so human review is always required before shipping.
        tier = _composite_tier(composite)
        if tier == Tier.GREEN:
            tier = Tier.AMBER

    return composite, breakdown, tier
