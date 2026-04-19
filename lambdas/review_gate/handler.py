"""Review gate Lambda — called by Step Functions waitForTaskToken.

Stores the task token on the job record so the review API can
later call SendTaskSuccess to resume the execution. Returns
immediately (the return value is ignored by SF in this pattern).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import boto3

from shared.logging_config import bind_job_context, configure_logging

logger = configure_logging()
ddb = boto3.resource("dynamodb")


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: {taskToken, jobId, outputKey, outputBucket, answerCount}."""
    job_id = event["jobId"]
    task_token = event["taskToken"]
    log = bind_job_context(logger, job_id=job_id)

    ddb.Table(os.environ["JOBS_TABLE"]).update_item(
        Key={"jobId": job_id},
        UpdateExpression=(
            "SET task_token = :tt, review_status = :s, "
            "output_key = :ok, output_bucket = :ob, "
            "review_requested_at = :ra"
        ),
        ExpressionAttributeValues={
            ":tt": task_token,
            ":s": "PENDING",
            ":ok": event.get("outputKey", ""),
            ":ob": event.get("outputBucket", ""),
            ":ra": datetime.now(timezone.utc).isoformat(),
        },
    )
    log.info("review_gate.stored_token", output_key=event.get("outputKey"))
    return {}
