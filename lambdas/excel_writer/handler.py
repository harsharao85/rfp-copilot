"""Lambda handler: read source workbook from S3, write annotated copy."""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from writer import write_output
from shared.logging_config import bind_job_context, configure_logging
from shared.models import CellRef, ConfidenceBreakdown, FinalAnswer, Tier

logger = configure_logging()
s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")


def _d2f(obj: Any) -> Any:
    """Recursively convert Decimal → float for Pydantic model construction."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _d2f(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_d2f(v) for v in obj]
    return obj


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: {jobId, sourceBucket, sourceKey}. outputBucket read from OUTPUT_BUCKET env."""
    job_id = event["jobId"]
    log = bind_job_context(logger, job_id=job_id)

    # Fetch all answered questions for this job from DynamoDB.
    # hard_rules handler wrote answer_text, tier, etc. onto each item.
    table = ddb.Table(os.environ["QUESTIONS_TABLE"])
    items: list[dict[str, Any]] = []
    query_kwargs: dict[str, Any] = {"KeyConditionExpression": Key("jobId").eq(job_id)}
    while True:
        resp = table.query(**query_kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        query_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    answers = [
        FinalAnswer(
            question_id=item["questionId"],
            answer_text=item["answer_text"],
            citations=item.get("citations", []),
            raw_confidence=float(item["raw_confidence"]),
            tier=Tier(item["tier"]),
            confidence_breakdown=ConfidenceBreakdown(**_d2f(item["confidence_breakdown"])),
            hard_rule_triggers=item.get("hard_rule_triggers", []),
            reviewer_required=bool(item.get("reviewer_required", True)),
            answer_cell=CellRef(**item["answer_cell"]),
            confidence_cell=CellRef(**item["confidence_cell"]),
        )
        for item in items
        if "answer_text" in item
    ]

    output_bucket = os.environ["OUTPUT_BUCKET"]

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.xlsx"
        dst = Path(tmpdir) / "answered.xlsx"
        s3.download_file(event["sourceBucket"], event["sourceKey"], str(src))

        write_output(
            source_path=src,
            dest_path=dst,
            answers=answers,
            job_id=job_id,
        )

        output_key = f"answered/{job_id}/{Path(event['sourceKey']).stem}_answered.xlsx"
        extra: dict[str, str] = {
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ServerSideEncryption": "aws:kms",
        }
        if kms_key := os.environ.get("OUTPUT_KMS_KEY_ID"):
            extra["SSEKMSKeyId"] = kms_key
        s3.upload_file(str(dst), output_bucket, output_key, ExtraArgs=extra)

    log.info("writer.done", output_key=output_key, answer_count=len(answers))

    return {
        "outputBucket": output_bucket,
        "outputKey": output_key,
        "answerCount": len(answers),
    }
