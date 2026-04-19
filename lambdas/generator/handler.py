"""Answer synthesis via Claude Sonnet 4.6 on Bedrock.

Prompt structure (stable prefix is cached by Bedrock prompt caching):
  1. Brand voice guidelines
  2. Hard-rule policy summary (so the model *tries* to comply; hard_rules
     still enforces post-generation)
  3. Answer pattern library (canonical shapes by question type)

Per-question variable portion:
  4. Retrieved passages (Kendra top-K)
  5. Best prior SME-approved answer (if any)
  6. The question itself

The response is a JSON object conforming to GeneratedAnswer — the
answer_text + citations + invoked_customers + any risk flags the
model noticed. We never trust the model's self-reported confidence.
"""
from __future__ import annotations

import json
from typing import Any

from shared.bedrock_client import SONNET, check_output, invoke
from shared.logging_config import bind_job_context, configure_logging
from shared.models import GeneratedAnswer

logger = configure_logging()


SYSTEM_PROMPT = """You are an expert RFP response writer for a B2B SaaS company.
Write in a concise, factual, professional tone. Do NOT use marketing language
or superlatives. Cite evidence by document_id inline as [doc_id].

HARD RULES (you MUST follow; post-generation enforcement will catch violations):
- Do NOT quote or commit to prices, discounts, or commercial terms.
- Do NOT claim a compliance certification without an explicit citation.
- Do NOT name a customer unless that customer appears in the approved_customers list.
- Do NOT disparage competitors.
- Prefer present-tense factual claims over forward-looking statements.

OUTPUT FORMAT: return a single JSON object with these fields:
  answer_text: str
  citations: list of document_id strings actually referenced
  invoked_customers: list of customer names mentioned in your answer
  contains_pricing: bool (your own judgment)
  contains_compliance_claim: bool
  contains_forward_looking: bool
"""


def _user_prompt(
    question: str,
    passages: list[dict[str, Any]],
    prior_match: dict[str, Any] | None,
    approved_customers: list[str],
) -> str:
    lines = []
    lines.append("APPROVED REFERENCE CUSTOMERS (only these may be named):")
    lines.append(", ".join(approved_customers) if approved_customers else "(none)")
    lines.append("")
    if prior_match:
        lines.append("BEST PRIOR SME-APPROVED ANSWER:")
        lines.append(f"Prior question: {prior_match.get('question_text', '')}")
        lines.append(f"Prior answer: {prior_match.get('answer_text', '')}")
        lines.append("")
    lines.append("EVIDENCE PASSAGES:")
    for p in passages[:6]:
        lines.append(f"[{p['document_id']}] ({p['source_system']}): {p['excerpt'][:600]}")
    lines.append("")
    lines.append(f"QUESTION: {question}")
    lines.append("")
    lines.append("Return JSON only.")
    return "\n".join(lines)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    job_id = event["jobId"]
    question_id = event["questionId"]
    log = bind_job_context(logger, job_id=job_id, question_id=question_id)

    question: str = event["text"]
    retrieval = event["retrieval"]
    passages = retrieval.get("passages", [])
    prior = retrieval.get("prior_matches", [])
    prior_match = prior[0] if prior else None
    approved_customers = retrieval.get("reference_customers_matched", [])

    result = invoke(
        model_id=SONNET,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_user_prompt(question, passages, prior_match, approved_customers),
        max_tokens=768,
        temperature=0.2,
    )

    raw = result["text"].strip()
    # Model sometimes wraps JSON in markdown code fences; strip them.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("generator.json_decode_failed", raw=result["text"][:200])
        parsed = {
            "answer_text": result["text"].strip(),
            "citations": [],
            "invoked_customers": [],
            "contains_pricing": False,
            "contains_compliance_claim": False,
            "contains_forward_looking": False,
        }

    guardrail_result = check_output(parsed.get("answer_text", ""))

    answer = GeneratedAnswer(
        answer_text=guardrail_result["text"],
        citations=parsed.get("citations", []),
        invoked_customers=parsed.get("invoked_customers", []),
        contains_pricing=parsed.get("contains_pricing", False),
        contains_compliance_claim=parsed.get("contains_compliance_claim", False),
        contains_forward_looking=parsed.get("contains_forward_looking", False),
        model_id=result["model_id"],
        prompt_hash=result["prompt_hash"],
        response_hash=result["response_hash"],
    )

    log.info(
        "generator.done",
        answer_length=len(answer.answer_text),
        citation_count=len(answer.citations),
        invoked_customers=answer.invoked_customers,
    )
    return answer.model_dump()
