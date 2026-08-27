"""Tests for the pre-ingest redaction gate."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet

import pytest

from shared.redaction import (
    OFF,
    STANDARD,
    STRICT,
    EntityClass,
    Finding,
    Redactor,
    normalise_entity,
)

SECRET = b"test-secret-not-for-production"

KNOWN = {
    "Acme Financial Corp": EntityClass.CUSTOMER,
    "Acme Financial": EntityClass.CUSTOMER,
    "Northwind Traders": EntityClass.CUSTOMER,
}


def make_redactor(ml_detect: MlDetect | None = None) -> Redactor:
    return Redactor(secret=SECRET, known_entities=KNOWN, ml_detect=ml_detect)


MlDetect = Callable[[str, AbstractSet[EntityClass]], Iterable[Finding]]


# -- normalisation ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Acme Financial Corp", "acme financial"),
        ("Acme Financial Corporation", "acme financial"),
        ("Acme Financial", "acme financial"),
        ("ACME FINANCIAL, INC.", "acme financial"),
        ("Acme Systems Group Ltd", "acme"),
    ],
)
def test_legal_suffixes_collapse_to_one_canonical_form(raw: str, expected: str) -> None:
    assert normalise_entity(raw) == expected


def test_normalisation_does_not_eat_a_single_word_name() -> None:
    # "Group" alone must survive -- stripping to empty would map every
    # single-suffix-word entity to the same token.
    assert normalise_entity("Group") == "group"


# -- determinism, the property the whole design rests on --------------------


def test_same_customer_yields_same_token_across_documents() -> None:
    r = make_redactor()
    a = r.redact("Acme Financial Corp uses AES-256.", STRICT)
    b = r.redact("We answered this for Acme Financial last year.", STRICT)

    token_a = a.text.split()[0]
    assert token_a.startswith("[CUSTOMER_")
    assert token_a in b.text, "same customer must resolve to same token"


def test_different_customers_yield_different_tokens() -> None:
    r = make_redactor()
    out = r.redact("Acme Financial Corp and Northwind Traders both asked.", STRICT)
    tokens = {t for t in out.text.split() if t.startswith("[CUSTOMER_")}
    assert len(tokens) == 2


def test_tokens_are_stable_across_redactor_instances() -> None:
    # Re-ingest must not renumber the corpus.
    first = make_redactor().redact("Northwind Traders asked.", STRICT).text
    second = make_redactor().redact("Northwind Traders asked.", STRICT).text
    assert first == second


def test_token_is_not_reversible_without_the_secret() -> None:
    a = Redactor(secret=b"secret-one", known_entities=KNOWN).redact("Northwind Traders", STRICT)
    b = Redactor(secret=b"secret-two", known_entities=KNOWN).redact("Northwind Traders", STRICT)
    assert a.text != b.text


# -- pattern detectors ------------------------------------------------------


def test_email_and_phone_are_redacted() -> None:
    r = make_redactor()
    out = r.redact("Contact jane.doe@acmefin.com or +1 416 555 0142.", STRICT)
    assert "jane.doe@acmefin.com" not in out.text
    assert "[EMAIL_" in out.text
    assert "[PHONE_" in out.text


def test_credentials_are_caught_even_under_the_off_profile() -> None:
    r = make_redactor()
    out = r.redact("key AKIAIOSFODNN7EXAMPLE rotated", OFF)
    assert "AKIAIOSFODNN7EXAMPLE" not in out.text


def test_documentation_ranges_are_not_redacted() -> None:
    # Redacting RFC 5737 / RFC 1918 examples adds noise and degrades answers.
    r = make_redactor()
    out = r.redact("Bind to 10.0.1.5 and document 192.0.2.44.", STRICT)
    assert "10.0.1.5" in out.text
    assert "192.0.2.44" in out.text


def test_public_reference_urls_survive() -> None:
    r = make_redactor()
    text = "See https://docs.aws.amazon.com/kms/latest/developerguide/rotate.html"
    assert "docs.aws.amazon.com" in r.redact(text, STRICT).text


def test_version_strings_are_not_mistaken_for_phone_numbers() -> None:
    r = make_redactor()
    out = r.redact("Upgraded to TLS 1.3 on port 8443.", STRICT)
    assert "[PHONE_" not in out.text


# -- overlap resolution -----------------------------------------------------


def test_longest_span_wins_and_text_is_not_corrupted() -> None:
    r = make_redactor()
    out = r.redact("Acme Financial Corp confirmed.", STRICT)
    # "Acme Financial" is also a dictionary entry and sits inside the longer
    # match. Exactly one token, no residue.
    assert out.text.count("[CUSTOMER_") == 1
    assert "Financial" not in out.text
    assert out.text.endswith(" confirmed.")


def test_email_beats_overlapping_url_detection() -> None:
    r = make_redactor()
    out = r.redact("mail to ops@acmefin.com now", STRICT)
    assert out.text.count("[") == 1


def test_offsets_stay_valid_with_many_findings() -> None:
    r = make_redactor()
    text = (
        "Acme Financial Corp (a@x.com), Northwind Traders (b@y.com), "
        "ref MSA-2024-00184, +1 416 555 0142."
    )
    out = r.redact(text, STRICT)
    for surface in ("a@x.com", "b@y.com", "Acme Financial", "Northwind"):
        assert surface not in out.text
    assert out.text.startswith("[CUSTOMER_")


# -- fail-closed behaviour --------------------------------------------------


def test_low_confidence_finding_quarantines_the_document() -> None:
    def weak_ml(text: str, wanted: AbstractSet[EntityClass]) -> Iterable[Finding]:
        yield Finding(
            entity_class=EntityClass.CUSTOMER,
            start=0,
            end=6,
            surface="Vertex",
            canonical="vertex",
            confidence=0.55,
            detector="comprehend",
        )

    out = make_redactor(ml_detect=weak_ml).redact("Vertex asked us.", STRICT)
    assert out.quarantined
    assert out.text == "", "quarantined documents must not emit text"
    assert out.quarantine_reason is not None
    assert "confidence floor" in out.quarantine_reason


def test_borderline_confidence_redacts_but_flags_for_review() -> None:
    def borderline_ml(text: str, wanted: AbstractSet[EntityClass]) -> Iterable[Finding]:
        yield Finding(
            entity_class=EntityClass.CUSTOMER,
            start=0,
            end=6,
            surface="Vertex",
            canonical="vertex",
            confidence=0.88,
            detector="comprehend",
        )

    out = make_redactor(ml_detect=borderline_ml).redact("Vertex asked us.", STRICT)
    assert out.quarantined
    assert "Vertex" not in out.text, "text is still redacted, just held back"
    assert out.quarantine_reason is not None
    assert "SME review" in out.quarantine_reason


def test_detector_exception_fails_closed() -> None:
    def exploding_ml(text: str, wanted: AbstractSet[EntityClass]) -> Iterable[Finding]:
        raise RuntimeError("comprehend throttled")

    out = make_redactor(ml_detect=exploding_ml).redact("Anything.", STRICT)
    assert out.quarantined
    assert out.text == ""
    assert out.quarantine_reason is not None
    assert "comprehend throttled" in out.quarantine_reason


# -- manifest ---------------------------------------------------------------


def test_manifest_carries_no_raw_surfaces() -> None:
    r = make_redactor()
    out = r.redact("Acme Financial Corp, jane@acmefin.com", STRICT)
    blob = repr(out.manifest)
    assert "Acme" not in blob
    assert "jane@acmefin.com" not in blob
    assert out.manifest["total_findings"] == 2


def test_manifest_counts_by_class() -> None:
    r = make_redactor()
    out = r.redact("a@x.com and b@y.com and Northwind Traders", STRICT)
    assert out.manifest["findings_by_class"]["EMAIL"] == 2
    assert out.manifest["findings_by_class"]["CUSTOMER"] == 1


# -- verification pass ------------------------------------------------------


def test_verify_detects_a_leaked_surface() -> None:
    r = make_redactor()
    leaked = r.verify("Northwind Traders slipped through", ["Northwind Traders"])
    assert leaked == ["Northwind Traders"]


def test_verify_is_clean_on_properly_redacted_text() -> None:
    r = make_redactor()
    out = r.redact("Northwind Traders asked about SOC 2.", STRICT)
    assert r.verify(out.text, ["Northwind Traders"]) == []


# -- profiles ---------------------------------------------------------------


def test_standard_profile_leaves_infrastructure_detail_alone() -> None:
    r = make_redactor()
    text = "Endpoint https://internal.corp/api at 198.18.0.7"
    out = r.redact(text, STANDARD)
    assert "198.18.0.7" in out.text, "STANDARD excludes IP"
    assert "internal.corp" in out.text, "STANDARD excludes URL"


# -- regression: partial redaction ------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Call +1 416 555 0142.",  # sentence-final period
        "Call +1 416 555 0142",  # end of string
        "Call +1 416 555 0142, then...",  # trailing comma
        "Reach (416) 555 0142.",  # parenthesised area code
    ],
)
def test_phone_is_fully_redacted_never_partially(text: str) -> None:
    out = make_redactor().redact(text, STRICT)
    assert "0142" not in out.text, f"partial redaction leaked digits: {out.text!r}"
    assert "555" not in out.text
    assert "[PHONE_" in out.text


def test_dotted_quad_is_not_partially_eaten_by_phone_detector() -> None:
    out = make_redactor().redact("Host 198.18.0.7 responded.", STANDARD)
    assert "198.18.0.7" in out.text, "STANDARD excludes IP; phone must not claim it"
