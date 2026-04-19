"""Staleness daemon — marks LibraryFeedback entries as corroboration_stale.

Triggered two ways:
  1. EventBridge daily schedule (2 AM UTC) — routine overnight sweep.
  2. API Gateway POST /admin/staleness/trigger — on-demand during demo
     to visibly demonstrate the mechanism mid-session.

Algorithm (per approved answer in LibraryFeedback):
  1. Read corroborated_by: list of S3 URIs recorded at SME approval time.
  2. For each URI, GET the JSON sidecar from S3 and read updated_at.
  3. If any source's updated_at > approved_at → mark corroboration_stale=True.
     The answer falls out of the H signal in the next retrieval pass.
  4. If previously stale and all sources are now older → un-flag (source reverted).

Returns a summary so the demo can show the mechanism firing live.
"""
from __future__ import annotations

import functools
import json
import os
from typing import Any
from urllib.parse import urlparse

import boto3

from shared.logging_config import configure_logging

logger = configure_logging()


@functools.lru_cache(maxsize=1)
def _s3():
    return boto3.client("s3")


@functools.lru_cache(maxsize=1)
def _ddb():
    return boto3.resource("dynamodb")


def _get_source_updated_at(s3_uri: str) -> str | None:
    """Fetch a reference corpus JSON sidecar and return its updated_at field."""
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        body = _s3().get_object(Bucket=bucket, Key=key)["Body"].read()
        doc = json.loads(body)
        return doc.get("updated_at")
    except Exception as exc:
        logger.warning("staleness_daemon.s3_fetch_error", uri=s3_uri, error=str(exc))
        return None


def _check_staleness(item: dict) -> bool:
    """Return True if any corroborating primary source has updated since approval."""
    approved_at: str = item.get("approved_at", "")
    corroborated_by: list[str] = item.get("corroborated_by", [])

    if not approved_at or not corroborated_by:
        return False

    for uri in corroborated_by:
        updated_at = _get_source_updated_at(uri)
        if updated_at and updated_at > approved_at:
            logger.info(
                "staleness_daemon.stale_detected",
                answer_id=item.get("answerId"),
                approved_at=approved_at,
                source_updated_at=updated_at,
                source_uri=uri,
            )
            return True
    return False


def run_sweep() -> dict[str, Any]:
    """Scan LibraryFeedback and flag/unflag corroboration_stale."""
    table = _ddb().Table(os.environ["LIBRARY_FEEDBACK_TABLE"])

    resp = table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("sme_approved").eq(True)
    )
    items = resp.get("Items", [])

    checked = stale_flagged = stale_cleared = 0
    for item in items:
        checked += 1
        answer_id = item["answerId"]
        version = item["version"]
        is_stale = _check_staleness(item)
        was_stale = bool(item.get("corroboration_stale", False))

        if is_stale == was_stale:
            continue  # no change

        table.update_item(
            Key={"answerId": answer_id, "version": version},
            UpdateExpression="SET corroboration_stale = :s",
            ExpressionAttributeValues={":s": is_stale},
        )
        if is_stale:
            stale_flagged += 1
            logger.info("staleness_daemon.flagged_stale", answer_id=answer_id)
        else:
            stale_cleared += 1
            logger.info("staleness_daemon.cleared_stale", answer_id=answer_id)

    summary = {
        "checked": checked,
        "stale_flagged": stale_flagged,
        "stale_cleared": stale_cleared,
    }
    logger.info("staleness_daemon.sweep_complete", **summary)
    return summary


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handles both EventBridge schedule events and API Gateway proxy events."""
    source = event.get("source", "")
    if source == "aws.events":
        # EventBridge scheduled trigger
        summary = run_sweep()
        return {"statusCode": 200, "body": json.dumps(summary)}

    # API Gateway proxy trigger (on-demand demo endpoint)
    summary = run_sweep()
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(summary),
    }
