"""HTTP API backend for the SME review UI.

Routes (HTTP API Gateway proxy format):
  GET  /reviews              — list jobs awaiting review
  GET  /reviews/{jobId}      — job + all reviewer-flagged Q&A
  POST /reviews/{jobId}/approve  — body: {answers:[{questionId,answer_text}]}
  POST /reviews/{jobId}/reject   — body: {reason: str}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from shared.logging_config import configure_logging

logger = configure_logging()
ddb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
}


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
            "outputKey": j.get("output_key", ""),
        }
        for j in resp.get("Items", [])
    ]
    return _resp(200, {"reviews": jobs})


def get_review(job_id: str) -> dict[str, Any]:
    jobs_table = ddb.Table(os.environ["JOBS_TABLE"])
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})

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
        _d2f({
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
        })
        for q in items
        if q.get("reviewer_required") or q.get("tier") in ("amber", "red")
    ]
    reviewable.sort(key=lambda q: (q["tier"] != "red", q["tier"] != "amber"))

    return _resp(200, {
        "jobId": job_id,
        "status": job.get("review_status"),
        "outputKey": job.get("output_key", ""),
        "questions": reviewable,
    })


def _write_library_feedback(
    job_id: str,
    approved_by: str,
    answer_map: dict[str, str],
    now: str,
) -> None:
    """Write LibraryFeedback entries for each SME-reviewed question.

    Called after send_task_success so the execution is always resumed even if
    this write fails. Failures are logged and swallowed — the flywheel is
    best-effort for Phase D; a DDB stream or transactional write handles
    durability in production.
    """
    questions_table = ddb.Table(os.environ["QUESTIONS_TABLE"])
    feedback_table = ddb.Table(os.environ["LIBRARY_FEEDBACK_TABLE"])
    expires_on = (datetime.now(timezone.utc) + timedelta(days=730)).date().isoformat()

    # Paginate through all questions for this job
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
        if not (q.get("reviewer_required") or q.get("tier") in ("amber", "red")):
            continue
        qid = q["questionId"]
        answer_text = answer_map.get(qid) or q.get("answer_text", "")
        if not answer_text:
            continue
        feedback_table.put_item(Item={
            "answerId": qid,
            "version": now,
            "question_text": q.get("text", ""),
            "answer_text": answer_text,
            "topic_ids": q.get("topic_ids", []),
            "approved_at": now,
            "approved_by": approved_by,
            "sme_approved": True,
            "corroborated_by": q.get("primary_passage_uris", []),
            "expires_on": expires_on,
        })
        written += 1
    logger.info("review_api.feedback_written", job_id=job_id, count=written)


def approve(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
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

    now = datetime.now(timezone.utc).isoformat()
    jobs_table.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET review_status = :s, reviewed_at = :t",
        ExpressionAttributeValues={":s": "APPROVED", ":t": now},
    )

    sfn.send_task_success(
        taskToken=job["task_token"],
        output=json.dumps({"reviewed": True, "jobId": job_id, "reviewedAt": now}),
    )
    logger.info("review_api.approved", job_id=job_id, edits=len(edited))

    # Flywheel write — best-effort after resuming execution
    approved_by = body.get("approved_by", "sme")
    try:
        _write_library_feedback(job_id, approved_by, answer_map, now)
    except Exception as exc:
        logger.warning("review_api.feedback_write_error", job_id=job_id, error=str(exc))

    return _resp(200, {"status": "approved"})


def reject(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    jobs_table = ddb.Table(os.environ["JOBS_TABLE"])
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})
    if job.get("review_status") != "PENDING":
        return _resp(409, {"error": f"job already {job.get('review_status')}"})

    reason = body.get("reason", "Rejected by SME")
    jobs_table.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET review_status = :s, reject_reason = :r",
        ExpressionAttributeValues={":s": "REJECTED", ":r": reason},
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

    if method == "GET" and path == "/reviews":
        return list_reviews()
    if method == "GET" and job_id and path.endswith(job_id):
        return get_review(job_id)
    if method == "POST" and job_id and path.endswith("/approve"):
        return approve(job_id, body)
    if method == "POST" and job_id and path.endswith("/reject"):
        return reject(job_id, body)

    return _resp(404, {"error": f"No route: {method} {path}"})
