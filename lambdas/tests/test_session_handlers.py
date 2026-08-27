"""Handler-level lifecycle tests against a stubbed DynamoDB.

Every AWS endpoint is firewalled here, so these run entirely against moto's
in-process DynamoDB and S3. What they assert is the thing the ruling actually
cares about: that a job's position in the seven-step workflow is written down
once, in one place, by whoever moved it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from shared import session

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "incoming" / "sample_rfp_acmesec.xlsx"

JOBS = "test-jobs"
QUESTIONS = "test-questions"
REVIEWS = "test-reviews"
INCOMING = "test-incoming"
OUTPUT = "test-output"
CORPUS = "test-corpus"


class Aws:
    """The stubbed account: three tables, three buckets."""

    def __init__(self) -> None:
        self.ddb = boto3.resource("dynamodb", region_name="us-east-1")
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.ddb.create_table(
            TableName=JOBS,
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        for name in (QUESTIONS, REVIEWS):
            sort_key = "questionId" if name == QUESTIONS else "reviewedAt"
            self.ddb.create_table(
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
        for bucket in (INCOMING, OUTPUT, CORPUS):
            self.s3.create_bucket(Bucket=bucket)

    def jobs(self) -> Any:
        return self.ddb.Table(JOBS)

    def job(self, job_id: str) -> dict[str, Any]:
        return dict(self.jobs().get_item(Key={"jobId": job_id}).get("Item") or {})

    def reviews(self, job_id: str) -> list[dict[str, Any]]:
        resp = self.ddb.Table(REVIEWS).query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("jobId").eq(job_id)
        )
        return [dict(i) for i in resp["Items"]]

    def steps(self, job_id: str) -> list[str]:
        return [str(r["step"]) for r in self.reviews(job_id)]


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Any:
    with mock_aws():
        env = Aws()
        for key, value in {
            "JOBS_TABLE": JOBS,
            "QUESTIONS_TABLE": QUESTIONS,
            "REVIEWS_TABLE": REVIEWS,
            "INCOMING_BUCKET": INCOMING,
            "OUTPUT_BUCKET": OUTPUT,
            "REFERENCE_CORPUS_BUCKET": CORPUS,
            "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:000000000000:stateMachine:test",
        }.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(session, "_ddb_resource", env.ddb)
        yield env


# ---------------------------------------------------------------------------
# upload_api — CREATED, UPLOADED, PROCESSING
# ---------------------------------------------------------------------------


@pytest.fixture
def upload(aws: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from upload_api import handler as upload_handler

    monkeypatch.setattr(upload_handler, "_ddb_resource", aws.ddb)
    monkeypatch.setattr(upload_handler, "_s3_client", aws.s3)
    monkeypatch.setattr(upload_handler, "_sfn_client", MagicMock())
    return upload_handler


def test_presign_writes_created(upload: Any, aws: Any) -> None:
    body = json.loads(upload.presign({"filename": "caiq.xlsx"})["body"])
    job = aws.job(body["jobId"])

    assert job["session_status"] == session.CREATED
    assert job["status"] == "UPLOADING"  # unchanged, the upload UI still polls it
    assert job["updated_at"]
    assert job["sme_edit_count"] == 0


def test_presign_logs_exactly_one_created_row(upload: Any, aws: Any) -> None:
    job_id = json.loads(upload.presign({"filename": "caiq.xlsx"})["body"])["jobId"]

    rows = aws.reviews(job_id)
    assert [r["step"] for r in rows] == [session.CREATED]
    assert rows[0]["actor"] == "system"
    assert rows[0]["jobId"] == job_id


def test_start_records_uploaded_then_processing(upload: Any, aws: Any) -> None:
    job_id = json.loads(upload.presign({"filename": "caiq.xlsx"})["body"])["jobId"]

    upload.start(job_id)

    job = aws.job(job_id)
    assert job["session_status"] == session.PROCESSING
    assert job["status"] == "PROCESSING"
    # UPLOADED gets its own audit row here; the PROCESSING row is written by
    # excel_parser, which is the first place question_count is known.
    assert aws.steps(job_id) == [session.CREATED, session.UPLOADED]


def test_start_twice_never_writes_an_illegal_status(upload: Any, aws: Any) -> None:
    job_id = json.loads(upload.presign({"filename": "caiq.xlsx"})["body"])["jobId"]
    upload.start(job_id)
    upload.start(job_id)

    assert aws.job(job_id)["session_status"] == session.PROCESSING
    assert aws.steps(job_id).count(session.UPLOADED) == 1


def test_status_holds_back_succeeded_until_the_final_workbook_exists(upload: Any, aws: Any) -> None:
    """UOW-D: the review page turns SUCCEEDED into a download link. Reporting it
    at approval time hands the human the pre-edit workbook."""
    job_id = json.loads(upload.presign({"filename": "caiq.xlsx"})["body"])["jobId"]
    aws.jobs().update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET review_status = :r, session_status = :s",
        ExpressionAttributeValues={":r": "APPROVED", ":s": session.APPROVED},
    )
    assert json.loads(upload.status(job_id)["body"])["status"] == "FINALIZING"

    aws.jobs().update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET session_status = :s",
        ExpressionAttributeValues={":s": session.COMPLETE},
    )
    assert json.loads(upload.status(job_id)["body"])["status"] == "SUCCEEDED"


def test_status_falls_back_for_a_pre_lifecycle_job(upload: Any, aws: Any) -> None:
    """A job written before session_status existed still reports SUCCEEDED."""
    aws.jobs().put_item(
        Item={"jobId": "job-legacy", "status": "PROCESSING", "review_status": "APPROVED"}
    )
    assert json.loads(upload.status("job-legacy")["body"])["status"] == "SUCCEEDED"


# ---------------------------------------------------------------------------
# excel_parser — question_count + the PROCESSING row
# ---------------------------------------------------------------------------


def test_parser_writes_question_count_and_processing_row(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from excel_parser import handler as parser_handler

    monkeypatch.setattr(parser_handler, "ddb", aws.ddb)
    monkeypatch.setattr(parser_handler, "s3", aws.s3)
    aws.s3.upload_file(str(FIXTURE), INCOMING, "incoming/job-1/caiq.xlsx")
    aws.jobs().put_item(
        Item={"jobId": "job-1", "status": "PROCESSING", "session_status": session.PROCESSING}
    )

    result = parser_handler.lambda_handler(
        {"bucket": INCOMING, "key": "incoming/job-1/caiq.xlsx", "jobId": "job-1"}, None
    )

    job = aws.job("job-1")
    # The review queue renders question_count as the answer count; nothing wrote
    # it before this, so every job in the queue showed 0.
    assert int(job["question_count"]) == result["questionCount"] == 30
    assert job["updated_at"]

    rows = aws.reviews("job-1")
    assert [r["step"] for r in rows] == [session.PROCESSING]
    assert int(rows[0]["detail"]["question_count"]) == 30


# ---------------------------------------------------------------------------
# review_gate — AWAITING_REVIEW, and the Catch path
# ---------------------------------------------------------------------------


@pytest.fixture
def gate(aws: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from review_gate import handler as gate_handler

    monkeypatch.setattr(gate_handler, "ddb", aws.ddb)
    return gate_handler


def _parked_job(aws: Any, job_id: str = "job-1") -> None:
    aws.jobs().put_item(
        Item={
            "jobId": job_id,
            "status": "PROCESSING",
            "session_status": session.PROCESSING,
            "tier_counts": {"green": 21, "amber": 6, "red": 3},
        }
    )


def test_review_gate_parks_the_job_and_logs_tier_counts(gate: Any, aws: Any) -> None:
    _parked_job(aws)

    gate.lambda_handler(
        {
            "jobId": "job-1",
            "taskToken": "tok-123",
            "outputKey": "answered/job-1/caiq_answered.xlsx",
            "outputBucket": OUTPUT,
        },
        None,
    )

    job = aws.job("job-1")
    assert job["session_status"] == session.AWAITING_REVIEW
    assert job["review_status"] == "PENDING"  # unchanged for the existing UI
    assert job["task_token"] == "tok-123"

    rows = aws.reviews("job-1")
    assert [r["step"] for r in rows] == [session.AWAITING_REVIEW]
    assert int(rows[0]["detail"]["tier_counts"]["amber"]) == 6


def test_catch_path_marks_failed(gate: Any, aws: Any) -> None:
    _parked_job(aws)

    gate.lambda_handler({"jobId": "job-1", "error": "States.TaskFailed", "cause": "boom"}, None)

    job = aws.job("job-1")
    assert job["session_status"] == session.FAILED
    assert job["failure_reason"] == "boom"
    assert aws.steps("job-1") == [session.FAILED]


def test_catch_path_does_not_relabel_a_rejected_job(gate: Any, aws: Any) -> None:
    """SendTaskFailure is how a reject resumes the machine, so the Catch fires
    on the happy path of a rejection. REJECTED has to survive it."""
    aws.jobs().put_item(
        Item={"jobId": "job-1", "session_status": session.REJECTED, "review_status": "REJECTED"}
    )

    gate.lambda_handler({"jobId": "job-1", "error": "SMEReject", "cause": "out of scope"}, None)

    assert aws.job("job-1")["session_status"] == session.REJECTED
    assert aws.reviews("job-1") == []


# ---------------------------------------------------------------------------
# review_api — IN_REVIEW, APPROVED, REJECTED
# ---------------------------------------------------------------------------


@pytest.fixture
def review(aws: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from review_api import handler as review_handler

    monkeypatch.setattr(review_handler, "ddb", aws.ddb)
    monkeypatch.setattr(review_handler, "s3", aws.s3)
    monkeypatch.setattr(review_handler, "sfn", MagicMock())
    return review_handler


def _awaiting_job(aws: Any, job_id: str = "job-1") -> None:
    aws.jobs().put_item(
        Item={
            "jobId": job_id,
            "session_status": session.AWAITING_REVIEW,
            "review_status": "PENDING",
            "task_token": "tok-123",
            "question_count": 30,
            "tier_counts": {"green": 21, "amber": 6, "red": 3},
        }
    )


def test_first_get_moves_to_in_review_and_a_refresh_does_not(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    review.get_review("job-1")
    review.get_review("job-1")
    review.get_review("job-1")

    assert aws.job("job-1")["session_status"] == session.IN_REVIEW
    assert aws.steps("job-1") == [session.IN_REVIEW]


def test_list_reviews_reports_the_real_answer_count(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    listed = json.loads(review.list_reviews()["body"])["reviews"]

    assert listed[0]["answerCount"] == 30
    assert listed[0]["tierCounts"]["red"] == 3


def test_approve_logs_the_edits(review: Any, aws: Any) -> None:
    _awaiting_job(aws)
    aws.ddb.Table(QUESTIONS).put_item(
        Item={"jobId": "job-1", "questionId": "q-0001", "text": "Key rotation?", "answer_text": "a"}
    )

    review.approve(
        "job-1",
        {
            "answers": [{"questionId": "q-0001", "answer_text": "edited"}],
            "approved_by": "R. Sandhu",
        },
    )

    job = aws.job("job-1")
    assert job["session_status"] == session.APPROVED
    assert job["review_status"] == "APPROVED"
    assert int(job["sme_edit_count"]) == 1

    rows = aws.reviews("job-1")
    assert [r["step"] for r in rows] == [session.APPROVED]
    assert rows[0]["actor"] == "R. Sandhu"
    assert rows[0]["detail"]["edited"] == ["q-0001"]
    assert int(rows[0]["detail"]["sme_edit_count"]) == 1


def test_reject_logs_the_reason(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    review.reject("job-1", {"reason": "pricing question, needs commercial"})

    job = aws.job("job-1")
    assert job["session_status"] == session.REJECTED
    assert job["review_status"] == "REJECTED"

    rows = aws.reviews("job-1")
    assert [r["step"] for r in rows] == [session.REJECTED]
    assert rows[0]["detail"]["reason"] == "pricing question, needs commercial"


# ---------------------------------------------------------------------------
# review_api — the actor on a human action is the verified JWT identity
# ---------------------------------------------------------------------------


def _jwt_event(
    job_id: str,
    route: str,
    claims: dict[str, Any] | None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An HTTP API v2 proxy event as the JWT authorizer delivers it."""
    request_context: dict[str, Any] = {"http": {"method": "POST"}}
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims, "scopes": None}}
    return {
        "rawPath": f"/reviews/{job_id}/{route}",
        "requestContext": request_context,
        "pathParameters": {"jobId": job_id},
        "body": json.dumps(body or {}),
    }


def test_approve_records_the_email_claim_as_actor(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    review.lambda_handler(
        _jwt_event(
            "job-1",
            "approve",
            {"email": "r.sandhu@example.com", "sub": "3f9c-uuid"},
            {"answers": []},
        ),
        None,
    )

    rows = aws.reviews("job-1")
    assert [r["step"] for r in rows] == [session.APPROVED]
    assert rows[0]["actor"] == "r.sandhu@example.com"


def test_approve_falls_back_to_sub_when_there_is_no_email_claim(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    review.lambda_handler(
        _jwt_event("job-1", "approve", {"sub": "3f9c-uuid"}, {"answers": []}), None
    )

    assert aws.reviews("job-1")[0]["actor"] == "3f9c-uuid"


def test_a_body_supplied_actor_cannot_override_the_jwt(review: Any, aws: Any) -> None:
    """The Reviews row is the audit trail for a write that seeds the corpus.
    An actor the caller declares about itself is not an audit trail."""
    _awaiting_job(aws)

    review.lambda_handler(
        _jwt_event(
            "job-1",
            "approve",
            {"email": "r.sandhu@example.com"},
            {"answers": [], "approved_by": "somebody.else@example.com"},
        ),
        None,
    )

    assert aws.reviews("job-1")[0]["actor"] == "r.sandhu@example.com"


def test_the_jwt_identity_is_stamped_on_the_sme_approved_corpus_document(
    review: Any, aws: Any
) -> None:
    """UOW-E feeds approved answers back into the knowledge base, so the
    identity that approved one has to survive into its metadata sidecar."""
    _awaiting_job(aws)
    aws.ddb.Table(QUESTIONS).put_item(
        Item={"jobId": "job-1", "questionId": "q-0001", "text": "Key rotation?", "answer_text": "a"}
    )

    review.lambda_handler(
        _jwt_event("job-1", "approve", {"email": "r.sandhu@example.com"}, {"answers": []}), None
    )

    keys = [
        o["Key"]
        for o in aws.s3.list_objects_v2(Bucket=CORPUS, Prefix="sme-approved/").get("Contents", [])
        if o["Key"].endswith(".metadata.json")
    ]
    assert keys
    sidecar = json.loads(aws.s3.get_object(Bucket=CORPUS, Key=keys[0])["Body"].read())
    assert sidecar["metadataAttributes"]["approved_by"] == "r.sandhu@example.com"


def test_reject_records_the_jwt_identity(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    review.lambda_handler(
        _jwt_event("job-1", "reject", {"email": "r.sandhu@example.com"}, {"reason": "pricing"}),
        None,
    )

    rows = aws.reviews("job-1")
    assert [r["step"] for r in rows] == [session.REJECTED]
    assert rows[0]["actor"] == "r.sandhu@example.com"


def test_jwt_actor_ignores_blank_and_missing_claims(review: Any) -> None:
    """No authorizer, an empty claims map, or a whitespace-only claim all mean
    'no verified identity' — the caller then falls back, rather than writing
    an actor of ''."""
    assert review.jwt_actor({}) is None
    assert review.jwt_actor({"requestContext": {"authorizer": {"jwt": {"claims": {}}}}}) is None
    assert (
        review.jwt_actor({"requestContext": {"authorizer": {"jwt": {"claims": {"email": "  "}}}}})
        is None
    )
    assert (
        review.jwt_actor(
            {"requestContext": {"authorizer": {"jwt": {"claims": {"email": " a@b.c "}}}}}
        )
        == "a@b.c"
    )


def test_one_row_per_transition_across_the_whole_workflow(
    upload: Any, review: Any, gate: Any, aws: Any
) -> None:
    """The audit trail an auditor would actually read, end to end."""
    job_id = json.loads(upload.presign({"filename": "caiq.xlsx"})["body"])["jobId"]
    upload.start(job_id)
    session.log_step(job_id, session.PROCESSING, "system", {"question_count": 30})
    gate.lambda_handler(
        {"jobId": job_id, "taskToken": "tok", "outputKey": "k", "outputBucket": OUTPUT}, None
    )
    review.get_review(job_id)
    review.approve(job_id, {"answers": []})

    assert aws.steps(job_id) == [
        session.CREATED,
        session.UPLOADED,
        session.PROCESSING,
        session.AWAITING_REVIEW,
        session.IN_REVIEW,
        session.APPROVED,
    ]


# ---------------------------------------------------------------------------
# log_step is fail-soft
# ---------------------------------------------------------------------------


def test_log_step_returns_false_when_the_table_is_missing(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REVIEWS_TABLE", "no-such-table")
    assert session.log_step("job-1", session.CREATED, "system", {}) is False


def test_log_step_returns_false_when_the_env_var_is_unset(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REVIEWS_TABLE")
    assert session.log_step("job-1", session.CREATED, "system", {}) is False


def test_a_broken_audit_table_does_not_break_the_pipeline(
    upload: Any, aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing an audit row is bad. Losing the customer's questionnaire is worse."""
    monkeypatch.setenv("REVIEWS_TABLE", "no-such-table")

    resp = upload.presign({"filename": "caiq.xlsx"})

    assert resp["statusCode"] == 200
    assert aws.job(json.loads(resp["body"])["jobId"])["session_status"] == session.CREATED


# ---------------------------------------------------------------------------
# approve — an unknown questionId is rejected, not upserted
#
# DynamoDB UpdateItem upserts. A payload naming a questionId this job does not
# have used to create a Questions row holding only answer_text and sme_edited:
# no tier, no raw_confidence, no cell references. The final workbook write then
# read it back, raised, and the state machine's Catch marked a job the SME had
# already approved as FAILED — permanently undownloadable.
# ---------------------------------------------------------------------------


def _one_real_question(aws: Any, question_id: str = "q-0001") -> None:
    aws.ddb.Table(QUESTIONS).put_item(
        Item={
            "jobId": "job-1",
            "questionId": question_id,
            "text": "Key rotation?",
            "answer_text": "machine answer",
        }
    )


def test_approve_rejects_an_unknown_question_id(review: Any, aws: Any) -> None:
    _awaiting_job(aws)
    _one_real_question(aws)

    resp = review.approve(
        "job-1", {"answers": [{"questionId": "CAIQ-038", "answer_text": "poison"}]}
    )

    assert resp["statusCode"] == 400
    assert "CAIQ-038" in resp["body"]


def test_approve_with_an_unknown_question_id_writes_nothing(review: Any, aws: Any) -> None:
    """Not the phantom row, not the edits alongside it, not the job transition.

    A refused approval must leave the job exactly as it found it, so the SME
    can fix the payload and approve again.
    """
    _awaiting_job(aws)
    _one_real_question(aws)

    review.approve(
        "job-1",
        {
            "answers": [
                {"questionId": "q-0001", "answer_text": "edited"},
                {"questionId": "CAIQ-038", "answer_text": "poison"},
            ]
        },
    )

    rows = aws.ddb.Table(QUESTIONS).query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("jobId").eq("job-1")
    )["Items"]
    assert [r["questionId"] for r in rows] == ["q-0001"]
    assert rows[0]["answer_text"] == "machine answer"
    assert "sme_edited" not in rows[0]

    job = aws.job("job-1")
    assert job["review_status"] == "PENDING"
    assert job["session_status"] == session.AWAITING_REVIEW
    assert aws.steps("job-1") == []


def test_a_refused_approval_does_not_resume_the_state_machine(review: Any, aws: Any) -> None:
    """SendTaskSuccess is one-shot. Firing it on a payload we then reject would
    finalize the job around answers that were never written."""
    _awaiting_job(aws)
    _one_real_question(aws)

    review.approve("job-1", {"answers": [{"questionId": "nope", "answer_text": "x"}]})

    review.sfn.send_task_success.assert_not_called()


def test_approve_rejects_an_answer_with_no_question_id(review: Any, aws: Any) -> None:
    _awaiting_job(aws)

    resp = review.approve("job-1", {"answers": [{"answer_text": "orphan"}]})

    assert resp["statusCode"] == 400
    assert aws.job("job-1")["review_status"] == "PENDING"


def test_approve_rejects_an_answer_with_no_text(review: Any, aws: Any) -> None:
    _awaiting_job(aws)
    _one_real_question(aws)

    resp = review.approve("job-1", {"answers": [{"questionId": "q-0001"}]})

    assert resp["statusCode"] == 400
    assert aws.job("job-1")["review_status"] == "PENDING"


def test_a_known_question_id_still_approves(review: Any, aws: Any) -> None:
    """The guard must not cost the happy path."""
    _awaiting_job(aws)
    _one_real_question(aws)

    resp = review.approve("job-1", {"answers": [{"questionId": "q-0001", "answer_text": "edited"}]})

    assert resp["statusCode"] == 200
    row = aws.ddb.Table(QUESTIONS).get_item(Key={"jobId": "job-1", "questionId": "q-0001"})["Item"]
    assert row["answer_text"] == "edited"
    assert row["sme_edited"] is True
