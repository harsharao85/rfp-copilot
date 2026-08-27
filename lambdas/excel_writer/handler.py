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

from shared import session
from shared.logging_config import bind_job_context, configure_logging
from shared.models import CellRef, ConfidenceBreakdown, FinalAnswer, Tier
from shared.provenance import Citation

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


def _citations(item: dict[str, Any], log: Any) -> list[Citation]:
    """Rebuild Citation objects written by the hard_rules handler.

    Rows predating the provenance wiring simply have no `citations_detailed`
    attribute; the writer then falls back to the flat document-id list. A row
    that has one but cannot be parsed is dropped with a warning rather than
    failing the whole workbook — one unrenderable source line is not worth
    losing every answer in the job.
    """
    citations: list[Citation] = []
    for raw in item.get("citations_detailed", []):
        try:
            citations.append(Citation(**_d2f(raw)))
        except Exception as exc:  # noqa: BLE001 — see docstring
            log.warning(
                "writer.citation_parse_failed",
                question_id=item.get("questionId"),
                error=str(exc),
            )
    return citations


def _final_answer(item: dict[str, Any], log: Any) -> FinalAnswer | None:
    """Rebuild one FinalAnswer from a Questions row, or None if the row is unusable.

    A row reaches here having been written by the hard_rules handler, which
    writes every attribute. A row written by anything else — the review API's
    approve path used to upsert a bare {answer_text, sme_edited} row for a
    questionId that did not exist — is missing raw_confidence, tier and the
    cell references, and the unguarded construction below raised KeyError.

    That exception surfaced as a Step Functions Catch, which marked the job
    FAILED *after* the SME had approved it: one malformed row cost the whole
    workbook, permanently. The approve path no longer creates such a row (see
    review_api.approve), but the writer runs on whatever is in the table and
    must not be the thing that fails the job. One unwritable answer is a gap in
    one cell; a raised exception is thirty-nine lost answers.
    """
    try:
        return FinalAnswer(
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
            citations_detailed=_citations(item, log),
            divergence_findings=list(item.get("divergence_findings", [])),
            divergence_tier_floor=(
                Tier(item["divergence_tier_floor"]) if item.get("divergence_tier_floor") else None
            ),
        )
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning(
            "writer.row_skipped",
            question_id=item.get("questionId"),
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def _tier_counts(answers: list[FinalAnswer]) -> dict[str, int]:
    """green/amber/red totals, so the review queue can show scale before a
    reviewer opens the job."""
    counts = {Tier.GREEN.value: 0, Tier.AMBER.value: 0, Tier.RED.value: 0}
    for ans in answers:
        counts[ans.tier.value] = counts.get(ans.tier.value, 0) + 1
    return counts


def _record_pass(
    job_id: str, *, final: bool, counts: dict[str, int], bucket: str, key: str
) -> None:
    """Write the writer's view of the job back to Jobs.

    The draft pass records scale only. The final pass — the one that runs after
    the SME's edits are in Questions — is what makes the job downloadable, so
    it also moves output_key onto the workbook the human actually edited and
    closes the lifecycle at COMPLETE.
    """
    table = ddb.Table(os.environ["JOBS_TABLE"])
    now = session.now_iso()
    if not final:
        table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET tier_counts = :tc, updated_at = :u",
            ExpressionAttributeValues={":tc": counts, ":u": now},
        )
        return

    job = table.get_item(Key={"jobId": job_id}).get("Item") or {}
    try:
        nxt: str | None = session.advance(
            str(job.get("session_status") or session.APPROVED), session.FINAL_WRITTEN
        )
    except session.InvalidTransitionError as exc:
        logger.warning("writer.illegal_transition", job_id=job_id, error=str(exc))
        nxt = None

    update = "SET tier_counts = :tc, updated_at = :u, output_key = :ok, output_bucket = :ob"
    values: dict[str, Any] = {":tc": counts, ":u": now, ":ok": key, ":ob": bucket}
    if nxt:
        update += ", session_status = :ss"
        values[":ss"] = nxt
    table.update_item(
        Key={"jobId": job_id}, UpdateExpression=update, ExpressionAttributeValues=values
    )
    if nxt:
        session.log_step(job_id, nxt, "system", {"output_key": key}, reviewed_at=now)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: {jobId, sourceBucket, sourceKey, stage}. outputBucket from OUTPUT_BUCKET env.

    ``stage`` is "draft" (before the review gate — this is the workbook the SME
    reads while reviewing) or "final" (after it — this is the workbook the SME
    downloads, containing their own edits). Answers are re-read from Questions
    on both passes, so the edits review_api wrote there need no special
    handling here.
    """
    job_id = event["jobId"]
    stage = str(event.get("stage", "draft")).lower()
    is_final = stage == "final"
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

    answers: list[FinalAnswer] = []
    skipped: list[str] = []
    for item in items:
        if "answer_text" not in item:
            continue
        answer = _final_answer(item, log)
        if answer is None:
            skipped.append(str(item.get("questionId", "?")))
            continue
        answers.append(answer)
    if skipped:
        log.warning("writer.rows_skipped", question_ids=skipped, count=len(skipped))

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

        suffix = "_final" if is_final else "_answered"
        output_key = f"answered/{job_id}/{Path(event['sourceKey']).stem}{suffix}.xlsx"
        extra: dict[str, str] = {
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ServerSideEncryption": "aws:kms",
        }
        if kms_key := os.environ.get("OUTPUT_KMS_KEY_ID"):
            extra["SSEKMSKeyId"] = kms_key
        s3.upload_file(str(dst), output_bucket, output_key, ExtraArgs=extra)

    counts = _tier_counts(answers)
    _record_pass(job_id, final=is_final, counts=counts, bucket=output_bucket, key=output_key)

    log.info("writer.done", output_key=output_key, answer_count=len(answers), stage=stage)

    return {
        "outputBucket": output_bucket,
        "outputKey": output_key,
        "answerCount": len(answers),
        "stage": stage,
        "tierCounts": counts,
    }
