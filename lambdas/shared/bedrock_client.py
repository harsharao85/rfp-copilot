"""Thin Bedrock client wrapper that enforces:
- Model ID constants (so we never drift to an unapproved model)
- Prompt caching on the stable prefix (voice + hard rules + answer patterns)
- Retry with exponential backoff on throttling
- Audit hash emission (prompt_hash, response_hash) for every call

Production use expects this to run inside a VPC with the Bedrock
PrivateLink endpoint (see network-stack.ts).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# SONNET: use 4.5 until aws-marketplace:Subscribe is granted to iamadmin for 4.6
# To upgrade: change to "us.anthropic.claude-sonnet-4-6" and bump SHARED_VERSION
SONNET = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _client() -> Any:
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def check_output(text: str) -> dict[str, Any]:
    """Apply the Bedrock Guardrail to generated output only (not the input question).
    Returns {"blocked": bool, "text": str}.
    Falls back to unblocked if the guardrail is unconfigured or the API fails."""
    guardrail_id = os.environ.get("GUARDRAIL_ID")
    if not guardrail_id:
        return {"blocked": False, "text": text}
    try:
        resp = _client().apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=os.environ.get("GUARDRAIL_VERSION", "DRAFT"),
            source="OUTPUT",
            content=[{"text": {"text": text}}],
        )
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            outputs = resp.get("outputs", [])
            blocked_msg = outputs[0]["text"] if outputs else "Response blocked by policy. An SME will review."
            return {"blocked": True, "text": blocked_msg}
        return {"blocked": False, "text": text}
    except ClientError:
        return {"blocked": False, "text": text}


def invoke(
    *,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    cache_system: bool = True,
    apply_guardrail: bool = False,
) -> dict[str, Any]:
    """Invoke a Claude model on Bedrock. Returns a dict with `text`,
    `model_id`, `prompt_hash`, `response_hash`, and `usage`.

    apply_guardrail is kept for signature compatibility but ignored —
    use check_output() after generation to apply the guardrail to output only."""
    client = _client()

    system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system_prompt}]
    if cache_system:
        # Cache the stable prefix. Savings kick in from the 2nd call
        # that uses the same system prompt (~10% of input-token cost).
        system_blocks[-1]["cache_control"] = {"type": "ephemeral"}

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    extra: dict[str, Any] = {}

    attempt = 0
    while True:
        try:
            resp = client.invoke_model(modelId=model_id, body=json.dumps(body), **extra)
            payload = json.loads(resp["body"].read())
            text = "".join(b.get("text", "") for b in payload.get("content", []))
            return {
                "text": text,
                "model_id": model_id,
                "prompt_hash": _hash(system_prompt + user_prompt),
                "response_hash": _hash(text),
                "usage": payload.get("usage", {}),
                "guardrail_applied": bool(extra),
            }
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"ThrottlingException", "ServiceUnavailableException"} and attempt < 5:
                time.sleep(2**attempt)
                attempt += 1
                continue
            raise
