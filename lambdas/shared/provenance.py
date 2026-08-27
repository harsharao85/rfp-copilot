"""Provenance and citation.

Every passage the retriever returns must be able to answer: *who did we say
this to, when, and who signed it off?* Without that, a first-pass questionnaire
answer is an assertion. With it, an SME can look at an amber cell and check the
lineage in seconds rather than going hunting.

Where the attribution lives
---------------------------
In sidecar metadata, not in the chunk body. This is a deliberate split:

  - Sidecar metadata is stored alongside the vector and returned with the
    passage, but is **not embedded**. So the customer name is citable and
    filterable without influencing similarity.
  - Chunk body is embedded. A customer name in the body pulls answers toward
    any query that happens to mention that customer, which is retrieval noise
    rather than signal -- "what is your key rotation policy" should not rank
    the Acme answer higher because the word Acme appears in it.

Practical upshot: put the attribution in the sidecar and, where the source
allows, keep it out of the prose. Where it is unavoidably in the prose
(a prior answer that says "as agreed with Acme Financial"), leave it — the
duplication is harmless, and rewriting source text to remove it would damage
the very lineage we are trying to preserve.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = ["Provenance", "Citation", "render_citation", "AnswerAudience"]


AnswerAudience = Literal[
    "customer",  # answered to a named customer in a questionnaire
    "prospect",  # answered during a sales cycle, may never have closed
    "internal",  # internal policy or control documentation
    "public",  # trust portal / published material
    "certification",  # SOC 2, ISO 27001 and similar attestations
]


class Provenance(BaseModel):
    """Attribution attached to every ingested document and carried through
    retrieval into the output workbook.

    `customer_name` is stored in the clear. It is the key that makes
    retrospective anomaly review possible: grouping prior answers by customer
    and date is how you find out you told two customers different things.
    """

    document_id: str
    source_id: str  # which configured source produced this
    audience: AnswerAudience = "internal"

    customer_name: str | None = None  # display name, used in citations
    customer_id: str | None = None  # stable key, survives renames

    questionnaire_id: str | None = None
    questionnaire_name: str | None = None

    answered_at: datetime | None = None  # when this answer went to the customer
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_on: datetime | None = None

    evidence_refs: list[str] = Field(default_factory=list)
    topic_ids: list[str] = Field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if self.expires_on is None:
            return False
        return self.expires_on < datetime.now(UTC)

    @property
    def age_days(self) -> int | None:
        anchor = self.answered_at or self.approved_at
        if anchor is None:
            return None
        return (datetime.now(UTC) - anchor).days

    def to_sidecar(self) -> dict[str, Any]:
        """Flatten into Bedrock KB sidecar attributes.

        Kept flat and small: S3 Vectors caps filterable metadata at 2 KB per
        vector, and nested structures are not filterable. Datetimes are ISO
        strings because the filter grammar compares lexically, and ISO-8601
        sorts correctly as a string — which is what makes `answered_at >
        '2026-01-01'` work as a retrieval filter without a numeric coercion.
        """
        attrs: dict[str, str | int | float | bool | list[str]] = {
            "document_id": self.document_id,
            "source_id": self.source_id,
            "audience": self.audience,
        }
        if self.customer_name:
            attrs["customer_name"] = self.customer_name
        if self.customer_id:
            attrs["customer_id"] = self.customer_id
        if self.questionnaire_id:
            attrs["questionnaire_id"] = self.questionnaire_id
        if self.questionnaire_name:
            attrs["questionnaire_name"] = self.questionnaire_name
        if self.answered_at:
            attrs["answered_at"] = self.answered_at.isoformat()
        if self.approved_at:
            attrs["approved_at"] = self.approved_at.isoformat()
        if self.expires_on:
            attrs["expires_on"] = self.expires_on.isoformat()
        if self.approved_by:
            attrs["approved_by"] = self.approved_by
        if self.topic_ids:
            attrs["topic_ids"] = self.topic_ids
        if self.evidence_refs:
            attrs["evidence_refs"] = self.evidence_refs
        attrs["expired"] = self.is_expired
        return attrs

    @classmethod
    def from_sidecar(cls, attrs: dict[str, Any]) -> Provenance:
        def _dt(key: str) -> datetime | None:
            raw = attrs.get(key)
            if not raw:
                return None
            try:
                return datetime.fromisoformat(str(raw))
            except ValueError:
                return None

        return cls(
            document_id=str(attrs.get("document_id", "")),
            source_id=str(attrs.get("source_id", "")),
            audience=attrs.get("audience", "internal"),
            customer_name=attrs.get("customer_name"),
            customer_id=attrs.get("customer_id"),
            questionnaire_id=attrs.get("questionnaire_id"),
            questionnaire_name=attrs.get("questionnaire_name"),
            answered_at=_dt("answered_at"),
            approved_by=attrs.get("approved_by"),
            approved_at=_dt("approved_at"),
            expires_on=_dt("expires_on"),
            evidence_refs=list(attrs.get("evidence_refs") or []),
            topic_ids=list(attrs.get("topic_ids") or []),
        )


class Citation(BaseModel):
    """A rendered source reference for one passage backing an answer."""

    provenance: Provenance
    excerpt: str = ""
    relevance: float = 0.0

    def render(self) -> str:
        return render_citation(self.provenance)


def render_citation(p: Provenance) -> str:
    """Human-readable source line for the output workbook.

    Leads with the customer and date because that is what an SME scans for.
    "Prior answer — Acme Financial Corp, Vendor Security Assessment,
    11 Mar 2026, approved by R. Sandhu (EXPIRED)"
    """
    parts: list[str] = []

    if p.audience == "customer" and p.customer_name:
        parts.append(f"Prior answer — {p.customer_name}")
    elif p.audience == "prospect" and p.customer_name:
        parts.append(f"Prospect answer — {p.customer_name}")
    elif p.audience == "certification":
        parts.append("Certification evidence")
    elif p.audience == "public":
        parts.append("Published (trust portal)")
    else:
        parts.append("Internal documentation")

    if p.questionnaire_name:
        parts.append(p.questionnaire_name)

    anchor = p.answered_at or p.approved_at
    if anchor:
        parts.append(anchor.strftime("%d %b %Y"))

    if p.approved_by:
        parts.append(f"approved by {p.approved_by}")

    line = ", ".join(parts)

    # Expiry is a suffix rather than a field so it survives truncation in a
    # narrow Excel comment box — the one part of the citation an SME must not
    # miss is that the evidence is out of date.
    if p.is_expired:
        line += "  (EXPIRED)"
    return line
