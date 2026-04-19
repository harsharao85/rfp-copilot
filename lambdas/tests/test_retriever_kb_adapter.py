"""Tests for _knowledge_base_retrieve — the Bedrock KB adapter.

Four assertions per the Phase C plan:
 1. `retrieve` is called with the correct source_type filter for each of the
    three dispatch source names (compliance_store, product_docs, prior_rfps).
 2. updated_at / approved_at in the KB result's metadata are propagated into
    RetrievedPassage.metadata so _apply_freshness_suppression still fires.
 3. Empty retrievalResults returns [] (not raises).
 4. A ClientError returns [] and logs retriever.kb_error.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from retriever import handler
from retriever.handler import _apply_freshness_suppression, _knowledge_base_retrieve


@pytest.fixture(autouse=True)
def _kb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-test-0001")


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the cached bedrock-agent-runtime client with a MagicMock."""
    client = MagicMock()
    # Bypass the lru_cache by monkeypatching the accessor to return our mock.
    monkeypatch.setattr(handler, "_bedrock_agent_runtime", lambda: client)
    return client


def _kb_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"retrievalResults": results}


def _kb_hit(
    *,
    text: str,
    uri: str,
    document_id: str,
    score: float = 0.73,
    updated_at: str | None = None,
    approved_at: str | None = None,
) -> dict[str, Any]:
    md: dict[str, Any] = {"document_id": document_id}
    if updated_at is not None:
        md["updated_at"] = updated_at
    if approved_at is not None:
        md["approved_at"] = approved_at
    return {
        "content": {"text": text},
        "location": {"type": "S3", "s3Location": {"uri": uri}},
        "score": score,
        "metadata": md,
    }


# ---------------------------------------------------------------------------
# Assertion 1 — filter routing per source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source,expected_source_type",
    [
        ("compliance_store", "compliance_cert"),
        ("product_docs",     "product_doc"),
        ("prior_rfps",       "prior_rfp"),
    ],
)
def test_retrieve_sends_correct_source_type_filter(
    mock_client: MagicMock,
    source: str,
    expected_source_type: str,
) -> None:
    mock_client.retrieve.return_value = _kb_response([])

    _knowledge_base_retrieve("encryption at rest", source=source, source_system="compliance")

    mock_client.retrieve.assert_called_once()
    call_kwargs = mock_client.retrieve.call_args.kwargs
    assert call_kwargs["knowledgeBaseId"] == "kb-test-0001"
    assert call_kwargs["retrievalQuery"] == {"text": "encryption at rest"}
    vs_config = call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert vs_config["numberOfResults"] == 5
    assert vs_config["filter"] == {"equals": {"key": "source_type", "value": expected_source_type}}


# ---------------------------------------------------------------------------
# Assertion 2 — updated_at / approved_at pass through into metadata so
#               _apply_freshness_suppression still fires end-to-end.
# ---------------------------------------------------------------------------

def test_updated_at_flows_through_to_passage_metadata(mock_client: MagicMock) -> None:
    mock_client.retrieve.return_value = _kb_response([
        _kb_hit(
            text="SOC 2 audit covers Security, Availability, Confidentiality.",
            uri="s3://bucket/compliance/soc2_cert_2025.pdf",
            document_id="soc2_cert_2025",
            updated_at="2025-10-01",
        ),
    ])

    passages = _knowledge_base_retrieve("SOC 2 scope", source="compliance_store", source_system="compliance")

    assert len(passages) == 1
    assert passages[0].metadata["updated_at"] == "2025-10-01"
    assert passages[0].document_id == "soc2_cert_2025"
    assert passages[0].uri == "s3://bucket/compliance/soc2_cert_2025.pdf"


def test_approved_at_flows_through_and_freshness_suppression_fires(mock_client: MagicMock) -> None:
    """End-to-end: KB returns primary + prior; suppression picks up both dates from metadata."""
    # First call: primary source
    # Second call: prior source
    mock_client.retrieve.side_effect = [
        _kb_response([
            _kb_hit(
                text="Current SOC 2 cert.",
                uri="s3://bucket/compliance/soc2_cert_2025.pdf",
                document_id="soc2_cert_2025",
                updated_at="2025-10-01",
            ),
        ]),
        _kb_response([
            _kb_hit(
                text="Older approved answer.",
                uri="s3://bucket/prior-rfps/acme_financial_soc2_answer.md",
                document_id="acme_financial_soc2_answer",
                approved_at="2025-07-01",
            ),
        ]),
    ]

    primary = _knowledge_base_retrieve("SOC 2", source="compliance_store", source_system="compliance")
    prior   = _knowledge_base_retrieve("SOC 2", source="prior_rfps",       source_system="historical_rfp")

    kept, suppressed = _apply_freshness_suppression(primary, prior)
    assert kept == []
    assert "acme_financial_soc2_answer" in suppressed


# ---------------------------------------------------------------------------
# Assertion 3 — empty results
# ---------------------------------------------------------------------------

def test_empty_retrieval_results_returns_empty_list(mock_client: MagicMock) -> None:
    mock_client.retrieve.return_value = _kb_response([])

    passages = _knowledge_base_retrieve("no matches", source="product_docs", source_system="whitepaper")

    assert passages == []


# ---------------------------------------------------------------------------
# Assertion 4 — ClientError yields [] and logs retriever.kb_error
# ---------------------------------------------------------------------------

def test_client_error_returns_empty_and_logs(
    mock_client: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # structlog writes JSON to stdout, so we capture there rather than
    # via caplog (which hooks only the stdlib logging module).
    mock_client.retrieve.side_effect = ClientError(
        error_response={"Error": {"Code": "ThrottlingException", "Message": "rate limit"}},
        operation_name="Retrieve",
    )

    passages = _knowledge_base_retrieve("whatever", source="compliance_store", source_system="compliance")

    assert passages == []
    captured = capsys.readouterr()
    assert "retriever.kb_error" in captured.out
    assert "ThrottlingException" in captured.out
