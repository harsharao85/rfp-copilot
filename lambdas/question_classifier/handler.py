"""Compound-question decomposition + topic classification + dispatch planning.

Two jobs in one Lambda to avoid an extra Step Functions state:

1. Classify topics: keyword-match the question against _TOPIC_PATTERNS to
   determine which source-authority domains are relevant.

2. Build dispatch plan: look up each topic in DISPATCH_TABLE and merge the
   plans (union sources, strictest corroboration settings).

3. Decompose if compound: Haiku 4.5 splits "Describe your SOC2 scope,
   audit frequency, and who conducts the audits" into atomic sub-questions.
   80% of RFP questions are atomic — skip the LLM call for them.

Returns: {compound, sub_questions, topics, dispatch_plan}
"""
from __future__ import annotations

import json
import re
from typing import Any

from dispatch import get_dispatch_plan
from shared.bedrock_client import HAIKU, invoke
from shared.logging_config import bind_job_context, configure_logging

logger = configure_logging()

# ---------------------------------------------------------------------------
# Topic keyword patterns — mirrors the retriever's _TOPIC_PATTERNS so
# both stages agree on topic boundaries. Production path: shared config file.
# ---------------------------------------------------------------------------
_TOPIC_PATTERNS: dict[str, list[str]] = {
    "encryption_at_rest":    [r"at.rest", r"aes.?256", r"storage.encr", r"encr.*rest"],
    "encryption_in_transit": [r"in.transit", r"\btls\b", r"\bhttps\b", r"transport.encr"],
    "key_management":        [r"\bbyok\b", r"customer.?managed.?key", r"key.rotation", r"\bkms\b"],
    "sso":                   [r"\bsso\b", r"\bsaml\b", r"\boidc\b", r"single.sign"],
    "mfa":                   [r"\bmfa\b", r"multi.?factor", r"two.?factor", r"authenticat"],
    "scim":                  [r"\bscim\b", r"user.provis", r"auto.*provis"],
    "dr_bcp":                [r"\bdr\b", r"\brto\b", r"\brpo\b", r"\bbcp\b", r"disaster.recov",
                              r"uptime", r"\bsla\b", r"availability", r"active.active"],
    "incident_response":     [r"incident", r"breach", r"security.event", r"notif.*72"],
    "ssdlc":                 [r"\bsdlc\b", r"secure.dev", r"owasp", r"code.review"],
    "pentest":               [r"pen.?test", r"penetration", r"vuln.*scan"],
    "sbom":                  [r"\bsbom\b", r"software.bill", r"dependency"],
    "dpa":                   [r"\bdpa\b", r"data.process.*agree", r"sub.?processor"],
    "data_residency":        [r"data.resid", r"data.local", r"region.*store", r"where.*data"],
    "soc2":                  [r"\bsoc.?2\b", r"\baicpa\b"],
    "iso27001":              [r"iso.?27001"],
    "fedramp":               [r"\bfedramp\b", r"federal.risk"],
    "gdpr":                  [r"\bgdpr\b", r"right.to.eras", r"data.subject"],
    "pricing":               [r"\bpric(e|ing)\b", r"\bcost\b", r"\bfee\b", r"\bsubscription\b",
                              r"per.seat", r"per.user", r"\bdiscount\b"],
    "customer_reference":    [r"customer.*example", r"case.stud", r"client.*reference",
                              r"reference.customer", r"who.*use"],
}

DECOMPOSE_SYSTEM = """You decompose compound RFP questions into atomic sub-questions.

Return a JSON array of strings. If the question is already atomic, return
an array with the single original question.

Rules:
- Preserve the original intent and technical terms verbatim.
- Do not add information not present in the question.
- Do not number the sub-questions.
- Maximum 5 sub-questions.

Example:
  Q: "Describe your SOC2 scope, audit frequency, and who conducts the audits."
  A: ["Describe your SOC2 scope.", "Describe your SOC2 audit frequency.", "Who conducts your SOC2 audits?"]
"""


def _extract_topics(text: str) -> list[str]:
    lower = text.lower()
    return [
        topic_id
        for topic_id, patterns in _TOPIC_PATTERNS.items()
        if any(re.search(p, lower) for p in patterns)
    ]


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    job_id = event["jobId"]
    question_id = event["questionId"]
    text: str = event["text"]
    log = bind_job_context(logger, job_id=job_id, question_id=question_id)

    topics = _extract_topics(text)
    dispatch_plan = get_dispatch_plan(topics)

    log.info(
        "classifier.topics",
        topics=topics,
        topic_class=dispatch_plan.topic_class,
        force_tier=dispatch_plan.force_tier.value if dispatch_plan.force_tier else None,
    )

    # Skip LLM decomposition if force_tier is set — no point generating answers
    # for questions we'll force RED (e.g. pricing). Return atomic.
    if dispatch_plan.force_tier:
        return {
            "compound": False,
            "sub_questions": [text],
            "topics": topics,
            "dispatch_plan": dispatch_plan.model_dump(mode="json"),
        }

    result = invoke(
        model_id=HAIKU,
        system_prompt=DECOMPOSE_SYSTEM,
        user_prompt=f"Q: {text}\nA:",
        max_tokens=256,
        temperature=0.0,
    )
    try:
        parts = json.loads(result["text"].strip())
        if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
            parts = [text]
    except json.JSONDecodeError:
        log.warning("classifier.json_decode_failed", raw=result["text"][:200])
        parts = [text]

    is_compound = len(parts) > 1
    log.info("classifier.decomposed", compound=is_compound, count=len(parts))

    return {
        "compound": is_compound,
        "sub_questions": parts,
        "topics": topics,
        "dispatch_plan": dispatch_plan.model_dump(mode="json"),
    }
