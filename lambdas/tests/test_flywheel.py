"""UOW-E — step 7: the filled-out questionnaire feeds the next one.

Two defects and one crash:

  1. The loop that emits approved answers into the KB skipped every green one,
     so the flywheel learned only from the answers the machine was worst at.
  2. The sidecar could not distinguish a human-corrected answer from an
     accepted machine one.
  3. `Provenance.is_expired` raised TypeError on any sidecar carrying a bare
     date, which is what review_api itself was writing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from shared.provenance import Provenance, render_citation

JOBS = "test-jobs"
QUESTIONS = "test-questions"
REVIEWS = "test-reviews"
CORPUS = "test-corpus"


def _question(question_id: str, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "jobId": "job-1",
        "questionId": question_id,
        "text": "How often are customer-managed encryption keys rotated?",
        "answer_text": "Every 90 days, evidenced in the SOC 2 Type II report (CC6.1).",
        "tier": "green",
        "reviewer_required": False,
        "topic_ids": ["key_management"],
        "primary_passage_uris": ["s3://corpus/compliance/soc2.md"],
    }
    item.update(overrides)
    return item


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch) -> Any:
    from review_api import handler as review_handler

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        ddb.create_table(
            TableName=JOBS,
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        for name, sort_key in ((QUESTIONS, "questionId"), (REVIEWS, "reviewedAt")):
            ddb.create_table(
                TableName=name,
                KeySchema=[
                    {"AttributeName": "jobId", "KeyType": "HASH"},
                    {"AttributeName": sort_key, "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "jobId", "AttributeType": "S"},
                    {"AttributeName": sort_key, "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
        s3.create_bucket(Bucket=CORPUS)
        for key, value in {
            "JOBS_TABLE": JOBS,
            "QUESTIONS_TABLE": QUESTIONS,
            "REVIEWS_TABLE": REVIEWS,
            "REFERENCE_CORPUS_BUCKET": CORPUS,
        }.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(review_handler, "ddb", ddb)
        monkeypatch.setattr(review_handler, "s3", s3)
        monkeypatch.setattr(review_handler, "sfn", MagicMock())
        yield SimpleNamespace(handler=review_handler, ddb=ddb, s3=s3)


def _sidecars(review: Any) -> dict[str, dict[str, Any]]:
    listing = review.s3.list_objects_v2(Bucket=CORPUS, Prefix="sme-approved/")
    out: dict[str, dict[str, Any]] = {}
    for obj in listing.get("Contents", []):
        if not obj["Key"].endswith(".metadata.json"):
            continue
        body = review.s3.get_object(Bucket=CORPUS, Key=obj["Key"])["Body"].read()
        out[obj["Key"]] = json.loads(body)["metadataAttributes"]
    return out


NOW = datetime.now(UTC).isoformat()


def test_a_green_answer_reaches_the_knowledge_base(review: Any) -> None:
    """Regression on the dropped filter: greens are the majority and the
    highest-confidence answers, and they used to be silently discarded."""
    review.ddb.Table(QUESTIONS).put_item(Item=_question("q-0001"))

    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {}, NOW)

    assert len(_sidecars(review)) == 1


def test_the_whole_questionnaire_is_emitted_not_just_the_hard_half(review: Any) -> None:
    table = review.ddb.Table(QUESTIONS)
    table.put_item(Item=_question("q-0001", tier="green", reviewer_required=False))
    table.put_item(Item=_question("q-0002", tier="amber", reviewer_required=True))
    table.put_item(Item=_question("q-0003", tier="red", reviewer_required=True))
    # An answer with no text is not an answer; it is still skipped.
    table.put_item(Item=_question("q-0004", tier="green", answer_text=""))

    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {}, NOW)

    # q-0001..q-0003 share question text, so they collapse onto one deterministic
    # doc id by design (re-approval overwrites rather than duplicating).
    table.put_item(Item=_question("q-0005", text="Do you encrypt data at rest?"))
    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {}, NOW)
    assert len(_sidecars(review)) == 2


def test_sidecar_marks_a_human_corrected_answer(review: Any) -> None:
    table = review.ddb.Table(QUESTIONS)
    table.put_item(Item=_question("q-0001", sme_edited=True))
    table.put_item(Item=_question("q-0002", text="Do you encrypt data at rest?"))

    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {}, NOW)

    by_edited = {bool(a["sme_edited"]): a for a in _sidecars(review).values()}
    assert set(by_edited) == {True, False}
    assert all(a["source_job_id"] == "job-1" for a in by_edited.values())


def test_an_edit_made_in_this_approval_counts_as_edited(review: Any) -> None:
    review.ddb.Table(QUESTIONS).put_item(Item=_question("q-0001"))

    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {"q-0001": "corrected text"}, NOW)

    (attrs,) = _sidecars(review).values()
    assert attrs["sme_edited"] is True


def test_sidecar_timestamps_are_offset_aware_and_still_sort(review: Any) -> None:
    review.ddb.Table(QUESTIONS).put_item(Item=_question("q-0001"))

    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {}, NOW)

    (attrs,) = _sidecars(review).values()
    approved_at = datetime.fromisoformat(attrs["approved_at"])
    expires_on = datetime.fromisoformat(attrs["expires_on"])
    assert approved_at.tzinfo is not None
    assert expires_on.tzinfo is not None
    # KB metadata filters compare these as strings, so ordering must survive
    # the change of format against date-only values written by earlier runs.
    assert attrs["approved_at"] > "2020-01-01"
    assert attrs["approved_at"] < attrs["expires_on"]
    assert attrs["expires_on"] > (datetime.now(UTC) + timedelta(days=700)).date().isoformat()


def test_sidecar_stays_inside_the_s3_vectors_budget(review: Any) -> None:
    """S3 Vectors caps filterable metadata at 2 KB per vector."""
    review.ddb.Table(QUESTIONS).put_item(
        Item=_question("q-0001", text="Describe your key management. " * 60)
    )

    review.handler._emit_sme_approved_to_kb("job-1", "R. Sandhu", {}, NOW)

    (attrs,) = _sidecars(review).values()
    assert len(json.dumps({"metadataAttributes": attrs}).encode()) < 2048


# ---------------------------------------------------------------------------
# The timezone crash on the path this unit touches
# ---------------------------------------------------------------------------

_DATE_ONLY_SIDECAR = {
    "document_id": "lfb-0001",
    "source_id": "sme_approved_answer",
    "audience": "internal",
    "approved_by": "R. Sandhu",
    "approved_at": "2025-06-30",
    "expires_on": "2025-06-30",
}


def test_date_only_sidecar_parses_to_an_aware_datetime() -> None:
    p = Provenance.from_sidecar(_DATE_ONLY_SIDECAR)

    assert p.expires_on is not None
    assert p.expires_on.tzinfo is not None
    assert p.approved_at is not None
    assert p.approved_at.tzinfo is not None


def test_is_expired_returns_a_bool_rather_than_raising() -> None:
    p = Provenance.from_sidecar(_DATE_ONLY_SIDECAR)

    expired = p.is_expired

    assert isinstance(expired, bool)
    assert expired is True  # 2025-06-30 is in the past


def test_age_days_survives_a_date_only_anchor() -> None:
    p = Provenance.from_sidecar(_DATE_ONLY_SIDECAR)

    assert isinstance(p.age_days, int)


def test_render_citation_does_not_crash_on_a_date_only_sidecar() -> None:
    """This is the live crash: a date-only sidecar reaching the workbook writer."""
    line = render_citation(Provenance.from_sidecar(_DATE_ONLY_SIDECAR))

    assert "EXPIRED" in line


def test_a_naive_datetime_passed_directly_is_also_made_aware() -> None:
    p = Provenance(
        document_id="d",
        source_id="s",
        expires_on=datetime(2025, 6, 30),  # noqa: DTZ001
    )

    assert p.expires_on is not None
    assert p.expires_on.tzinfo is UTC
    assert p.is_expired is True


def test_round_trip_through_the_sidecar_keeps_the_timestamp() -> None:
    p = Provenance.from_sidecar(_DATE_ONLY_SIDECAR)

    attrs = p.to_sidecar()

    assert attrs["approved_at"] == "2025-06-30T00:00:00+00:00"
    assert attrs["expired"] is True
    # Still lexicographically comparable against the date-only form it came from.
    assert str(attrs["approved_at"]) > "2025-06-29"
    assert str(attrs["approved_at"]) < "2025-07-01"
