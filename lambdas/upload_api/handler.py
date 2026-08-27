"""Upload API Lambda.

Routes (HTTP API Gateway proxy format):
  POST /upload/presign          → {uploadUrl, bucket, key, jobId}
  POST /upload/{jobId}/start    → {jobId, status}
  GET  /upload/{jobId}/status   → {jobId, status, outputKey?, reviewStatus?}
  GET  /upload/{jobId}/download → {downloadUrl}

IAM scope (least privilege per spec):
  incoming bucket  — s3:PutObject  (to sign presigned PUT URLs)
  output bucket    — s3:GetObject  (to sign presigned GET URLs)
  jobs table       — PutItem, UpdateItem, GetItem
  state machine    — states:StartExecution
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config

from shared import session
from shared.logging_config import configure_logging

logger = configure_logging()

# Set by the CDK stack from the `corsOrigins` context value. API Gateway's own
# CORS configuration enforces the full origin list; this header covers the
# non-preflight response. Falls back to "*" only when the stack has not set it
# — never to a particular site.
CORS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
}

_s3_client = None
_sfn_client = None
_ddb_resource = None


def _s3() -> Any:
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _s3_client


def _sfn() -> Any:
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _ddb() -> Any:
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource("dynamodb")
    return _ddb_resource


def _resp(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(body, default=str),
    }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def presign(body: dict[str, Any]) -> dict[str, Any]:
    """Generate a pre-signed PUT URL and create the initial job record."""
    filename = body.get("filename", "rfp.xlsx")
    # Sanitise filename — keep only the base name, replace spaces
    safe_name = filename.split("/")[-1].replace(" ", "_") or "rfp.xlsx"

    job_id = f"job-{str(uuid.uuid4())[:8]}"
    key = f"incoming/{job_id}/{safe_name}"
    bucket = os.environ["INCOMING_BUCKET"]
    now = datetime.now(UTC).isoformat()

    upload_url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": "application/octet-stream"},
        ExpiresIn=300,  # 5 minutes
    )

    # session_status is the authoritative lifecycle; `status` stays exactly as
    # it was so the existing upload-page polling is unaffected.
    _ddb().Table(os.environ["JOBS_TABLE"]).put_item(
        Item={
            "jobId": job_id,
            "status": "UPLOADING",
            "session_status": session.advance(None, session.PRESIGN),
            "key": key,
            "filename": safe_name,
            "created_at": now,
            "updated_at": now,
            "sme_edit_count": 0,
        }
    )
    session.log_step(job_id, session.CREATED, "system", {}, reviewed_at=now)

    logger.info("upload_api.presigned", job_id=job_id, key=key)
    return _resp(200, {"uploadUrl": upload_url, "bucket": bucket, "key": key, "jobId": job_id})


def _try_advance(job_id: str, current: str | None, event: str) -> str | None:
    """advance() that logs and returns None instead of failing the request.

    A replayed or out-of-order call must not write an illegal session_status,
    but it must also not break an upload the user is watching. The illegal move
    is refused; the rest of the route carries on as it did before.
    """
    try:
        return session.advance(current, event)
    except session.InvalidTransitionError as exc:
        logger.warning("upload_api.illegal_transition", job_id=job_id, error=str(exc))
        return None


def start(job_id: str) -> dict[str, Any]:
    """Kick off the Step Functions execution and mark the job PROCESSING."""
    jobs_table = _ddb().Table(os.environ["JOBS_TABLE"])
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})

    bucket = os.environ["INCOMING_BUCKET"]
    key = job["key"]
    sm_arn = os.environ["STATE_MACHINE_ARN"]

    # The bytes are in S3 by the time the browser calls /start — that is a
    # distinct lifecycle fact from "the pipeline is running", and the audit
    # trail records both.
    current = job.get("session_status") or session.CREATED
    uploaded = _try_advance(job_id, current, session.UPLOAD_COMPLETE)
    if uploaded:
        now = session.now_iso()
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET session_status = :ss, updated_at = :u",
            ExpressionAttributeValues={":ss": uploaded, ":u": now},
        )
        session.log_step(job_id, uploaded, "system", {}, reviewed_at=now)

    _sfn().start_execution(
        stateMachineArn=sm_arn,
        name=job_id,
        input=json.dumps({"bucket": bucket, "key": key, "jobId": job_id}),
    )

    processing = _try_advance(job_id, uploaded or current, session.EXECUTION_STARTED)
    now = session.now_iso()
    if processing:
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, session_status = :ss, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "PROCESSING", ":ss": processing, ":u": now},
        )
    else:
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "PROCESSING", ":u": now},
        )
    # The PROCESSING audit row is written by excel_parser, which is the first
    # place question_count — the detail the ruling asks for on that step — is
    # known. One row per transition, written where it can say something.

    logger.info("upload_api.started", job_id=job_id)
    return _resp(200, {"jobId": job_id, "status": "PROCESSING"})


def status(job_id: str) -> dict[str, Any]:
    """Return derived status for the upload UI's polling loop."""
    job = _ddb().Table(os.environ["JOBS_TABLE"]).get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})

    review_status = job.get("review_status", "")
    raw_status = job.get("status", "UPLOADING")
    session_status = job.get("session_status", "")

    # Map to upload UI status labels.
    #
    # SUCCEEDED is what makes the review page hand over a download link, so it
    # must not be reported until the *final* workbook — the one containing the
    # SME's own edits — has been written. Between approval and that write the
    # job reports FINALIZING, which both UIs treat as "keep polling".
    # A job with no session_status at all is pre-lifecycle: fall back to the
    # original mapping rather than stranding it.
    if session_status == "FAILED":
        derived = "FAILED"
    elif session_status == "COMPLETE":
        derived = "SUCCEEDED"
    elif review_status == "PENDING":
        derived = "WAITING_FOR_REVIEW"
    elif review_status == "APPROVED":
        derived = "SUCCEEDED" if not session_status else "FINALIZING"
    elif review_status == "REJECTED":
        derived = "FAILED"
    else:
        derived = raw_status  # UPLOADING | PROCESSING

    return _resp(
        200,
        {
            "jobId": job_id,
            "status": derived,
            "outputKey": job.get("output_key", ""),
            "reviewStatus": review_status,
            "sessionStatus": session_status,
            "createdAt": job.get("created_at", ""),
        },
    )


def download(job_id: str) -> dict[str, Any]:
    """Return a pre-signed GET URL for the output workbook."""
    job = _ddb().Table(os.environ["JOBS_TABLE"]).get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        return _resp(404, {"error": "job not found"})

    output_key = job.get("output_key", "")
    if not output_key:
        return _resp(404, {"error": "output not ready"})

    download_url = _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["OUTPUT_BUCKET"], "Key": output_key},
        ExpiresIn=900,  # 15 minutes
    )

    logger.info("upload_api.download_signed", job_id=job_id, key=output_key)
    return _resp(200, {"downloadUrl": download_url})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path: str = event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}
    job_id = path_params.get("jobId")
    body: dict[str, Any] = json.loads(event.get("body") or "{}")

    if method == "POST" and path.endswith("/upload/presign"):
        return presign(body)
    if method == "POST" and job_id and path.endswith("/start"):
        return start(job_id)
    if method == "GET" and job_id and path.endswith("/status"):
        return status(job_id)
    if method == "GET" and job_id and path.endswith("/download"):
        return download(job_id)

    return _resp(404, {"error": f"No route: {method} {path}"})
