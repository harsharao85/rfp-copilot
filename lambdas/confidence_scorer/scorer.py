"""Composite confidence scoring — per v0.4 plan §5.7.

We do not trust LLM self-reported confidence. Empirically, modern
LLMs are overconfident on wrong answers — the "confident hallucination"
failure mode. The composite below is grounded in measurable signals
that are tunable against labeled data.

Weights:
  H (0.45) — Prior-answer match similarity to nearest SME-approved
  R (0.25) — Retrieval strength (Kendra top-K normalized)
  C (0.15) — Source coverage (>=2 independent sources)
  F (0.10) — Freshness decay
  G (0.05) — Guardrail clean

Thresholds:
  >= 0.80 green
  0.55–0.80 amber
  < 0.55 red

Hard-rule overrides live in `hard_rules/` and run AFTER scoring —
they can demote green to amber or amber to red, never the reverse.
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


def score(
    *,
    passages: list[RetrievedPassage],
    prior_matches: list[PriorAnswerMatch],
    generated: GeneratedAnswer,
    guardrail_flags: list[str] | None = None,
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

    # H=0 cap: if no comparable prior answer exists, cap at amber.
    # This is the "never-before-answered" safeguard — even strong
    # retrieval shouldn't yield green without SME history.
    if breakdown.h == 0.0 and composite >= GREEN_THRESHOLD:
        composite = GREEN_THRESHOLD - 0.01

    if composite >= GREEN_THRESHOLD:
        tier = Tier.GREEN
    elif composite >= AMBER_THRESHOLD:
        tier = Tier.AMBER
    else:
        tier = Tier.RED

    return composite, breakdown, tier
