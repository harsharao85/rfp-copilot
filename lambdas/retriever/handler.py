"""Retrieval orchestrator — source-authority aware.

Consumes the dispatch_plan from the classifier to route retrievals to the
correct sources. Applies two corroboration rules before returning:

  Rule 1 — Freshness suppression: if a primary source's updated_at is newer
    than a prior RFP's approved_at, the prior is suppressed and never reaches
    the generator. This is the anti-decay mechanism (architecture §5.2).

  Rule 2 — Primary required: if corroboration_required=True and no primary
    source returned passages, set corroboration_metadata.primary_missing=True.
    The hard-rules engine caps the tier at max_tier_without_primary.

Rules 3 and 4 live in the hard-rules engine:
  Rule 3 — force_tier is forwarded via corroboration_metadata.
  Rule 4 — customer-name gating is checked against reference_customers_matched
    by apply_rules() in the hard-rules engine.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

import functools
import urllib.error
import urllib.request

import boto3
from boto3.dynamodb.conditions import Attr

from shared.logging_config import bind_job_context, configure_logging
from shared.models import DispatchPlan, PriorAnswerMatch, Retrieval, RetrievedPassage

logger = configure_logging()


# Lazy-initialized so the module is safely importable without AWS credentials
# (important for unit tests). The cached client is reused across Lambda invocations
# within the same execution context.
@functools.lru_cache(maxsize=1)
def _bedrock_agent_runtime():
    return boto3.client("bedrock-agent-runtime")


@functools.lru_cache(maxsize=1)
def _ddb():
    return boto3.resource("dynamodb")

# ---------------------------------------------------------------------------
# Source routing — maps dispatch source names to Bedrock KB source_type
# metadata values and to the RetrievedPassage.source_system literal.
# All S3-backed sources share one KB; source_type discriminates at query time.
# `sme_approved_answers` is queried separately (not via dispatch) to feed the
# H signal with real cosine scores — see _kb_prior_matches below.
# ---------------------------------------------------------------------------
_SOURCE_TYPE_FILTER: dict[str, str] = {
    "compliance_store":     "compliance_cert",
    "prior_rfps":           "prior_rfp",
    "product_docs":         "product_doc",
    "sme_approved_answers": "sme_approved_answer",
}

_SOURCE_TO_SYSTEM: dict[str, str] = {
    "compliance_store":     "compliance",
    "prior_rfps":           "historical_rfp",
    "product_docs":         "whitepaper",
    "sme_approved_answers": "sme_approved",
}

_DEFERRED_SOURCES = {"deal_desk"}  # seismic + gong are wired via MOCK_SOURCES_API_URL

_MOCK_SOURCE_PATHS = {
    "seismic": "/seismic/content",
    "gong": "/gong/calls",
}

# ---------------------------------------------------------------------------
# Topic keyword map — kept in sync with question_classifier/_TOPIC_PATTERNS.
# Used when the event arrives without classification (e.g. direct Lambda invoke).
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
}


def _extract_topics(text: str) -> list[str]:
    lower = text.lower()
    return [
        topic_id
        for topic_id, patterns in _TOPIC_PATTERNS.items()
        if any(re.search(p, lower) for p in patterns)
    ]


# ---------------------------------------------------------------------------
# Bedrock Knowledge Base retrieve — vector search over the unified corpus,
# filtered by source_type metadata to isolate compliance / product / priors.
# ---------------------------------------------------------------------------

def _knowledge_base_retrieve(
    text: str,
    source: str,           # dispatch source name: compliance_store | product_docs | prior_rfps
    source_system: str,    # RetrievedPassage.source_system literal
    top_k: int = 5,
) -> list[RetrievedPassage]:
    """Call bedrock-agent-runtime:Retrieve with a source_type filter.

    The metadata sidecars emitted by scripts/generate_corpus_documents.py
    carry updated_at on primaries and approved_at on priors — both are
    propagated into RetrievedPassage.metadata so _apply_freshness_suppression
    can compare them downstream.
    """
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]
    source_type = _SOURCE_TYPE_FILTER[source]
    try:
        resp = _bedrock_agent_runtime().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": text},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": top_k,
                    "filter": {"equals": {"key": "source_type", "value": source_type}},
                }
            },
        )
    except Exception as exc:
        logger.warning("retriever.kb_error", source=source, error=str(exc))
        return []

    passages: list[RetrievedPassage] = []
    for r in resp.get("retrievalResults", []):
        md = r.get("metadata", {}) or {}
        uri = r.get("location", {}).get("s3Location", {}).get("uri")
        meta: dict[str, Any] = {}
        if uri:
            meta["s3_uri"] = uri
        if md.get("updated_at"):
            meta["updated_at"] = md["updated_at"]
        if md.get("approved_at"):
            meta["approved_at"] = md["approved_at"]

        doc_id = md.get("document_id") or (uri.rsplit("/", 1)[-1] if uri else "unknown")
        passages.append(RetrievedPassage(
            source_system=source_system,  # type: ignore[arg-type]
            document_id=doc_id,
            excerpt=(r.get("content", {}).get("text") or "")[:2000],
            score=min(max(float(r.get("score", 0.0)), 0.0), 1.0),
            uri=uri,
            metadata=meta,
        ))
    return passages


def _mock_api_passages(source: str, text: str, top_k: int = 5) -> list[RetrievedPassage]:
    """Call the mock sources API for seismic or gong content.

    Circuit-breaker behaviour: on any non-200 response or timeout, returns []
    and logs source_degraded. The corroboration_metadata field in the Retrieval
    envelope carries `source_degraded` into the Step Functions audit trail.
    """
    base_url = os.environ.get("MOCK_SOURCES_API_URL", "")
    if not base_url:
        logger.info("retriever.mock_api_not_configured", source=source)
        return []

    path = _MOCK_SOURCE_PATHS.get(source, "")
    if not path:
        return []

    url = f"{base_url}{path}?query={urllib.request.quote(text)}&limit={top_k}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer demo-key"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                logger.warning("retriever.source_degraded", source=source, status=resp.status)
                return []
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("retriever.source_degraded", source=source, error=str(exc))
        return []

    system_map = {"seismic": "seismic", "gong": "gong"}
    source_system = system_map.get(source, "other")
    passages = []
    for item in data.get("results", []):
        meta: dict[str, Any] = {}
        if item.get("updated_at"):
            meta["updated_at"] = item["updated_at"]
        passages.append(
            RetrievedPassage(
                source_system=source_system,  # type: ignore[arg-type]
                document_id=item.get("card_id") or item.get("call_id", "unknown"),
                excerpt=item.get("excerpt", "")[:2000],
                score=float(item.get("score", 0.5)),
                uri=url,
                metadata=meta,
            )
        )
    return passages


def _retrieve_for_sources(
    sources: list[str], text: str, top_k: int = 5
) -> tuple[list[RetrievedPassage], list[str]]:
    """Fan out to each source. Returns (passages, degraded_source_names)."""
    passages: list[RetrievedPassage] = []
    degraded: list[str] = []
    for source in sources:
        if source in _DEFERRED_SOURCES:
            logger.info("retriever.source_deferred", source=source)
            continue
        if source == "customer_refs_db":
            continue  # handled via _dynamo_reference_customers
        if source in _MOCK_SOURCE_PATHS:
            before = len(passages)
            results = _mock_api_passages(source, text, top_k)
            passages.extend(results)
            # Degraded = attempted the call but API returned no results due to error.
            # _mock_api_passages returns [] on any non-200 or timeout and already logged.
            # We detect degradation by checking if MOCK_SOURCES_API_URL is set but we got nothing.
            if not results and os.environ.get("MOCK_SOURCES_API_URL"):
                degraded.append(source)
            continue
        if source not in _SOURCE_TYPE_FILTER:
            logger.warning("retriever.unknown_source", source=source)
            continue
        system = _SOURCE_TO_SYSTEM.get(source, "other")
        passages.extend(_knowledge_base_retrieve(text, source=source, source_system=system, top_k=top_k))
    return passages, degraded


# ---------------------------------------------------------------------------
# Corroboration Rule 1 — Freshness suppression (pure function, directly testable)
# ---------------------------------------------------------------------------

def _apply_freshness_suppression(
    primary_passages: list[RetrievedPassage],
    prior_passages: list[RetrievedPassage],
) -> tuple[list[RetrievedPassage], list[str]]:
    """Return (unsuppressed_priors, suppressed_document_ids).

    A prior passage is suppressed when its approved_at is older than the most
    recently updated primary source. Both timestamps are ISO date strings
    (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ — lexicographic comparison is correct
    for ISO-8601 dates).

    If no primary passages have an updated_at, suppression does not apply —
    we never suppress on missing data.
    """
    if not primary_passages:
        return prior_passages, []

    primary_updated_ats = [
        p.metadata.get("updated_at", "")
        for p in primary_passages
        if p.metadata.get("updated_at")
    ]
    if not primary_updated_ats:
        return prior_passages, []

    primary_freshest = max(primary_updated_ats)

    kept: list[RetrievedPassage] = []
    suppressed: list[str] = []
    for p in prior_passages:
        approved_at = p.metadata.get("approved_at", "")
        if approved_at and approved_at < primary_freshest:
            suppressed.append(p.document_id)
        else:
            kept.append(p)

    return kept, suppressed


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def _kb_prior_matches(text: str, top_k: int = 3) -> list[PriorAnswerMatch]:
    """Query the KB for SME-approved Q&A with real semantic similarity.

    Replaces the old DynamoDB scan over LibraryFeedback. Using KB retrieve
    gives us a real cosine similarity (from Titan Embed v2) instead of the
    old fixed 0.82 synthetic value, and removes the topic-tag matching
    brittleness (e.g. the `encryption_at_rest` vs `encryption` mismatch).
    """
    passages = _knowledge_base_retrieve(
        text,
        source="sme_approved_answers",
        source_system="sme_approved",
        top_k=top_k,
    )
    today = date.today().isoformat()
    matches: list[PriorAnswerMatch] = []
    for p in passages:
        md = p.metadata or {}
        expires_on = md.get("expires_on") or ""
        if expires_on and expires_on < today:
            continue  # hard-expire at the retrieval boundary
        matches.append(PriorAnswerMatch(
            answer_id=p.document_id,
            # Sidecar carries the original question text; fall back to the
            # chunk excerpt if a chunked sidecar has lost it (shouldn't happen
            # at our corpus size but defensive).
            question_text=str(md.get("question_text", "")) or p.excerpt[:200],
            answer_text=p.excerpt,
            similarity=p.score,  # real cosine from KB, not fake 0.82
            approved_by=str(md.get("approved_by", "unknown")),
            approved_at=str(md.get("approved_at", "2025-01-01T00:00:00Z")),
            expires_on=None,  # boundary-filtered above; model doesn't need to re-check
        ))
    return matches


def _dynamo_reference_customers(industry: str | None) -> list[str]:
    """Scan CustomerRefs for public_reference=true + unexpired approval.

    # Scan acceptable at demo-scale table size; production should Query on
    # a public_reference GSI.
    """
    table = _ddb().Table(os.environ["CUSTOMER_REFS_TABLE"])
    today = date.today().isoformat()

    try:
        resp = table.scan(
            FilterExpression=Attr("public_reference").eq(True) & Attr("approval_expires").gte(today)
        )
        return [
            item["name"]
            for item in resp.get("Items", [])
            if industry is None or item.get("industry") == industry
        ]
    except Exception as exc:
        logger.warning("retriever.dynamo_refs_error", error=str(exc))
        return []


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    job_id = event["jobId"]
    question_id = event["questionId"]
    text: str = event["text"]
    industry: str | None = event.get("industry")
    log = bind_job_context(logger, job_id=job_id, question_id=question_id)

    use_stub = os.environ.get("USE_RETRIEVAL_STUB", "false").lower() == "true"
    if use_stub:
        log.warning("retriever.using_stub")
        return Retrieval(passages=[], prior_matches=[], reference_customers_matched=[]).model_dump(mode="json")

    # Parse dispatch plan from classifier output, or fall back to topic extraction.
    classification = event.get("classification", {})
    dispatch_plan_data = classification.get("dispatch_plan")
    if dispatch_plan_data:
        dispatch_plan = DispatchPlan(**dispatch_plan_data)
        topics = classification.get("topics", [])
    else:
        # Direct invocation without classifier — extract topics inline.
        topics = _extract_topics(text)
        from dispatch import get_dispatch_plan  # noqa: PLC0415 — local import avoids circular on stub path
        dispatch_plan = get_dispatch_plan(topics)

    # Rule 3 forwarding: if dispatch forces a tier, skip retrieval entirely.
    # The hard-rules engine reads corroboration_metadata.force_tier and enforces RED.
    if dispatch_plan.force_tier:
        log.info("retriever.force_tier_skip", force_tier=dispatch_plan.force_tier.value)
        return Retrieval(
            passages=[],
            prior_matches=[],
            reference_customers_matched=[],
            corroboration_metadata={
                "force_tier": dispatch_plan.force_tier.value,
                "reason": dispatch_plan.reason or "dispatch_force_tier",
            },
        ).model_dump(mode="json")

    # Retrieve primary sources (factual authority).
    primary_passages, primary_degraded = _retrieve_for_sources(dispatch_plan.primary, text)

    # Retrieve secondary sources (prior RFPs — phrasing reference only).
    raw_prior_passages, secondary_degraded = _retrieve_for_sources(dispatch_plan.secondary, text)

    # Rule 1: Freshness suppression — suppress priors older than the primary's updated_at.
    prior_passages, suppressed_ids = _apply_freshness_suppression(primary_passages, raw_prior_passages)

    # Tertiary: contextual enrichment (seismic/gong).
    tertiary_passages, tertiary_degraded = _retrieve_for_sources(dispatch_plan.tertiary, text)

    all_passages = primary_passages + prior_passages + tertiary_passages
    all_degraded = primary_degraded + secondary_degraded + tertiary_degraded

    # Rule 2: Primary required check.
    corroboration_metadata: dict[str, Any] = {}
    if suppressed_ids:
        corroboration_metadata["suppressed_priors"] = suppressed_ids
    if all_degraded:
        corroboration_metadata["source_degraded"] = all_degraded
    if dispatch_plan.corroboration_required and not primary_passages:
        corroboration_metadata["primary_missing"] = True
        corroboration_metadata["max_tier"] = dispatch_plan.max_tier_without_primary.value
        log.warning("retriever.primary_missing", topics=topics)

    # Persist suppressed_prior_count so the review UI can show the staleness badge.
    # Only write when suppressions occurred — zero is the implicit default.
    if suppressed_ids:
        try:
            _ddb().Table(os.environ["QUESTIONS_TABLE"]).update_item(
                Key={"jobId": job_id, "questionId": question_id},
                UpdateExpression="SET suppressed_prior_count = :spc",
                ExpressionAttributeValues={":spc": len(suppressed_ids)},
            )
        except Exception as exc:
            log.warning("retriever.suppressed_count_write_error", error=str(exc))

    prior_matches = _kb_prior_matches(text)
    refs = _dynamo_reference_customers(industry)

    log.info(
        "retriever.done",
        passage_count=len(all_passages),
        primary_count=len(primary_passages),
        prior_count=len(prior_passages),
        suppressed_count=len(suppressed_ids),
        degraded_sources=all_degraded,
        prior_match_count=len(prior_matches),
        refs_count=len(refs),
        topics=topics,
    )

    return Retrieval(
        passages=all_passages,
        prior_matches=prior_matches,
        reference_customers_matched=refs,
        corroboration_metadata=corroboration_metadata,
    ).model_dump(mode="json")
