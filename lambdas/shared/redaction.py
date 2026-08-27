"""Deterministic pseudonymisation for corpus documents before embedding.

Design rationale
----------------
Customer identifiers are *pseudonymised*, not stripped. `Acme Financial Corp`
becomes `[CUSTOMER_A7F3]` consistently across every document in the corpus.

This matters more than it looks. Masking with `XXXX` or deleting the span
destroys two things we need:

  1. Cross-document reasoning. "Have we answered this for this customer
     before?" requires that the same customer resolve to the same symbol in
     doc A and doc B.
  2. Embedding quality. Chunks where every entity collapses to the same mask
     token become semantically mushy and cluster together in vector space
     for the wrong reason. Distinct stable tokens preserve distinctness.

Tokens are short, typed and subword-friendly so Titan embeds them as a
recognisable unit rather than as high-entropy noise: `[CUSTOMER_A7F3]`, not
`[a3f9e2c1-4b7d-11ef-9c3a-0242ac120002]`.

Reversal
--------
The token is an HMAC of the normalised entity under a secret held in KMS, so
the corpus alone does not permit reversal. The forward map lives in DynamoDB
under a separate CMK. Only the output-writing path may read it, and only for
entities the CustomerRefs table marks `public_reference=true` and unexpired --
which reuses the reference-gating rule the pipeline already enforces.

Fail-closed
-----------
Any error, or any finding below the confidence floor, quarantines the document
rather than passing it through. Once a raw identifier is embedded into the
vector index, removing it means a full re-ingest; there is no partial delete
that is cheap or provable. Fail-open is not an option worth the convenience.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Callable, Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "CITATION_SAFE",
    "EntityClass",
    "Finding",
    "RedactionProfile",
    "RedactionResult",
    "Redactor",
    "STRICT",
    "STANDARD",
    "OFF",
]

REDACTION_VERSION = "2026-08-26"

# Token width. 4 hex chars = 65,536 buckets per class. Collision probability
# crosses 1% at ~36 distinct entities in a class (birthday bound), which is
# too tight for a real corpus -- so the Redactor detects collisions against
# the entity map and widens to 6 chars for the colliding entity. Width is
# per-entity, not global, so existing tokens stay stable.
_TOKEN_WIDTH = 4
_TOKEN_WIDTH_WIDE = 6


class EntityClass(str, Enum):
    """Classes of entity we pseudonymise. Value is the token prefix."""

    CUSTOMER = "CUSTOMER"
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IP = "IP"
    URL = "URL"
    ADDRESS = "ADDRESS"
    CONTRACT = "CONTRACT"
    CREDENTIAL = "CREDENTIAL"


# Legal-form suffixes stripped during normalisation so that "Acme Financial
# Corp", "Acme Financial Corporation" and "Acme Financial" collapse to one
# token. Without this the same customer fragments across several tokens and
# the cross-document guarantee above quietly stops holding.
_LEGAL_SUFFIXES = (
    "incorporated",
    "inc",
    "corporation",
    "corp",
    "company",
    "co",
    "limited",
    "ltd",
    "llc",
    "llp",
    "lp",
    "plc",
    "gmbh",
    "ag",
    "sa",
    "nv",
    "bv",
    "pty",
    "pte",
    "srl",
    "spa",
    "oy",
    "ab",
    "as",
    "kk",
    "holdings",
    "group",
    "international",
    "technologies",
    "technology",
    "systems",
    "solutions",
    "services",
    "software",
)

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalise_entity(raw: str) -> str:
    """Fold an entity mention to its canonical form for token derivation.

    Lowercases, strips punctuation and trailing legal-form suffixes. Suffix
    stripping is iterative because "Acme Systems Group Ltd" carries three.
    """
    text = _PUNCT.sub(" ", raw.lower())
    text = _WS.sub(" ", text).strip()
    parts = text.split(" ")
    while len(parts) > 1 and parts[-1] in _LEGAL_SUFFIXES:
        parts.pop()
    return " ".join(parts)


# --------------------------------------------------------------------------
# Pattern-based detectors
#
# These run before any ML pass. They are exact, cheap and have no confidence
# score to threshold -- a string matching the email grammar is an email. The
# ML pass exists to catch what patterns cannot: unlisted org names and people.
# --------------------------------------------------------------------------

_PATTERNS: dict[EntityClass, re.Pattern[str]] = {
    EntityClass.EMAIL: re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # E.164 and common NA/EU formats. Deliberately not greedy about bare
    # digit runs -- version strings and port numbers are not phone numbers.
    # The trailing guard is deliberately `(?![-\w]|\.\d)` and not `(?![\w.])`.
    # The stricter form rejects a phone number at the end of a sentence,
    # because the following character is a period -- the engine then backtracks
    # off the final group and emits `[PHONE_xxxx] 0142`, leaking the last four
    # digits. Partial redaction is worse than none, since it looks handled.
    # This form still refuses to run into a version string or dotted quad.
    EntityClass.PHONE: re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?"
        r"\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?![-\w]|\.\d)"
    ),
    EntityClass.IP: re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
    ),
    EntityClass.URL: re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.IGNORECASE),
    # Contract / order / MSA references, e.g. MSA-2024-00184, SOW#4471.
    EntityClass.CONTRACT: re.compile(
        r"\b(?:MSA|SOW|DPA|PO|ORD|CTR)[-#\s]?\d{2,}(?:[-]\d+)*\b", re.IGNORECASE
    ),
    # Anything that looks like a live secret has no business in a corpus.
    EntityClass.CREDENTIAL: re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}"
        r"|(?:ghp|gho|ghs)_[A-Za-z0-9]{36}"
        r"|-----BEGIN[A-Z ]+PRIVATE KEY-----)"
    ),
}

# Reserved hosts that are documentation examples, not real infrastructure.
# Redacting these adds noise and makes answers worse for no privacy gain.
_IP_ALLOW = re.compile(
    r"^(?:0\.0\.0\.0|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\."
    r"|169\.254\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|255\.)"
)

_URL_ALLOW = re.compile(
    r"^https?://(?:[^/]*\.)?(?:aws\.amazon\.com|docs\.aws\.amazon\.com"
    r"|nist\.gov|iso\.org|cloudsecurityalliance\.org|example\.(?:com|org))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    """One detected entity occurrence in a document."""

    entity_class: EntityClass
    start: int
    end: int
    surface: str  # the literal matched text
    canonical: str  # normalised form used to derive the token
    confidence: float  # 1.0 for pattern/dictionary hits
    detector: str  # "dictionary" | "pattern" | "comprehend"

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class RedactionProfile:
    """Which classes to redact and how aggressively."""

    name: str
    classes: frozenset[EntityClass]
    use_ml_detection: bool = True
    # Findings below this are treated as a failure, not as a pass. A weak
    # signal on a customer name is exactly the case where guessing is wrong.
    confidence_floor: float = 0.80
    # Above the floor but below this, the doc is quarantined for human review
    # rather than silently accepted.
    review_threshold: float = 0.95
    fail_closed: bool = True


STRICT = RedactionProfile(
    name="strict",
    classes=frozenset(EntityClass),
    use_ml_detection=True,
    confidence_floor=0.80,
    review_threshold=0.95,
)

STANDARD = RedactionProfile(
    name="standard",
    classes=frozenset(
        {
            EntityClass.CUSTOMER,
            EntityClass.PERSON,
            EntityClass.EMAIL,
            EntityClass.PHONE,
            EntityClass.CREDENTIAL,
            EntityClass.CONTRACT,
        }
    ),
    use_ml_detection=True,
    confidence_floor=0.75,
    review_threshold=0.90,
)

# Default for the POC.
#
# Customer *company* names pass through untouched — they are the citation key.
# An answer that cannot say "this is what we told Acme Financial in March"
# cannot support retrospective anomaly review, which is the point of the
# corpus. The name rides in sidecar metadata (see ingestion.connectors), so it
# is citable and filterable without entering the embedding.
#
# Individual contact details are a different category and still go. Knowing
# that an answer was given to Acme Financial does not require jane.doe's mobile
# number, and a corpus that accumulates named individuals acquires a retention
# obligation nobody signed up for. Credentials go for the obvious reason.
CITATION_SAFE = RedactionProfile(
    name="citation_safe",
    classes=frozenset(
        {
            EntityClass.PERSON,
            EntityClass.EMAIL,
            EntityClass.PHONE,
            EntityClass.CREDENTIAL,
        }
    ),
    use_ml_detection=False,  # dictionary + patterns only; no Comprehend spend
    confidence_floor=0.75,
    review_threshold=0.90,
)

# Escape hatch for corpora that are already synthetic. Still runs the
# credential detector -- a leaked key in "synthetic" data is still a leaked key.
OFF = RedactionProfile(
    name="off",
    classes=frozenset({EntityClass.CREDENTIAL}),
    use_ml_detection=False,
    confidence_floor=0.0,
    review_threshold=0.0,
)

PROFILES: dict[str, RedactionProfile] = {p.name: p for p in (STRICT, STANDARD, CITATION_SAFE, OFF)}


@dataclass
class RedactionResult:
    """Outcome of redacting one document."""

    text: str
    findings: list[Finding] = field(default_factory=list)
    token_map: dict[str, str] = field(default_factory=dict)  # token -> canonical
    quarantined: bool = False
    quarantine_reason: str | None = None

    @property
    def manifest(self) -> dict[str, Any]:
        """Auditable record of what was removed. Carries no raw surfaces --
        only classes, offsets and tokens -- so the manifest itself is safe to
        store alongside the sanitised corpus and to show a customer as
        evidence that the gate ran."""
        by_class: dict[str, int] = {}
        for f in self.findings:
            by_class[f.entity_class.value] = by_class.get(f.entity_class.value, 0) + 1
        return {
            "redaction_version": REDACTION_VERSION,
            "total_findings": len(self.findings),
            "findings_by_class": by_class,
            "tokens_emitted": sorted(self.token_map.keys()),
            "quarantined": self.quarantined,
            "quarantine_reason": self.quarantine_reason,
            "min_confidence": min((f.confidence for f in self.findings), default=1.0),
        }


class Redactor:
    """Pseudonymises document text.

    `known_entities` is the curated dictionary pass -- in the deployed system
    this is loaded from the CustomerRefs DynamoDB table, so the customer list
    the pipeline already maintains for reference-gating doubles as the
    redaction dictionary. One source of truth, not two.

    `ml_detect` is injected rather than imported so the engine stays unit
    testable without a Comprehend round-trip, and so the ML backend can be
    swapped without touching this module.
    """

    def __init__(
        self,
        secret: bytes | None = None,
        known_entities: dict[str, EntityClass] | None = None,
        ml_detect: (Callable[[str, AbstractSet[EntityClass]], Iterable[Finding]] | None) = None,
    ) -> None:
        self._secret = (
            secret
            or os.environ.get("REDACTION_HMAC_SECRET", "").encode()
            or b"dev-only-insecure-secret"
        )
        self._known = known_entities or {}
        self._ml_detect = ml_detect
        self._issued: dict[str, str] = {}  # canonical -> token, collision guard

    # -- token derivation ---------------------------------------------------

    def token_for(self, canonical: str, entity_class: EntityClass) -> str:
        digest = (
            hmac.new(
                self._secret,
                f"{entity_class.value}:{canonical}".encode(),
                hashlib.sha256,
            )
            .hexdigest()
            .upper()
        )

        token = f"[{entity_class.value}_{digest[:_TOKEN_WIDTH]}]"
        prior = self._issued.get(token)
        if prior is not None and prior != canonical:
            # Collision with a different entity -- widen for this one only so
            # tokens already issued elsewhere in the corpus stay stable.
            token = f"[{entity_class.value}_{digest[:_TOKEN_WIDTH_WIDE]}]"
        self._issued[token] = canonical
        return token

    # -- detection ----------------------------------------------------------

    def _dictionary_findings(self, text: str, profile: RedactionProfile) -> list[Finding]:
        out: list[Finding] = []
        # Longest first so "Acme Financial Corp" wins over "Acme".
        for name in sorted(self._known, key=len, reverse=True):
            entity_class = self._known[name]
            if entity_class not in profile.classes:
                continue
            for m in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
                out.append(
                    Finding(
                        entity_class=entity_class,
                        start=m.start(),
                        end=m.end(),
                        surface=m.group(0),
                        canonical=normalise_entity(m.group(0)),
                        confidence=1.0,
                        detector="dictionary",
                    )
                )
        return out

    def _pattern_findings(self, text: str, profile: RedactionProfile) -> list[Finding]:
        out: list[Finding] = []
        for entity_class, pattern in _PATTERNS.items():
            if entity_class not in profile.classes:
                continue
            for m in pattern.finditer(text):
                surface = m.group(0)
                if entity_class is EntityClass.IP and _IP_ALLOW.match(surface):
                    continue
                if entity_class is EntityClass.URL and _URL_ALLOW.match(surface):
                    continue
                out.append(
                    Finding(
                        entity_class=entity_class,
                        start=m.start(),
                        end=m.end(),
                        surface=surface,
                        canonical=normalise_entity(surface)
                        if entity_class in (EntityClass.CUSTOMER, EntityClass.PERSON)
                        else surface.lower(),
                        confidence=1.0,
                        detector="pattern",
                    )
                )
        return out

    @staticmethod
    def _resolve_overlaps(findings: Iterable[Finding]) -> list[Finding]:
        """Longest span wins; ties broken by detector precedence.

        An email address contains a domain that the URL detector may also
        claim, and a dictionary customer name may sit inside a longer ML-
        detected org span. Replacing both corrupts the text, so overlaps are
        resolved once, globally, before any substitution happens.
        """
        precedence = {"dictionary": 0, "pattern": 1, "comprehend": 2}
        ordered = sorted(
            findings,
            key=lambda f: (-f.length, precedence.get(f.detector, 9), f.start),
        )
        kept: list[Finding] = []
        for f in ordered:
            if any(f.start < k.end and k.start < f.end for k in kept):
                continue
            kept.append(f)
        return sorted(kept, key=lambda f: f.start)

    # -- main entry point ---------------------------------------------------

    def redact(
        self,
        text: str,
        profile: RedactionProfile = STRICT,
        # Accepted so callers can pass audit context without a second call
        # path; not yet threaded into RedactionResult. Wiring it is a change
        # to the quarantine record, not a lint fix.
        document_id: str | None = None,  # noqa: ARG002
    ) -> RedactionResult:
        if profile.name == "off" and not profile.classes:
            return RedactionResult(text=text)

        try:
            findings = self._dictionary_findings(text, profile)
            findings += self._pattern_findings(text, profile)

            if profile.use_ml_detection and self._ml_detect is not None:
                wanted = profile.classes & {
                    EntityClass.CUSTOMER,
                    EntityClass.PERSON,
                    EntityClass.ADDRESS,
                }
                if wanted:
                    findings += list(self._ml_detect(text, wanted))
        except Exception as exc:  # noqa: BLE001 -- fail closed on any detector error
            if profile.fail_closed:
                return RedactionResult(
                    text="",
                    quarantined=True,
                    quarantine_reason=f"detector error: {type(exc).__name__}: {exc}",
                )
            raise

        findings = self._resolve_overlaps(findings)

        # Confidence gate. A finding we are unsure about is the strongest
        # signal that a human should look, so it quarantines rather than
        # degrading to best-effort.
        weak = [f for f in findings if f.confidence < profile.confidence_floor]
        if weak and profile.fail_closed:
            classes = sorted({f.entity_class.value for f in weak})
            return RedactionResult(
                text="",
                findings=findings,
                quarantined=True,
                quarantine_reason=(
                    f"{len(weak)} finding(s) below confidence floor "
                    f"{profile.confidence_floor} in class(es) {', '.join(classes)}"
                ),
            )

        # Substitute right-to-left so earlier offsets stay valid.
        token_map: dict[str, str] = {}
        buf = text
        for f in sorted(findings, key=lambda f: f.start, reverse=True):
            token = self.token_for(f.canonical, f.entity_class)
            token_map[token] = f.canonical
            buf = buf[: f.start] + token + buf[f.end :]

        result = RedactionResult(text=buf, findings=findings, token_map=token_map)

        borderline = [
            f
            for f in findings
            if profile.confidence_floor <= f.confidence < profile.review_threshold
        ]
        if borderline:
            result.quarantined = True
            result.quarantine_reason = (
                f"{len(borderline)} finding(s) between confidence floor and "
                f"review threshold {profile.review_threshold} -- SME review required"
            )
        return result

    # -- verification -------------------------------------------------------

    def verify(self, sanitised: str, known_surfaces: Sequence[str]) -> list[str]:
        """Second-pass assertion that no known surface survived.

        Cheap, and it is the check worth running: it is the thing you show a
        customer as evidence, and it catches detector regressions that unit
        tests on synthetic text will not.
        """
        leaked: list[str] = []
        haystack = sanitised.lower()
        for surface in known_surfaces:
            if surface and surface.lower() in haystack:
                leaked.append(surface)
        return leaked
