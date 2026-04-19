"""Fire-and-forget trigger for Bedrock KB ingestion.

Invoked by three independent sources:
  1. S3 ObjectCreated events on the reference corpus bucket (keys under
     compliance/, product-docs/, prior-rfps/, sme-approved/). Runs whenever
     a source document or an SME approval lands.
  2. EventBridge weekly rate rule — safety net for the S3-event pipeline.
  3. EventBridge yearly rate rule — ensures at minimum one invocation
     per ~12 months even if both other triggers go silent.

Idempotent: Bedrock KB ingestion jobs are delta-based — only new or changed
S3 keys get re-embedded — so triggering on every PutObject is safe. If another
ingestion job is already running, ConflictException is caught and logged; the
changes will be picked up by the next trigger.
"""
from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.logging_config import configure_logging

logger = configure_logging()
bedrock_agent = boto3.client("bedrock-agent")


def _invocation_source(event: dict[str, Any]) -> str:
    """Tag where this invocation came from for log filtering / metrics."""
    if "Records" in event and event["Records"] and "s3" in event["Records"][0]:
        return "s3_event"
    if event.get("source") == "aws.events":
        return "eventbridge_schedule"
    return "manual"


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]
    ds_id = os.environ["DATA_SOURCE_ID"]
    source = _invocation_source(event)

    try:
        resp = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id,
        )
        job_id = resp["ingestionJob"]["ingestionJobId"]
        logger.info("ingestion_trigger.started", source=source, ingestion_job_id=job_id)
        return {"status": "started", "ingestionJobId": job_id}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        # Benign — another ingestion is already running, our S3 write will be
        # picked up by the next delta pass.
        if code in ("ConflictException", "ValidationException"):
            logger.info("ingestion_trigger.skipped", source=source, reason=code)
            return {"status": "skipped", "reason": code}
        logger.warning("ingestion_trigger.error", source=source, code=code, error=str(e))
        raise
