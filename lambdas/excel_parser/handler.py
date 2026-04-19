"""Lambda handler: download the RFP workbook from S3, parse, persist."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import boto3

from parser import parse_workbook
from shared.logging_config import bind_job_context, configure_logging

logger = configure_logging()

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Expected event: {"bucket": "...", "key": "...", "jobId": "..."}."""
    bucket = event["bucket"]
    key = event["key"]
    job_id = event["jobId"]
    log = bind_job_context(logger, job_id=job_id)

    log.info("parser.start", bucket=bucket, key=key)

    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        s3.download_fileobj(bucket, key, tmp)
        tmp.flush()
        job_id, questions = parse_workbook(tmp.name, job_id=job_id)

    log.info("parser.parsed", count=len(questions))

    questions_table = ddb.Table(os.environ["QUESTIONS_TABLE"])
    with questions_table.batch_writer() as batch:
        for q in questions:
            item = q.model_dump(mode="json")
            # DynamoDB key schema is camelCase; model uses snake_case
            item["jobId"] = item.pop("job_id")
            item["questionId"] = item.pop("question_id")
            batch.put_item(Item=item)

    jobs_table = ddb.Table(os.environ["JOBS_TABLE"])
    jobs_table.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET parsed_at = :t, question_count = :c, sources = :s",
        ExpressionAttributeValues={
            ":t": datetime.now(timezone.utc).isoformat(),
            ":c": len(questions),
            ":s": {"bucket": bucket, "key": key},
        },
    )

    # Write a question manifest to S3 for the writer Lambda and audit trail.
    # The questions array is also returned inline for the plain Map state
    # (30 questions × ~500 bytes ≈ 15 KB — well under the 256 KB state limit).
    # Scale-up path: Distributed Map + S3JsonItemReader (docs/architecture.md §11).
    manifest_bucket = os.environ["OUTPUT_BUCKET"]
    manifest_key = f"manifests/{job_id}/questions.json"
    manifest_items = [
        {
            "jobId": job_id,
            "questionId": q.question_id,
            "text": q.text,
            "section": q.section,
            "answer_cell": q.answer_cell.model_dump(),
            "confidence_cell": q.confidence_cell.model_dump(),
        }
        for q in questions
    ]
    s3.put_object(
        Bucket=manifest_bucket,
        Key=manifest_key,
        Body=json.dumps(manifest_items),
        ContentType="application/json",
    )

    return {
        "jobId": job_id,
        "questionCount": len(questions),
        "manifestBucket": manifest_bucket,
        "manifestKey": manifest_key,
        "questions": manifest_items,
    }
