"""Tests for provenance citation and retrospective divergence detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from divergence.detector import (
    ClaimRelation,
    Comparator,
    DivergenceDetector,
    DivergenceKind,
    PriorStatement,
)
from shared.models import Tier
from shared.provenance import Provenance, render_citation

NOW = datetime.now(UTC)


def prov(
    customer: str | None = "Acme Financial Corp",
    customer_id: str | None = "cust-acme",
    days_ago: int = 30,
    expires_in: int | None = 365,
    **kw: Any,
) -> Provenance:
    return Provenance(
        document_id=kw.pop("document_id", "doc-1"),
        source_id="golden-questionnaire",
        audience=kw.pop("audience", "customer"),
        customer_name=customer,
        customer_id=customer_id,
        answered_at=NOW - timedelta(days=days_ago),
        expires_on=None if expires_in is None else NOW + timedelta(days=expires_in),
        **kw,
    )


def stmt(sid: str, answer: str, provenance: Provenance) -> PriorStatement:
    return PriorStatement(
        statement_id=sid,
        question_text="Describe your key rotation policy.",
        answer_text=answer,
        provenance=provenance,
    )


def comparator(relation: ClaimRelation) -> Comparator:
    def _c(question: str, left: str, right: str) -> ClaimRelation:
        return relation

    return _c


# -- citation rendering -----------------------------------------------------


def test_citation_leads_with_customer_and_date() -> None:
    line = render_citation(
        prov(
            questionnaire_name="Vendor Security Assessment",
            approved_by="R. Sandhu",
        )
    )
    assert line.startswith("Prior answer — Acme Financial Corp")
    assert "Vendor Security Assessment" in line
    assert "approved by R. Sandhu" in line


def test_expired_evidence_is_flagged_in_the_citation() -> None:
    line = render_citation(prov(expires_in=-1))
    assert line.endswith("(EXPIRED)")


def test_certification_and_public_audiences_do_not_name_a_customer() -> None:
    assert "Certification evidence" in render_citation(
        prov(customer=None, customer_id=None, audience="certification")
    )
    assert "Published" in render_citation(prov(customer=None, customer_id=None, audience="public"))


def test_internal_source_without_customer_still_renders() -> None:
    line = render_citation(prov(customer=None, customer_id=None, audience="internal"))
    assert "Internal documentation" in line


# -- every citation names the document it came from -------------------------
#
# Workflow step 4 is "sources quoted against the answers". A product-doc or
# compliance sidecar carries no customer and no questionnaire, so the citation
# line used to be the bare literal "Internal documentation", repeated once per
# source: an SME reading the cell comment could not tell which document backed
# the answer, or that two bullets were two different documents.


def _product_doc() -> Provenance:
    """The sidecar shape data/corpus-real/product-docs actually ships."""
    return Provenance(
        document_id="encryption_at_rest_aes256gcm",
        source_id="product_docs",
        audience="internal",
        topic_ids=["encryption"],
    )


def _compliance(document_id: str, *, expires_in_days: int) -> Provenance:
    """The sidecar shape data/corpus-real/compliance actually ships."""
    return Provenance(
        document_id=document_id,
        source_id="compliance_store",
        audience="certification",
        expires_on=NOW + timedelta(days=expires_in_days),
    )


def test_a_product_doc_citation_names_its_document() -> None:
    line = render_citation(_product_doc())

    assert "encryption_at_rest_aes256gcm" in line
    assert line.startswith("Internal documentation")


def test_two_product_docs_do_not_render_as_the_same_line() -> None:
    """The failure an SME actually saw: two bullets, identical text."""
    first = render_citation(_product_doc())
    second = render_citation(
        Provenance(document_id="key_management_byok_hsm", source_id="product_docs")
    )

    assert first != second


def test_a_compliance_citation_names_the_report() -> None:
    line = render_citation(_compliance("soc2_type2_2026", expires_in_days=365))

    assert "soc2_type2_2026" in line
    assert line.startswith("Certification evidence")
    assert "(EXPIRED)" not in line


def test_an_expired_compliance_citation_names_the_report_that_expired() -> None:
    """CAIQ-015/016: "Certification evidence (EXPIRED)" told the SME something
    was stale without saying which report, which is half a warning."""
    line = render_citation(_compliance("soc2_type2_2024_expired", expires_in_days=-30))

    assert "soc2_type2_2024_expired" in line
    assert line.endswith("(EXPIRED)")


def test_an_sme_approved_answer_names_its_document_and_keeps_its_approver() -> None:
    line = render_citation(
        Provenance(
            document_id="lfb-f30d354ab6a08d65",
            source_id="sme_approved_answers",
            audience="internal",
            approved_at=NOW - timedelta(days=1),
            approved_by="r.calloway@echoworx.example",
        )
    )

    assert "lfb-f30d354ab6a08d65" in line
    assert "approved by r.calloway@echoworx.example" in line


def test_a_prior_rfp_citation_keeps_the_rich_form_and_not_the_raw_id() -> None:
    """Regression guard on the fix. Where the sidecar names a customer and a
    questionnaire, that *is* the document's name and the id would be noise."""
    line = render_citation(
        prov(
            document_id="northwind_mutual_caiq_2026",
            questionnaire_name="CAIQ v4.0.3",
            approved_by="l.fontaine@echoworx.example",
        )
    )

    assert line.startswith("Prior answer — Acme Financial Corp")
    assert "CAIQ v4.0.3" in line
    assert "approved by l.fontaine@echoworx.example" in line
    assert "northwind_mutual_caiq_2026" not in line


def test_a_citation_with_no_document_id_still_renders_its_audience() -> None:
    """A malformed sidecar costs the document name, never the whole line."""
    line = render_citation(
        Provenance(document_id="", source_id="product_docs", audience="internal")
    )

    assert line == "Internal documentation"


# -- sidecar round trip -----------------------------------------------------


def test_customer_name_survives_the_sidecar_round_trip() -> None:
    original = prov(questionnaire_id="vsa-2026-014", approved_by="R. Sandhu")
    restored = Provenance.from_sidecar(original.to_sidecar())
    assert restored.customer_name == "Acme Financial Corp"
    assert restored.customer_id == "cust-acme"
    assert restored.questionnaire_id == "vsa-2026-014"


def test_sidecar_dates_are_iso_strings_so_filters_compare_lexically() -> None:
    side = prov().to_sidecar()
    assert isinstance(side["answered_at"], str)
    # Lexical comparison is what makes a KB metadata filter on date work.
    assert side["answered_at"] > "2020-01-01"


def test_sidecar_stays_well_under_the_s3_vectors_metadata_budget() -> None:
    side = prov(
        questionnaire_name="Vendor Security Assessment 2026",
        approved_by="R. Sandhu",
        topic_ids=["encryption", "key_management", "data_protection"],
        evidence_refs=["soc2_2025_cc6.1", "iso27001_a10.1"],
    ).to_sidecar()
    assert len(repr(side).encode()) < 2048


def test_sidecar_omits_empty_fields_rather_than_emitting_nulls() -> None:
    side = prov(customer=None, customer_id=None).to_sidecar()
    assert "customer_name" not in side
    assert "customer_id" not in side


# -- the classification matrix ----------------------------------------------


def test_two_customers_contradicting_in_the_same_period_is_red() -> None:
    a = stmt("a", "Keys rotate annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Keys rotate quarterly.", prov("Northwind Traders", "cust-nw", 40))

    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b])

    assert len(report.findings) == 1
    assert report.findings[0].kind is DivergenceKind.CUSTOMER_DIVERGENCE
    assert report.tier_floor is Tier.RED
    assert "Acme Financial Corp" in report.findings[0].detail
    assert "Northwind Traders" in report.findings[0].detail


def test_two_customers_contradicting_years_apart_is_amber_not_red() -> None:
    a = stmt("a", "Keys rotate annually.", prov("Acme Financial Corp", "cust-acme", 900))
    b = stmt("b", "Keys rotate quarterly.", prov("Northwind Traders", "cust-nw", 20))

    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b])

    assert report.findings[0].kind is DivergenceKind.UNRESOLVED_DIVERGENCE
    assert report.tier_floor is Tier.AMBER


def test_same_customer_contradiction_is_red_even_when_far_apart() -> None:
    # They hold both documents. Time does not soften it.
    a = stmt("a", "Keys rotate annually.", prov("Acme Financial Corp", "cust-acme", 900))
    b = stmt("b", "Keys rotate quarterly.", prov("Acme Financial Corp", "cust-acme", 20))

    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b])

    assert report.findings[0].kind is DivergenceKind.SELF_CONTRADICTION
    assert report.tier_floor is Tier.RED


def test_customer_grouping_uses_stable_id_not_display_name() -> None:
    # Same customer, renamed after acquisition. Must not read as two parties.
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 400))
    b = stmt("b", "Quarterly.", prov("Acme Financial (a Globex company)", "cust-acme", 10))

    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b])

    assert report.findings[0].kind is DivergenceKind.SELF_CONTRADICTION


def test_agreement_produces_no_finding() -> None:
    a = stmt("a", "Keys rotate annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Keys are rotated once a year.", prov("Northwind Traders", "cust-nw", 20))

    report = DivergenceDetector(comparator(ClaimRelation.AGREE)).detect([a, b])

    assert report.findings == []
    assert report.tier_floor is None
    assert report.compared_pairs == 1


def test_unrelated_answers_produce_no_finding() -> None:
    a = stmt("a", "Keys rotate annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "We run quarterly pen tests.", prov("Northwind Traders", "cust-nw", 20))

    report = DivergenceDetector(comparator(ClaimRelation.UNRELATED)).detect([a, b])

    assert report.findings == []


# -- failure and edge behaviour ---------------------------------------------


def test_uncertain_comparator_does_not_read_as_agreement() -> None:
    a = stmt("a", "Keys rotate annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Rotation is automated.", prov("Northwind Traders", "cust-nw", 20))

    report = DivergenceDetector(comparator(ClaimRelation.UNCERTAIN)).detect([a, b])

    assert report.findings[0].kind is DivergenceKind.UNRESOLVED_DIVERGENCE
    assert report.tier_floor is Tier.AMBER


def test_comparator_exception_fails_to_uncertain_not_to_clean() -> None:
    def exploding(question: str, left: str, right: str) -> ClaimRelation:
        raise RuntimeError("bedrock throttled")

    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Quarterly.", prov("Northwind Traders", "cust-nw", 20))

    report = DivergenceDetector(exploding).detect([a, b])

    assert report.tier_floor is Tier.AMBER, "a failed comparison is not a pass"


def test_consistent_but_wholly_expired_priors_are_flagged() -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 800, expires_in=-30))
    b = stmt("b", "Annually.", prov("Northwind Traders", "cust-nw", 700, expires_in=-10))

    report = DivergenceDetector(comparator(ClaimRelation.AGREE)).detect([a, b])

    assert report.findings[0].kind is DivergenceKind.STALE_CONSENSUS
    assert report.tier_floor is Tier.AMBER


def test_single_unexpired_prior_is_clean() -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 10))
    report = DivergenceDetector(comparator(ClaimRelation.AGREE)).detect([a])
    assert report.findings == []


def test_single_expired_prior_is_flagged() -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 800, expires_in=-30))
    report = DivergenceDetector(comparator(ClaimRelation.AGREE)).detect([a])
    assert report.findings[0].kind is DivergenceKind.STALE_CONSENSUS


def test_empty_input_is_clean() -> None:
    report = DivergenceDetector(comparator(ClaimRelation.AGREE)).detect([])
    assert report.findings == []
    assert report.statements_examined == 0


def test_pair_count_is_capped_to_bound_model_spend() -> None:
    many = [
        stmt(f"s{i}", f"Answer {i}.", prov(f"Customer {i}", f"cust-{i}", i + 1))
        for i in range(30)  # 435 unbounded pairs
    ]
    detector = DivergenceDetector(comparator(ClaimRelation.AGREE), max_pairs=25)
    report = detector.detect(many)
    assert report.compared_pairs == 25


def test_red_dominates_amber_in_the_report_floor() -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Quarterly.", prov("Acme Financial Corp", "cust-acme", 20))
    c = stmt("c", "Monthly.", prov("Northwind Traders", "cust-nw", 900))

    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b, c])

    kinds = {f.kind for f in report.findings}
    assert DivergenceKind.SELF_CONTRADICTION in kinds
    assert report.tier_floor is Tier.RED


# -- reviewer-facing output --------------------------------------------------


def test_finding_renders_a_scannable_reviewer_line() -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Quarterly.", prov("Northwind Traders", "cust-nw", 40))
    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b])

    line = report.findings[0].render()
    assert line.startswith("[CUSTOMER DIVERGENCE]")
    assert "Acme Financial Corp" in line


@pytest.mark.parametrize(
    "days,phrase",
    [(0, "same day"), (14, "14 days apart"), (200, "months apart"), (900, "years apart")],
)
def test_time_gaps_are_described_in_human_terms(days: int, phrase: str) -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 1))
    b = stmt("b", "Quarterly.", prov("Northwind Traders", "cust-nw", 1 + days))
    report = DivergenceDetector(comparator(ClaimRelation.CONTRADICT), period_days=10).detect([a, b])
    assert phrase in report.findings[0].detail


def test_summary_is_serialisable_for_the_audit_record() -> None:
    a = stmt("a", "Annually.", prov("Acme Financial Corp", "cust-acme", 10))
    b = stmt("b", "Quarterly.", prov("Northwind Traders", "cust-nw", 40))
    summary = DivergenceDetector(comparator(ClaimRelation.CONTRADICT)).detect([a, b]).summary
    assert summary["findings"] == 1
    assert summary["tier_floor"] == "red"
    assert summary["findings_by_kind"]["customer_divergence"] == 1
