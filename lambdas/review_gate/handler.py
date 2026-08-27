"""Review gate Lambda — called by Step Functions waitForTaskToken.

Stores the task token on the job record so the review API can
later call SendTaskSuccess to resume the execution. Returns
immediately (the return value is ignored by SF in this pattern).

The same function is also the state machine's Catch target. That invocation
carries no task token, which is what distinguishes it: it marks the job FAILED
instead of parking it for review. Reusing this Lambda keeps the set of writers
to the Jobs lifecycle small — one function owns every transition the state
machine itself makes.
"""

from __future__ import annotations

import os
from typing import Any

import boto3

from shared import session
from shared.logging_config import bind_job_context, configure_logging

logger = configure_logging()
ddb = boto3.resource("dynamodb")


def _mark_failed(job_id: str, event: dict[str, Any], log: Any) -> dict[str, Any]:
    """Catch path: record a terminal FAILED, unless the job is already terminal.

    A rejected job reaches here too — SendTaskFailure surfaces as a state
    machine error — and REJECTED must survive. The transition table is what
    refuses that overwrite; there is no second copy of the rule here.
    """
    table = ddb.Table(os.environ["JOBS_TABLE"])
    job = table.get_item(Key={"jobId": job_id}).get("Item") or {}
    current = job.get("session_status") or (
        session.REJECTED if job.get("review_status") == "REJECTED" else session.PROCESSING
    )
    try:
        nxt = session.advance(str(current), session.FAIL)
    except session.InvalidTransitionError as exc:
        log.info("review_gate.fail_not_recorded", current=current, reason=str(exc))
        return {}

    reason = str(event.get("cause") or event.get("error") or "execution failed")[:256]
    now = session.now_iso()
    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression=(
            "SET #s = :s, session_status = :ss, updated_at = :u, failure_reason = :r"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "FAILED", ":ss": nxt, ":u": now, ":r": reason},
    )
    session.log_step(job_id, nxt, "system", {"reason": reason}, reviewed_at=now)
    log.warning("review_gate.marked_failed", reason=reason)
    return {}


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: {taskToken, jobId, outputKey, outputBucket, answerCount}.

    Without a taskToken the invocation is the state machine's Catch target.
    """
    job_id = event["jobId"]
    log = bind_job_context(logger, job_id=job_id)

    task_token = event.get("taskToken")
    if not task_token:
        return _mark_failed(job_id, event, log)

    table = ddb.Table(os.environ["JOBS_TABLE"])
    job = table.get_item(Key={"jobId": job_id}).get("Item") or {}
    now = session.now_iso()

    # tier_counts was written by the draft workbook pass; carry it into the
    # audit row so the log says how much work was parked, not just that it was.
    tier_counts = job.get("tier_counts", {})
    try:
        nxt: str | None = session.advance(
            str(job.get("session_status") or session.PROCESSING), session.REVIEW_PARKED
        )
    except session.InvalidTransitionError as exc:
        log.warning("review_gate.illegal_transition", error=str(exc))
        nxt = None

    update = (
        "SET task_token = :tt, review_status = :s, "
        "output_key = :ok, output_bucket = :ob, "
        "review_requested_at = :ra, updated_at = :ra"
    )
    values: dict[str, Any] = {
        ":tt": task_token,
        ":s": "PENDING",
        ":ok": event.get("outputKey", ""),
        ":ob": event.get("outputBucket", ""),
        ":ra": now,
    }
    if nxt:
        update += ", session_status = :ss"
        values[":ss"] = nxt

    table.update_item(
        Key={"jobId": job_id}, UpdateExpression=update, ExpressionAttributeValues=values
    )
    if nxt:
        session.log_step(job_id, nxt, "system", {"tier_counts": tier_counts}, reviewed_at=now)

    log.info("review_gate.stored_token", output_key=event.get("outputKey"))
    return {}
