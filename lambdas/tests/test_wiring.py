"""Tests for the §5 immediate-wiring items in HANDOFF.md.

Four seams, one file:
  1. retriever  — sidecar attributes become a typed Provenance.
  2. hard_rules — DivergenceReport.tier_floor is enforced as rule 6.
  3. excel_writer — citations and findings reach the reviewer comment.
  4. comparator — the narrow Haiku call, and every way it is allowed to fail.

The recurring assertion across all four is the same one: nothing here may make
an answer look cleaner than the evidence supports.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from divergence.comparator import BedrockComparator, parse_relation
from divergence.detector import (
    ClaimRelation,
    DivergenceDetector,
    DivergenceKind,
    PriorStatement,
)
from excel_writer.writer import _comment_body
from hard_rules.rules import apply_rules
from retriever import handler
from retriever.handler import _knowledge_base_retrieve
from shared.models import CellRef, ConfidenceBreakdown, FinalAnswer, Tier
from shared.provenance import Citation, Provenance

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# 1. retriever/handler.py — hydrate RetrievedPassage.metadata into Provenance
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _kb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-test-0001")


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr(handler, "_bedrock_agent_runtime", lambda: client)
    return client


def _hit(md: dict[str, Any], *, uri: str = "s3://bucket/prior-rfps/a.md") -> dict[str, Any]:
    return {
        "content": {"text": "We rotate customer-managed keys annually."},
        "location": {"type": "S3", "s3Location": {"uri": uri}},
        "score": 0.81,
        "metadata": md,
    }


_FULL_SIDECAR = {
    "document_id": "acme_vsa_2026",
    "source_id": "golden-questionnaire",
    "audience": "customer",
    "customer_name": "Acme Financial Corp",
    "customer_id": "cust-acme",
    "questionnaire_name": "Vendor Security Assessment",
    "answered_at": "2026-03-11T00:00:00+00:00",
    "approved_by": "R. Sandhu",
    "expires_on": "2027-03-11T00:00:00+00:00",
    "topic_ids": ["key_management"],
}


def test_full_sidecar_becomes_typed_provenance(mock_client: MagicMock) -> None:
    mock_client.retrieve.return_value = {"retrievalResults": [_hit(_FULL_SIDECAR)]}

    (passage,) = _knowledge_base_retrieve(
        "key rotation", source="prior_rfps", source_system="historical_rfp"
    )

    prov = passage.provenance
    assert prov is not None
    assert prov.customer_name == "Acme Financial Corp"
    assert prov.customer_id == "cust-acme"
    assert prov.audience == "customer"
    assert prov.answered_at == datetime(2026, 3, 11, tzinfo=UTC)
    assert prov.approved_by == "R. Sandhu"
    assert prov.is_expired is False
    # The whole sidecar still travels as raw metadata, including list values.
    assert passage.metadata["customer_id"] == "cust-acme"
    assert passage.metadata["topic_ids"] == ["key_management"]
    assert passage.metadata["s3_uri"] == "s3://bucket/prior-rfps/a.md"


def test_provenance_renders_a_citation_an_sme_can_scan(mock_client: MagicMock) -> None:
    mock_client.retrieve.return_value = {"retrievalResults": [_hit(_FULL_SIDECAR)]}

    (passage,) = _knowledge_base_retrieve(
        "key rotation", source="prior_rfps", source_system="historical_rfp"
    )

    line = Citation(provenance=passage.provenance).render()  # type: ignore[arg-type]
    assert line.startswith("Prior answer — Acme Financial Corp")
    assert "11 Mar 2026" in line
    assert "approved by R. Sandhu" in line


def test_malformed_sidecar_degrades_provenance_but_keeps_the_passage(
    mock_client: MagicMock,
) -> None:
    """A bad attribute costs us the citation, never the evidence."""
    mock_client.retrieve.return_value = {
        "retrievalResults": [_hit({"document_id": "d1", "audience": "not-a-real-audience"})]
    }

    passages = _knowledge_base_retrieve(
        "key rotation", source="prior_rfps", source_system="historical_rfp"
    )

    assert len(passages) == 1
    assert passages[0].document_id == "d1"
    assert passages[0].provenance is None


def test_sidecar_missing_source_id_falls_back_to_the_dispatch_source(
    mock_client: MagicMock,
) -> None:
    mock_client.retrieve.return_value = {"retrievalResults": [_hit({"document_id": "d1"})]}

    (passage,) = _knowledge_base_retrieve(
        "key rotation", source="prior_rfps", source_system="historical_rfp"
    )

    assert passage.provenance is not None
    assert passage.provenance.source_id == "prior_rfps"


def test_expired_sidecar_is_flagged_expired(mock_client: MagicMock) -> None:
    stale = dict(_FULL_SIDECAR, expires_on=(NOW - timedelta(days=30)).isoformat())
    mock_client.retrieve.return_value = {"retrievalResults": [_hit(stale)]}

    (passage,) = _knowledge_base_retrieve(
        "key rotation", source="prior_rfps", source_system="historical_rfp"
    )

    assert passage.provenance is not None
    assert passage.provenance.is_expired is True
    assert "(EXPIRED)" in Citation(provenance=passage.provenance).render()


# ---------------------------------------------------------------------------
# 2. hard_rules/rules.py — DivergenceReport.tier_floor as rule 6
# ---------------------------------------------------------------------------

BENIGN = "Access to production systems is limited to named administrators."


def _rules(current: Tier, floor: Tier | None, kinds: tuple[str, ...] = ()) -> Any:
    return apply_rules(
        answer_text=BENIGN,
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=current,
        divergence_tier_floor=floor,
        divergence_kinds=kinds,
    )


def test_red_floor_forces_red_from_green() -> None:
    result = _rules(Tier.GREEN, Tier.RED, ("customer_divergence",))
    assert result.final_tier == Tier.RED
    assert "divergence:customer_divergence" in result.triggers
    assert result.reviewer_required is True


def test_amber_floor_demotes_green() -> None:
    result = _rules(Tier.GREEN, Tier.AMBER, ("stale_consensus",))
    assert result.final_tier == Tier.AMBER
    assert "divergence:stale_consensus" in result.triggers


def test_amber_floor_cannot_promote_red() -> None:
    """Hard rules only ever demote — the floor is a floor, not a setting."""
    result = _rules(Tier.RED, Tier.AMBER, ("unresolved_divergence",))
    assert result.final_tier == Tier.RED


def test_no_divergence_leaves_the_tier_untouched() -> None:
    result = _rules(Tier.GREEN, None)
    assert result.final_tier == Tier.GREEN
    assert result.triggers == []


def test_repeated_kinds_produce_one_trigger_each() -> None:
    result = _rules(Tier.GREEN, Tier.RED, ("self_contradiction", "self_contradiction"))
    assert result.triggers.count("divergence:self_contradiction") == 1


def test_floor_without_kinds_still_labels_the_trigger() -> None:
    result = _rules(Tier.GREEN, Tier.AMBER)
    assert result.triggers == ["divergence:amber"]


def test_divergence_trigger_collapses_in_the_summary_checklist() -> None:
    """The Summary sheet groups on the prefix before ':', so every divergence
    kind should roll up under one 'divergence' row rather than fragmenting."""
    result = _rules(Tier.GREEN, Tier.RED, ("customer_divergence", "self_contradiction"))
    keys = {t.split(":", 1)[0] for t in result.triggers}
    assert keys == {"divergence"}


def test_pricing_red_survives_an_amber_divergence_floor() -> None:
    result = apply_rules(
        answer_text="Enterprise pricing starts at $50 per user per month.",
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.GREEN,
        divergence_tier_floor=Tier.AMBER,
        divergence_kinds=("temporal_drift",),
    )
    assert result.final_tier == Tier.RED


def test_detector_report_floor_feeds_the_rule_end_to_end() -> None:
    """The two halves meet: a real DivergenceReport drives a real tier."""

    def prov(customer: str, cust_id: str, days_ago: int) -> Provenance:
        return Provenance(
            document_id=f"doc-{cust_id}-{days_ago}",
            source_id="golden-questionnaire",
            audience="customer",
            customer_name=customer,
            customer_id=cust_id,
            answered_at=NOW - timedelta(days=days_ago),
        )

    statements = [
        PriorStatement(
            "s1",
            "Key rotation?",
            "Keys rotate annually.",
            prov("Acme Financial Corp", "cust-acme", 20),
        ),
        PriorStatement(
            "s2",
            "Key rotation?",
            "Keys rotate every 90 days.",
            prov("Northwind Capital", "cust-northwind", 30),
        ),
    ]
    report = DivergenceDetector(lambda *_: ClaimRelation.CONTRADICT).detect(statements)

    assert report.tier_floor is Tier.RED
    assert report.findings[0].kind is DivergenceKind.CUSTOMER_DIVERGENCE

    result = apply_rules(
        answer_text=BENIGN,
        invoked_customers=[],
        approved_reference_customers=[],
        current_tier=Tier.GREEN,
        divergence_tier_floor=report.tier_floor,
        divergence_kinds=[f.kind.value for f in report.findings],
    )
    assert result.final_tier == Tier.RED


# ---------------------------------------------------------------------------
# 3. excel_writer/writer.py — render citations + findings into the comment
# ---------------------------------------------------------------------------


def _answer(**kw: Any) -> FinalAnswer:
    defaults: dict[str, Any] = dict(
        question_id="q1",
        answer_text="Keys rotate every 90 days.",
        citations=["acme_vsa_2026"],
        raw_confidence=0.71,
        tier=Tier.AMBER,
        confidence_breakdown=ConfidenceBreakdown(h=0.7, r=0.6, c=0.5, f=0.9, g=1.0),
        hard_rule_triggers=["divergence:customer_divergence"],
        reviewer_required=True,
        answer_cell=CellRef(sheet="Sheet1", coordinate="B2"),
        confidence_cell=CellRef(sheet="Sheet1", coordinate="C2"),
    )
    defaults.update(kw)
    return FinalAnswer(**defaults)


def _citation(**kw: Any) -> Citation:
    return Citation(
        provenance=Provenance(
            **{
                "document_id": "acme_vsa_2026",
                "source_id": "golden-questionnaire",
                "audience": "customer",
                "customer_name": "Acme Financial Corp",
                "questionnaire_name": "Vendor Security Assessment",
                "answered_at": datetime(2026, 3, 11, tzinfo=UTC),
                "approved_by": "R. Sandhu",
                **kw,
            }
        )
    )


def test_comment_renders_the_citation_line_not_the_document_id() -> None:
    body = _comment_body(_answer(citations_detailed=[_citation()]))
    assert "Prior answer — Acme Financial Corp" in body
    assert "11 Mar 2026" in body
    assert "acme_vsa_2026" not in body  # the raw id is superseded, not appended


def test_comment_falls_back_to_document_ids_without_provenance() -> None:
    body = _comment_body(_answer())
    assert "Citations:" in body
    assert "acme_vsa_2026" in body


def test_comment_leads_with_divergence_findings() -> None:
    body = _comment_body(
        _answer(
            divergence_findings=[
                "[CUSTOMER DIVERGENCE] Acme Financial Corp and Northwind Capital were "
                "given contradictory answers 10 days apart.",
            ],
            citations_detailed=[_citation()],
        )
    )
    lines = body.splitlines()
    assert "Divergence across prior answers:" in lines
    # Ahead of confidence and sources — an SME who reads one block reads this one.
    assert lines.index("Divergence across prior answers:") < lines.index("Sources:")
    assert any(line.startswith("Confidence:") for line in lines)
    assert lines.index("Divergence across prior answers:") < next(
        i for i, line in enumerate(lines) if line.startswith("Confidence:")
    )


def test_comment_truncates_long_finding_lists_visibly() -> None:
    body = _comment_body(_answer(divergence_findings=[f"[X] finding {i}" for i in range(7)]))
    assert "finding 3" in body
    assert "finding 4" not in body
    assert "… and 3 more" in body


def test_expired_source_keeps_its_marker_in_the_comment() -> None:
    body = _comment_body(
        _answer(
            citations_detailed=[_citation(expires_on=NOW - timedelta(days=1))],
        )
    )
    assert "(EXPIRED)" in body


# ---------------------------------------------------------------------------
# 4. divergence/comparator.py — the narrow Haiku call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("CONTRADICT", ClaimRelation.CONTRADICT),
        ("  agree\n", ClaimRelation.AGREE),
        ("UNRELATED.", ClaimRelation.UNRELATED),
        ("uncertain", ClaimRelation.UNCERTAIN),
        ("The answers appear to AGREE.", ClaimRelation.AGREE),
        ("banana", ClaimRelation.UNCERTAIN),  # unrecognised
        ("AGREE or UNRELATED", ClaimRelation.UNCERTAIN),  # two calls is no call
        ("", ClaimRelation.UNCERTAIN),
        (None, ClaimRelation.UNCERTAIN),
    ],
)
def test_parse_relation(reply: str | None, expected: ClaimRelation) -> None:
    assert parse_relation(reply) is expected


def _stub_invoke(monkeypatch: pytest.MonkeyPatch, text: str) -> list[dict[str, Any]]:
    """Patch shared.bedrock_client.invoke; return the list of captured calls."""
    import shared.bedrock_client as bc

    calls: list[dict[str, Any]] = []

    def fake_invoke(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"text": text, "response_hash": "deadbeef"}

    monkeypatch.setattr(bc, "invoke", fake_invoke)
    return calls


def test_comparator_returns_the_parsed_relation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_invoke(monkeypatch, "CONTRADICT")
    cmp = BedrockComparator()

    assert cmp("Key rotation?", "Annually.", "Every 90 days.") is ClaimRelation.CONTRADICT
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["cache_system"] is True
    assert "haiku" in calls[0]["model_id"]


def test_comparator_prompt_carries_only_the_two_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model must not see dates, customers or tiers — it decides one thing."""
    calls = _stub_invoke(monkeypatch, "AGREE")
    BedrockComparator()("Key rotation?", "Annually.", "Once a year.")

    prompt = calls[0]["user_prompt"] + calls[0]["system_prompt"]
    for leak in ("Acme", "customer_id", "RED", "AMBER", "tier", "2026-"):
        assert leak not in prompt


def test_bedrock_failure_degrades_to_uncertain_never_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shared.bedrock_client as bc

    def boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("ThrottlingException")

    monkeypatch.setattr(bc, "invoke", boom)
    cmp = BedrockComparator()

    assert cmp("Key rotation?", "Annually.", "Every 90 days.") is ClaimRelation.UNCERTAIN
    assert cmp.errors == 1


def test_throttled_comparator_still_produces_an_amber_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: a broken comparator must not read as a clean answer."""
    import shared.bedrock_client as bc

    monkeypatch.setattr(bc, "invoke", lambda **_: (_ for _ in ()).throw(RuntimeError("throttled")))

    def prov(cust: str) -> Provenance:
        return Provenance(
            document_id=f"doc-{cust}",
            source_id="golden-questionnaire",
            audience="customer",
            customer_name=cust,
            customer_id=cust,
            answered_at=NOW - timedelta(days=10),
        )

    report = DivergenceDetector(BedrockComparator()).detect(
        [
            PriorStatement("s1", "Key rotation?", "Annually.", prov("acme")),
            PriorStatement("s2", "Key rotation?", "Every 90 days.", prov("northwind")),
        ]
    )

    assert report.tier_floor is Tier.AMBER
    assert report.findings[0].kind is DivergenceKind.UNRESOLVED_DIVERGENCE


def test_identical_answers_short_circuit_without_a_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_invoke(monkeypatch, "CONTRADICT")
    cmp = BedrockComparator()

    assert cmp("Key rotation?", "Annually.", "Annually.") is ClaimRelation.AGREE
    assert calls == []


def test_empty_answer_is_uncertain_not_agreement(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_invoke(monkeypatch, "AGREE")
    cmp = BedrockComparator()

    assert cmp("Key rotation?", "Annually.", "   ") is ClaimRelation.UNCERTAIN
    assert calls == []


def test_cache_is_symmetric_so_the_reversed_pair_is_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O(n²) pairwise sweeps re-ask the same symmetric question constantly."""
    calls = _stub_invoke(monkeypatch, "CONTRADICT")
    cmp = BedrockComparator()

    a, b = "Annually.", "Every 90 days."
    assert cmp("Key rotation?", a, b) is ClaimRelation.CONTRADICT
    assert cmp("Key rotation?", b, a) is ClaimRelation.CONTRADICT

    assert len(calls) == 1
    assert cmp.calls == 1


def test_a_different_question_is_not_a_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_invoke(monkeypatch, "AGREE")
    cmp = BedrockComparator()

    cmp("Key rotation?", "Annually.", "Once a year.")
    cmp("Backup frequency?", "Annually.", "Once a year.")

    assert len(calls) == 2


def test_long_answers_are_trimmed_before_they_reach_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_invoke(monkeypatch, "AGREE")
    BedrockComparator()("Key rotation?", "A" * 5000, "B" * 5000)

    assert "A" * 1500 in calls[0]["user_prompt"]
    assert "A" * 1501 not in calls[0]["user_prompt"]


# ---------------------------------------------------------------------------
# 5. The DynamoDB seam between hard_rules and excel_writer
#
# hard_rules writes citations_detailed; excel_writer reads them back. DynamoDB
# rejects float, so the write side converts to Decimal and the read side
# converts back. Nothing else exercises that pair, and a mismatch surfaces only
# on a deployed invocation — which is exactly why it is pinned here.
#
# Both handlers use Lambda-flat imports (`from rules import ...`), so they are
# loaded the way the Lambda runtime loads them rather than as package modules.
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402
import sys  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402

_LAMBDAS = Path(__file__).resolve().parent.parent


def _load_lambda_module(package: str, alias: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import a handler the way the Lambda runtime does, with AWS stubbed out.

    Both handlers build their boto3 client at module scope, so a bare import
    resolves the local credential chain — which makes the result depend on
    whatever AWS config the developer happens to have. (An SSO/`login` profile
    raises MissingDependencyException without botocore[crt]; an empty
    environment quietly succeeds.) Stubbing before exec keeps the test about
    the serialisation helpers and nothing else.
    """
    import boto3

    monkeypatch.setattr(boto3, "resource", lambda *a, **k: MagicMock())
    monkeypatch.setattr(boto3, "client", lambda *a, **k: MagicMock())
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.syspath_prepend(str(_LAMBDAS / package))
    spec = importlib.util.spec_from_file_location(alias, _LAMBDAS / package / "handler.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[alias] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_citations_survive_the_dynamodb_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    hard_rules_handler = _load_lambda_module("hard_rules", "_hr_handler", monkeypatch)
    writer_handler = _load_lambda_module("excel_writer", "_xw_handler", monkeypatch)

    event = {
        "generation": {"citations": ["acme_vsa_2026", "not_retrieved"]},
        "retrieval": {
            "passages": [
                {
                    "document_id": "acme_vsa_2026",
                    "excerpt": "Keys rotate annually." * 40,
                    "score": 0.8123,
                    "provenance": Provenance(
                        document_id="acme_vsa_2026",
                        source_id="golden-questionnaire",
                        audience="customer",
                        customer_name="Acme Financial Corp",
                        answered_at=datetime(2026, 3, 11, tzinfo=UTC),
                        approved_by="R. Sandhu",
                    ).model_dump(mode="json"),
                }
            ]
        },
    }

    written = hard_rules_handler._f2d(hard_rules_handler._detailed_citations(event))

    # A cited id with no retrieved passage is skipped, not half-built.
    assert len(written) == 1
    # DynamoDB rejects float outright — every one must have become a Decimal.
    assert isinstance(written[0]["relevance"], Decimal)
    assert not _contains_float(written)
    # Excerpts are trimmed so the item stays small.
    assert len(written[0]["excerpt"]) == hard_rules_handler._MAX_EXCERPT_CHARS

    citations = writer_handler._citations({"citations_detailed": written}, MagicMock())

    assert len(citations) == 1
    assert citations[0].provenance.customer_name == "Acme Financial Corp"
    assert citations[0].relevance == pytest.approx(0.8123)
    assert citations[0].render().startswith("Prior answer — Acme Financial Corp")


def test_unparseable_stored_citation_is_dropped_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_handler = _load_lambda_module("excel_writer", "_xw_handler2", monkeypatch)
    log = MagicMock()

    citations = writer_handler._citations(
        {"questionId": "q1", "citations_detailed": [{"provenance": {"audience": "bogus"}}]}, log
    )

    assert citations == []
    assert log.warning.called


def _contains_float(obj: Any) -> bool:
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_float(v) for v in obj)
    return False


# ---------------------------------------------------------------------------
# 6. The mock-sources URL builder used to be tested here.
#
# _mock_api_passages() and the MockSourcesApi that served it were removed when
# the stack was de-personalised: the retriever no longer calls any HTTP source,
# so there is no URL left to build or encode. The test went with the code it
# covered rather than being weakened to keep it green. The retriever's
# remaining source fan-out is covered by tests/test_retriever_kb_adapter.py.
# ---------------------------------------------------------------------------


def test_retriever_has_no_http_source_fan_out() -> None:
    """Guard against the mock HTTP source being reintroduced by accident.

    Every source the retriever knows must resolve through the Knowledge Base,
    the DynamoDB customer-refs gate, or the deferred list — never an outbound
    HTTP call from inside the retrieval path.
    """
    assert not hasattr(handler, "_mock_api_passages")
    assert not hasattr(handler, "_MOCK_SOURCE_PATHS")

    passages, degraded = handler._retrieve_for_sources(["seismic", "gong"], "any question")
    assert passages == []
    assert degraded == []
