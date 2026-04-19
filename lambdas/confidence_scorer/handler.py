"""Lambda handler for confidence scoring."""
from __future__ import annotations

from typing import Any

from scorer import score
from shared.logging_config import bind_job_context, configure_logging
from shared.models import GeneratedAnswer, PriorAnswerMatch, RetrievedPassage

logger = configure_logging()


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Expected event: question + retrieval + generation payload from upstream states."""
    job_id = event["jobId"]
    question_id = event["questionId"]
    log = bind_job_context(logger, job_id=job_id, question_id=question_id)

    passages = [RetrievedPassage(**p) for p in event["retrieval"]["passages"]]
    prior_matches = [PriorAnswerMatch(**m) for m in event["retrieval"]["prior_matches"]]
    generated = GeneratedAnswer(**event["generation"])
    guardrail_flags = event.get("guardrail_flags", [])
    topic_class = event["retrieval"].get("topic_class", "unclassified")

    composite, breakdown, tier = score(
        passages=passages,
        prior_matches=prior_matches,
        generated=generated,
        guardrail_flags=guardrail_flags,
        topic_class=topic_class,
    )

    log.info("score.computed", topic_class=topic_class, composite=composite,
             tier=tier.value, **breakdown.model_dump())

    return {
        "composite": composite,
        "tier": tier.value,
        "breakdown": breakdown.model_dump(),
    }
