"""Mock Seismic + Gong API Lambda.

Simulates two external SaaS REST APIs behind a single Lambda + API Gateway:
  GET /seismic/content?query=<text>&limit=<n>
  GET /gong/calls?query=<text>&limit=<n>

Deliberately realistic bad behaviour for demo purposes:
  - 5% random 503 (simulates source degradation — fires circuit breaker)
  - 10% 2s tail latency (realistic P95)
  - Typical ~50ms latency otherwise

Auth: expects any Authorization header. Returns 401 if absent.

This Lambda is the demo stand-in for a real Seismic OAuth integration
and a real Gong API key integration. The retriever calls these paths
when MOCK_SOURCES_API_URL is set. Circuit breaker visibility: when this
Lambda returns 503 the retriever logs `source_degraded` and the
corroboration_metadata field carries the flag into the audit trail.
"""
from __future__ import annotations

import json
import random
import time
from typing import Any

# Hardcoded synthetic content — matches generate_synthetic_data.py's
# SEISMIC_CARDS and GONG_TRANSCRIPTS. Inline here so the Lambda is
# self-contained with no S3 or DynamoDB reads on every call.

_SEISMIC_CARDS = [
    {
        "card_id": "seismic-card-001",
        "title": "Enterprise Security Overview One-Pager",
        "excerpt": "SOC 2 Type II (Trust Principles: Security, Availability, Confidentiality), ISO 27001, active FedRAMP Moderate authorization in progress. Encryption at rest AES-256-GCM, in transit TLS 1.2+. MFA mandatory for admin roles.",
        "updated_at": "2025-10-01",
        "owner": "Product Marketing",
    },
    {
        "card_id": "seismic-card-002",
        "title": "FAQ — Reference Customer Stories (Financial Services)",
        "excerpt": "Approved public references in the financial services vertical. Consult the Customer References DB before naming any customer in an outbound document.",
        "updated_at": "2025-11-15",
        "owner": "Sales Enablement",
    },
    {
        "card_id": "seismic-card-003",
        "title": "Data Residency Configuration Matrix",
        "excerpt": "Tenant creation supports region selection: us-east-1, us-west-2, eu-west-1, eu-central-1, ap-southeast-2. Cross-region replication is an opt-in DR feature. Residency enforced at the storage layer via KMS CMK scoping.",
        "updated_at": "2025-08-20",
        "owner": "Security Engineering",
    },
    {
        "card_id": "seismic-card-004",
        "title": "SSDLC Summary Deck",
        "excerpt": "Secure SDLC aligned to OWASP SAMM. SAST: Semgrep. SCA: Snyk. Container scanning: Trivy + ECR scan. Threat modeling: STRIDE per service. Annual third-party pentest. SBOM generated per build.",
        "updated_at": "2025-09-05",
        "owner": "Application Security",
    },
    {
        "card_id": "seismic-card-005",
        "title": "SSO and MFA Integration Guide",
        "excerpt": "SAML 2.0 and OIDC supported for customer SSO. OAuth 2.0 Authorization Code with PKCE for native integrations. SCIM 2.0 for automated provisioning. MFA supports TOTP, WebAuthn/FIDO2. Hardware keys required for admin roles.",
        "updated_at": "2025-10-20",
        "owner": "Product",
    },
]

_GONG_CALLS = [
    {
        "call_id": "gong-call-001",
        "title": "Discovery call — prospect asks about FedRAMP timeline",
        "date": "2026-04-01",
        "excerpt": "Prospect asked when FedRAMP Moderate authorization will be live. SE responded that the ATO assessment is in progress and declined to commit to a specific date in writing.",
        "topic_ids": ["fedramp"],
    },
    {
        "call_id": "gong-call-002",
        "title": "Technical deep-dive — encryption and key management",
        "date": "2026-03-24",
        "excerpt": "Discussed BYOK via AWS KMS CMK in the customer's account. Confirmed key deletion makes data unreadable. All data-in-transit uses TLS 1.2 minimum with HSTS. TLS 1.0/1.1 deprecated end-to-end.",
        "topic_ids": ["encryption_at_rest", "encryption_in_transit", "key_management"],
    },
    {
        "call_id": "gong-call-003",
        "title": "Procurement call — DPA and sub-processors",
        "date": "2026-04-06",
        "excerpt": "Walked through standard DPA. EU SCCs are incorporated. Public sub-processor list updated with 30-day advance notice. Prospect requested a reference customer — deferred pending approval.",
        "topic_ids": ["dpa", "gdpr", "customer_reference"],
    },
    {
        "call_id": "gong-call-004",
        "title": "Security architecture call — incident response and pentest",
        "date": "2026-03-15",
        "excerpt": "SOC is 24x7. NIST 800-61 framework. Customer notification within 72 hours of confirmed incident. Annual third-party pentest; executive summary available under NDA.",
        "topic_ids": ["incident_response", "pentest"],
    },
]

RNG = random.Random()  # not seeded — deliberate randomness for latency/errors


def _keyword_score(text: str, query: str) -> float:
    """Simple keyword overlap score for filtering content."""
    if not query:
        return 1.0
    q_words = set(query.lower().split())
    t_words = set(text.lower().split())
    return len(q_words & t_words) / max(len(q_words), 1)


def _simulate_behaviour() -> dict[str, Any] | None:
    """Return a 503 response dict 5% of the time; else sleep and return None."""
    if RNG.random() < 0.05:
        return _response(503, {"error": "service_temporarily_unavailable", "retry_after": 30})
    # 10% tail latency of 2s; otherwise ~50ms
    sleep_ms = 2000 if RNG.random() < 0.10 else RNG.randint(30, 80)
    time.sleep(sleep_ms / 1000)
    return None


def _response(status: int, body: dict) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _handle_seismic_content(query: str, limit: int) -> dict[str, Any]:
    scored = sorted(
        ((c, _keyword_score(c["title"] + " " + c["excerpt"], query)) for c in _SEISMIC_CARDS),
        key=lambda x: -x[1],
    )
    results = [
        {
            "card_id": c["card_id"],
            "title": c["title"],
            "excerpt": c["excerpt"],
            "updated_at": c["updated_at"],
            "score": round(s, 3),
        }
        for c, s in scored[:limit]
        if s > 0
    ]
    return _response(200, {"source": "seismic", "results": results, "query": query})


def _handle_gong_calls(query: str, limit: int) -> dict[str, Any]:
    scored = sorted(
        ((c, _keyword_score(c["title"] + " " + c["excerpt"], query)) for c in _GONG_CALLS),
        key=lambda x: -x[1],
    )
    results = [
        {
            "call_id": c["call_id"],
            "title": c["title"],
            "date": c["date"],
            "excerpt": c["excerpt"],
            "score": round(s, 3),
        }
        for c, s in scored[:limit]
        if s > 0
    ]
    return _response(200, {"source": "gong", "results": results, "query": query})


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not headers.get("authorization"):
        return _response(401, {"error": "missing_authorization_header"})

    bad = _simulate_behaviour()
    if bad:
        return bad

    path: str = event.get("rawPath", "")
    qs: dict[str, str] = event.get("queryStringParameters") or {}
    query = qs.get("query", "")
    limit = int(qs.get("limit", "5"))

    if path.endswith("/seismic/content"):
        return _handle_seismic_content(query, limit)
    if path.endswith("/gong/calls"):
        return _handle_gong_calls(query, limit)

    return _response(404, {"error": "not_found", "path": path})
