"""Lambda handler for hard-rule enforcement."""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import boto3

from rules import RULES_VERSION, apply_rules
from shared.logging_config import bind_job_context, configure_logging
from shared.models import Tier


logger = configure_logging()
ddb = boto3.resource("dynamodb")


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event shape: {jobId, questionId, generation, score, retrieval, answer_cell, confidence_cell}."""
    job_id = event["jobId"]
    question_id = event["questionId"]
    log = bind_job_context(logger, job_id=job_id, question_id=question_id)

    current_tier = Tier(event["score"]["tier"])
    answer_text: str = event["generation"]["answer_text"]
    invoked_customers: list[str] = event["generation"].get("invoked_customers", [])
    approved_customers: list[str] = event["retrieval"].get("reference_customers_matched", [])
    corroboration_meta: dict = event["retrieval"].get("corroboration_metadata", {})

    result = apply_rules(
        answer_text=answer_text,
        invoked_customers=invoked_customers,
        approved_reference_customers=approved_customers,
        current_tier=current_tier,
    )

    # Source-authority rule 3: dispatch force_tier (e.g. pricing → RED).
    # Belt-and-suspenders: PRICING_PATTERNS in apply_rules() already catches
    # pricing language; this catches cases where the generator produces a
    # clean deflection ("pricing not available") that contains no pricing terms.
    if corroboration_meta.get("force_tier") == "red":
        result.final_tier = Tier.RED
        trigger = corroboration_meta.get("reason", "dispatch_force_tier")
        if trigger not in result.triggers:
            result.triggers.append(trigger)

    # Source-authority rule 2: primary source required but not found.
    # Cap tier at AMBER — the answer lacks the authoritative source it needs.
    if corroboration_meta.get("primary_missing") and result.final_tier == Tier.GREEN:
        result.final_tier = Tier.AMBER
        result.triggers.append("unauthorized_without_primary")
        result.reviewer_required = True

    log.info(
        "hard_rules.applied",
        initial_tier=current_tier.value,
        final_tier=result.final_tier.value,
        triggers=result.triggers,
        rules_version=RULES_VERSION,
    )

    # Persist FinalAnswer fields onto the existing question item so the writer
    # can query questionsTable by jobId without needing state machine payloads.
    # topic_ids + primary_passage_uris are stored here so the review_api can
    # write LibraryFeedback entries with the correct corroborated_by provenance.
    breakdown = event["score"].get("breakdown", {})
    topic_ids = event.get("classification", {}).get("topics", [])
    primary_systems = {"compliance", "whitepaper"}
    primary_passage_uris = [
        p.get("uri", "")
        for p in event.get("retrieval", {}).get("passages", [])
        if p.get("source_system") in primary_systems and p.get("uri")
    ]
    ddb.Table(os.environ["QUESTIONS_TABLE"]).update_item(
        Key={"jobId": job_id, "questionId": question_id},
        UpdateExpression=(
            "SET answer_text = :at, citations = :ci, raw_confidence = :rc, "
            "#tier_attr = :ti, confidence_breakdown = :cb, "
            "hard_rule_triggers = :hr, reviewer_required = :rr, "
            "topic_ids = :tids, primary_passage_uris = :ppu"
        ),
        ExpressionAttributeNames={"#tier_attr": "tier"},  # reserved word
        ExpressionAttributeValues={
            ":at": answer_text,
            ":ci": event["generation"].get("citations", []),
            ":rc": Decimal(str(event["score"]["composite"])),
            ":ti": result.final_tier.value,
            ":cb": {k: Decimal(str(v)) for k, v in breakdown.items() if isinstance(v, (int, float))},
            ":hr": result.triggers,
            ":rr": result.reviewer_required,
            ":tids": topic_ids,
            ":ppu": primary_passage_uris,
        },
    )

    return {
        "final_tier": result.final_tier.value,
        "hard_rule_triggers": result.triggers,
        "reviewer_required": result.reviewer_required,
        "rules_version": RULES_VERSION,
    }
