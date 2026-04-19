"""Pydantic data models shared between Lambda handlers and local scripts.

These define the typed envelope that flows between Step Functions states.
Keep them small and stable — Step Functions JSON payloads are not
forgiving of schema drift.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Tier(str, Enum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class CellRef(BaseModel):
    """Addresses back to the original workbook so the writer can
    surgically update cells without rebuilding the sheet."""
    sheet: str
    coordinate: str  # e.g. "B12"


class Question(BaseModel):
    job_id: str
    question_id: str
    text: str
    section: str | None = None
    context: str | None = None
    answer_cell: CellRef
    confidence_cell: CellRef
    compound: bool = False
    sub_questions: list[str] = Field(default_factory=list)


class RetrievedPassage(BaseModel):
    source_system: Literal[
        "historical_rfp", "whitepaper", "seismic", "gong", "cms", "confluence",
        "compliance", "sme_approved", "other"
    ]
    document_id: str
    excerpt: str
    score: float  # 0..1 normalized Kendra relevance
    uri: str | None = None
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class DispatchPlan(BaseModel):
    """Source authority routing plan for a classified topic.

    topic_class drives tier logic downstream (Phase G):
      auth_compliance — compliance_store is primary; GREEN if primary retrieves,
                        AMBER if it doesn't. sme_approved_answer NOT queried
                        (no SME-override decay risk on authoritative topics).
      auth_product    — product_docs is primary; same tier logic as above.
      gated           — pricing → RED forced; customer_reference → hard rule 4.
      unclassified    — composite-scored, tier capped at AMBER regardless of
                        composite (human review always required).

    primary:   authoritative sources (factual truth).
    secondary: phrasing reference only (prior RFPs). Never factual authority.
    tertiary:  contextual enrichment (Seismic, Gong) — optional, circuit-broken.
    """
    topic_class: str = "unclassified"
    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    tertiary: list[str] = Field(default_factory=list)
    force_tier: Tier | None = None
    reason: str | None = None


class PriorAnswerMatch(BaseModel):
    """SME-approved prior answer from the LibraryFeedback corpus."""
    answer_id: str
    question_text: str
    answer_text: str
    similarity: float  # cosine 0..1
    approved_by: str
    approved_at: datetime
    expires_on: datetime | None = None


class Retrieval(BaseModel):
    passages: list[RetrievedPassage]
    prior_matches: list[PriorAnswerMatch]
    reference_customers_matched: list[str] = Field(default_factory=list)
    """Customer names confirmed public_reference=true + unexpired approval in CustomerRefs.
    An answer invoking a customer NOT in this list must be forced amber (hard-rule #4)."""
    topic_class: str = "unclassified"
    """Class-based tier routing: auth_compliance | auth_product | gated | unclassified."""
    corroboration_metadata: dict[str, Any] = Field(default_factory=dict)
    """Source-authority signals forwarded to the hard-rules engine.
    Keys: force_tier (str), reason (str)."""


class GeneratedAnswer(BaseModel):
    answer_text: str
    citations: list[str]  # document_ids referenced
    invoked_customers: list[str] = Field(default_factory=list)
    contains_pricing: bool = False
    contains_compliance_claim: bool = False
    contains_forward_looking: bool = False
    model_id: str
    prompt_hash: str
    response_hash: str


class ConfidenceBreakdown(BaseModel):
    """Per v0.4 plan §5.7 — composite with explainable components."""
    h: float = Field(ge=0.0, le=1.0, description="Prior-answer match similarity")
    r: float = Field(ge=0.0, le=1.0, description="Retrieval strength (top-K normalized)")
    c: float = Field(ge=0.0, le=1.0, description="Source coverage")
    f: float = Field(ge=0.0, le=1.0, description="Freshness")
    g: float = Field(ge=0.0, le=1.0, description="Guardrail clean")

    # Weights locked per plan; override per-segment in future
    w_h: float = 0.45
    w_r: float = 0.25
    w_c: float = 0.15
    w_f: float = 0.10
    w_g: float = 0.05

    def composite(self) -> float:
        return (
            self.w_h * self.h
            + self.w_r * self.r
            + self.w_c * self.c
            + self.w_f * self.f
            + self.w_g * self.g
        )


class FinalAnswer(BaseModel):
    question_id: str
    answer_text: str
    citations: list[str]
    raw_confidence: float
    tier: Tier
    confidence_breakdown: ConfidenceBreakdown
    hard_rule_triggers: list[str] = Field(default_factory=list)
    reviewer_required: bool
    answer_cell: CellRef
    confidence_cell: CellRef
