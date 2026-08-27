"""HTTP API backend for the SME review UI.

Routes (HTTP API Gateway proxy format):
  GET  /reviews              — list jobs awaiting review
  GET  /reviews/{jobId}      — job + all reviewer-flagged Q&A
  POST /reviews/{jobId}/approve  — body: {answers:[{questionId,answer_text}]}
  POST /reviews/{jobId}/reject   — body: {reason: str}
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from shared import session
from shared.logging_config import configure_logging

logger = configure_logging()
ddb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")
s3 = boto3.client("s3")

# Set by the CDK stack from the `corsOrigins` context value. API Gateway's own
# CORS configuration enforces the full origin list; this header covers the
# non-preflight response. Falls back to "*" only when the stack has not set it
# — never to a particular site.
CORS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def jwt_actor(event: dict[str, Any]) -> str | None:
    """Identity of the human making this call, from the verified JWT claims.

    API Gateway's JWT authorizer has already validated signature, issuer,
    audience and expiry by the time the claims reach here, so these are the only
    trustworthy identity in the request — the request body is not. ``email``
    is what an auditor reads; ``sub`` is the fallback for a pool configured
    without an email claim, and is at least stable and unique.

    Returns ``None`` when there are no claims at all, which is how the direct
    unit-test calls and any future non-JWT invocation path stay working.
    """
    authorizer = event.get("requestContext", {}).get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims") or {}
    for key in ("email", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resp(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(body, default=str),
    }


def _d2f(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _d2f(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_d2f(v) for v in obj]
    return obj


# ---- route handlers --------------------------------------------------------


def list_reviews() -> dict[str, Any]:
    table = ddb.Table(os.environ["JOBS_TABLE"])
    resp = table.scan(FilterExpression=Attr("review_status").eq("PENDING"))
    jobs = [
        {
            "jobId": j["jobId"],
            "reviewRequestedAt": j.get("review_requested_at", ""),
            "answerCount": int(j.get("question_count", 0)),
            # Scale before opening: how many of those answers are green/amber/red.
            "tierCounts": _d2f(j.get("tier_counts", {})),
            "sessionStatus": j.get("session_status", ""),
            "outputKey": j.get("output_key", ""),
        }
        for j in resp.get("Items", [])
    ]
    return _resp(200, {"reviews": jobs})


def _mark_in_review(jobs_table: Any, job_id: str, current: str | None, actor: str) -> None:
    """Move AWAITING_REVIEW → IN_REVIEW on the first GET only.

    The condition on session_status is what makes "first only" true: a reviewer
    who refreshes the page, or two reviewers who open the same job, produce one
    IN_REVIEW row between them, not one each.
    """
    if current != session.AWAITING_REVIEW:
        return
    now = session.now_iso()
    try:
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET session_status = :ss, updated_at = :u",
            ConditionExpression=Attr("session_status").eq(session.AWAITING_REVIEW),
            ExpressionAttributeValues={
                ":ss": session.advance(current, session.REVIEW_OPENED),
                ":u": now,
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return
    session.log_step(job_id, session.IN_REVIEW, actor, {}, reviewed_at=now)


def get_review(job_id: str, actor: str | None = None) -> dict[str, Any]:
    jobs_table = ddb.Table(os.environ["JOBS_TABLE"])
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})

    _mark_in_review(jobs_table, job_id, job.get("session_status"), actor or "sme")

    questions_table = ddb.Table(os.environ["QUESTIONS_TABLE"])
    items: list[Any] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("jobId").eq(job_id)}
    while True:
        r = questions_table.query(**kwargs)
        items.extend(r["Items"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    reviewable = [
        _d2f(
            {
                "questionId": q["questionId"],
                "text": q.get("text", ""),
                "section": q.get("section", ""),
                "answer_text": q.get("answer_text", ""),
                "tier": q.get("tier", "red"),
                "raw_confidence": q.get("raw_confidence", 0),
                "hard_rule_triggers": q.get("hard_rule_triggers", []),
                "reviewer_required": q.get("reviewer_required", True),
                "answer_cell": q.get("answer_cell", {}),
                "citations": q.get("citations", []),
                "confidence_breakdown": q.get("confidence_breakdown", {}),
                "suppressed_prior_count": q.get("suppressed_prior_count", 0),
                "primary_passage_uris": q.get("primary_passage_uris", []),
                "topic_ids": q.get("topic_ids", []),
            }
        )
        for q in items
        if q.get("reviewer_required") or q.get("tier") in ("amber", "red")
    ]
    reviewable.sort(key=lambda q: (q["tier"] != "red", q["tier"] != "amber"))

    return _resp(
        200,
        {
            "jobId": job_id,
            "status": job.get("review_status"),
            "outputKey": job.get("output_key", ""),
            "questions": reviewable,
        },
    )


def _qa_markdown(
    *,
    doc_id: str,
    approved_by: str,
    approved_at: str,
    topic_ids: list[str],
    corroborated_by: list[str],
    question_text: str,
    answer_text: str,
) -> str:
    """Same shape as scripts/generate_corpus_documents.py's _generate_sme_approved."""
    return (
        f"# SME-Approved Q&A — {doc_id}\n\n"
        f"**Approved:** {approved_by} on {approved_at}  \n"
        f"**Topics:** {', '.join(topic_ids)}  \n"
        f"**Corroborated by:** {', '.join(corroborated_by) or '—'}\n\n"
        f"## Question\n\n{question_text.strip()}\n\n"
        f"## Answer\n\n{answer_text.strip()}\n"
    )


def _emit_sme_approved_to_kb(
    job_id: str,
    approved_by: str,
    answer_map: dict[str, str],
    now_iso: str,
) -> None:
    """Write each approved Q&A to S3 as markdown + Bedrock metadata sidecar.

    The PutObject events fire the ingestion_trigger Lambda automatically
    (subscribed to this bucket in orchestration-stack), so no explicit
    StartIngestionJob call is needed here. Approval benefits future RFPs,
    not the in-flight one — a ~1-3 minute index lag is acceptable.

    Every answered question is emitted, green included. An earlier version
    emitted only the reviewer-flagged and amber/red rows, which meant the
    flywheel learned from the answers the machine was *worst* at and threw away
    the majority it was best at. What the human approves is the filled-out
    questionnaire, not the difficult half of it.
    """
    bucket = os.environ["REFERENCE_CORPUS_BUCKET"]
    approved_at_date = now_iso[:10]  # human-facing date in the markdown body
    # Sidecar timestamps are full ISO-8601 with offset. Bedrock KB metadata
    # filters compare these lexicographically, and a fixed-width YYYY-MM-DD
    # prefix means the offset form still sorts against date-only values written
    # by earlier runs and by the corpus generator.
    approved_at_iso = now_iso
    expires_on = (datetime.now(UTC) + timedelta(days=730)).isoformat()

    questions_table = ddb.Table(os.environ["QUESTIONS_TABLE"])
    items: list[Any] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("jobId").eq(job_id)}
    while True:
        r = questions_table.query(**kwargs)
        items.extend(r["Items"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    written = 0
    for q in items:
        qid = q["questionId"]
        answer_text = answer_map.get(qid) or q.get("answer_text", "")
        question_text = q.get("text", "")
        if not answer_text or not question_text:
            continue
        sme_edited = bool(q.get("sme_edited")) or qid in answer_map
        # Deterministic doc_id from question text so re-approvals overwrite
        # prior version rather than accumulate duplicates in the KB.
        doc_id = "lfb-" + hashlib.sha256(question_text.encode("utf-8")).hexdigest()[:16]
        topic_ids = list(q.get("topic_ids", []))
        corroborated_by = list(q.get("primary_passage_uris", []))

        md_body = _qa_markdown(
            doc_id=doc_id,
            approved_by=approved_by,
            approved_at=approved_at_date,
            topic_ids=topic_ids,
            corroborated_by=corroborated_by,
            question_text=question_text,
            answer_text=answer_text,
        )
        # Kept flat and small — S3 Vectors caps filterable metadata at 2 KB per
        # vector. sme_edited and source_job_id are what let a later reviewer
        # tell a human-corrected answer from an accepted machine one.
        sidecar = {
            "metadataAttributes": {
                "document_id": doc_id,
                "source_type": "sme_approved_answer",
                "topic_ids": topic_ids,
                "approved_at": approved_at_iso,
                "approved_by": approved_by,
                "expires_on": expires_on,
                "sme_edited": sme_edited,
                "source_job_id": job_id,
                "question_text": question_text[:500],  # cap for sidecar size safety
            }
        }
        key = f"sme-approved/{doc_id}.md"
        s3.put_object(
            Bucket=bucket, Key=key, Body=md_body.encode("utf-8"), ContentType="text/markdown"
        )
        s3.put_object(
            Bucket=bucket,
            Key=f"{key}.metadata.json",
            Body=json.dumps(sidecar).encode("utf-8"),
            ContentType="application/json",
        )
        written += 1

    logger.info("review_api.sme_approved_written", job_id=job_id, count=written)


def approve(job_id: str, body: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
    jobs_table = ddb.Table(os.environ["JOBS_TABLE"])
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})
    if job.get("review_status") != "PENDING":
        return _resp(409, {"error": f"job already {job.get('review_status')}"})

    # Persist any edited answers
    edited = body.get("answers", [])
    answer_map: dict[str, str] = {}
    if edited:
        questions_table = ddb.Table(os.environ["QUESTIONS_TABLE"])
        for a in edited:
            answer_map[a["questionId"]] = a["answer_text"]
            questions_table.update_item(
                Key={"jobId": job_id, "questionId": a["questionId"]},
                UpdateExpression="SET answer_text = :t, sme_edited = :e",
                ExpressionAttributeValues={":t": a["answer_text"], ":e": True},
            )

    now = datetime.now(UTC).isoformat()
    # The verified JWT identity wins over anything the caller put in the body:
    # this actor is the audit trail for a write that reaches the SME-approved
    # corpus, and a self-declared one is not an audit trail.
    approved_by = actor or body.get("approved_by", "sme")
    update = "SET review_status = :s, reviewed_at = :t, updated_at = :t, sme_edit_count = :n"
    values: dict[str, Any] = {":s": "APPROVED", ":t": now, ":n": len(answer_map)}
    try:
        next_status: str | None = session.advance(
            str(job.get("session_status") or session.AWAITING_REVIEW), session.APPROVE
        )
    except session.InvalidTransitionError as exc:
        logger.warning("review_api.illegal_transition", job_id=job_id, error=str(exc))
        next_status = None
    if next_status:
        update += ", session_status = :ss"
        values[":ss"] = next_status
    jobs_table.update_item(
        Key={"jobId": job_id}, UpdateExpression=update, ExpressionAttributeValues=values
    )
    if next_status:
        session.log_step(
            job_id,
            next_status,
            approved_by,
            {"edited": sorted(answer_map), "sme_edit_count": len(answer_map)},
            reviewed_at=now,
        )

    sfn.send_task_success(
        taskToken=job["task_token"],
        output=json.dumps({"reviewed": True, "jobId": job_id, "reviewedAt": now}),
    )
    logger.info("review_api.approved", job_id=job_id, edits=len(edited))

    # Flywheel write — best-effort after resuming execution.
    # Writes Q&A to S3 + triggers KB ingestion. Approved answers become
    # semantically retrievable in ~1-3 min (next ingestion run).
    try:
        _emit_sme_approved_to_kb(job_id, approved_by, answer_map, now)
    except Exception as exc:
        logger.warning("review_api.sme_approved_write_error", job_id=job_id, error=str(exc))

    return _resp(200, {"status": "approved"})


def reject(job_id: str, body: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
    jobs_table = ddb.Table(os.environ["JOBS_TABLE"])
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})
    if job.get("review_status") != "PENDING":
        return _resp(409, {"error": f"job already {job.get('review_status')}"})

    reason = body.get("reason", "Rejected by SME")
    now = datetime.now(UTC).isoformat()
    update = "SET review_status = :s, reject_reason = :r, updated_at = :t"
    values: dict[str, Any] = {":s": "REJECTED", ":r": reason, ":t": now}
    try:
        next_status: str | None = session.advance(
            str(job.get("session_status") or session.AWAITING_REVIEW), session.REJECT
        )
    except session.InvalidTransitionError as exc:
        logger.warning("review_api.illegal_transition", job_id=job_id, error=str(exc))
        next_status = None
    if next_status:
        update += ", session_status = :ss"
        values[":ss"] = next_status
    jobs_table.update_item(
        Key={"jobId": job_id}, UpdateExpression=update, ExpressionAttributeValues=values
    )
    if next_status:
        session.log_step(
            job_id,
            next_status,
            actor or body.get("rejected_by", "sme"),
            {"reason": reason},
            reviewed_at=now,
        )
    sfn.send_task_failure(
        taskToken=job["task_token"],
        error="SMEReject",
        cause=reason[:256],
    )
    logger.info("review_api.rejected", job_id=job_id, reason=reason)
    return _resp(200, {"status": "rejected"})


# ---- dispatcher ------------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}
    job_id = path_params.get("jobId")
    body: dict[str, Any] = json.loads(event.get("body") or "{}")
    actor = jwt_actor(event)

    if method == "GET" and path == "/reviews":
        return list_reviews()
    if method == "GET" and job_id and path.endswith(job_id):
        return get_review(job_id, actor)
    if method == "POST" and job_id and path.endswith("/approve"):
        return approve(job_id, body, actor)
    if method == "POST" and job_id and path.endswith("/reject"):
        return reject(job_id, body, actor)

    return _resp(404, {"error": f"No route: {method} {path}"})
