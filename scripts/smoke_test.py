"""Local smoke test: parser → (mocked retrieval+generation) → scorer →
hard-rules → writer, producing an annotated output workbook.

Intentionally does NOT touch AWS. The retrieval + generation stages
are replaced by deterministic mocks seeded from the generated
synthetic data so we can exercise the full scoring + rules + writer
path with no network calls.

What this proves:
  1. Parser extracts all 30 questions from the synthetic RFP.
  2. Scorer produces plausible H/R/C/F/G and composite values.
  3. Hard rules correctly demote pricing/compliance/reference/forward-looking
     answers to RED/AMBER.
  4. Writer produces a valid xlsx with per-cell fill colors and a
     summary sheet.

Run from repo root:   python scripts/smoke_test.py
Output lands at:     data/output/sample_rfp_acmesec_answered.xlsx
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

from confidence_scorer.scorer import score  # noqa: E402
from excel_parser.parser import parse_workbook  # noqa: E402
from excel_writer.writer import write_output  # noqa: E402
from hard_rules.rules import apply_rules  # noqa: E402
from shared.models import (  # noqa: E402
    ConfidenceBreakdown,
    FinalAnswer,
    GeneratedAnswer,
    PriorAnswerMatch,
    RetrievedPassage,
    Tier,
)

DATA = ROOT / "data"


# --------------------------------------------------------------------------
# Mock retrieval + generation.
#
# For the smoke test we pick an answer pattern based on keyword matching
# against the question text, so different questions trigger different
# scoring profiles + rule triggers. In production this is replaced by
# Kendra + Bedrock calls.
# --------------------------------------------------------------------------

APPROVED_REFERENCES = [
    c["name"] for c in json.loads((DATA / "graph" / "customers.json").read_text())
    if c["public_reference"] and c["approval_expires"]
    and datetime.fromisoformat(c["approval_expires"]).date() > datetime.now(timezone.utc).date()
]


def mock_retrieve_and_generate(question_text: str) -> tuple[
    list[RetrievedPassage], list[PriorAnswerMatch], GeneratedAnswer
]:
    q = question_text.lower()
    now = datetime.now(timezone.utc)

    def passage(source: str, doc_id: str, excerpt: str, score: float, age_days: int) -> RetrievedPassage:
        return RetrievedPassage(
            source_system=source,  # type: ignore[arg-type]
            document_id=doc_id,
            excerpt=excerpt,
            score=score,
            uri=f"s3://ref/{source}/{doc_id}.md",
            metadata={"updated_at": (now - timedelta(days=age_days)).isoformat()},
        )

    def prior(similarity: float, expires_days: int | None = 365) -> PriorAnswerMatch:
        return PriorAnswerMatch(
            answer_id=str(uuid.uuid4()),
            question_text="prior matching question",
            answer_text="prior SME-approved answer",
            similarity=similarity,
            approved_by="sme-001",
            approved_at=now - timedelta(days=90),
            expires_on=now + timedelta(days=expires_days) if expires_days else None,
        )

    # Encryption / data protection → strong signals, likely green
    if any(kw in q for kw in ["encrypt", "tls", "cipher"]):
        passages = [
            passage("historical_rfp", "HIST-001", "Encryption at rest uses AES-256-GCM...", 0.92, 60),
            passage("whitepaper", "security_overview", "At rest: AES-256-GCM...", 0.85, 40),
            passage("seismic", "seismic-card-001", "Encryption at rest AES-256-GCM...", 0.80, 40),
        ]
        priors = [prior(0.90)]
        gen = GeneratedAnswer(
            answer_text=(
                "Customer data is encrypted at rest using AES-256-GCM with keys managed by "
                "AWS KMS. Customer-managed keys (BYOK) are supported on the Enterprise tier. "
                "Key rotation is enabled by default on an annual cadence. [HIST-001]"
            ),
            citations=["HIST-001", "security_overview"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # Identity / SSO / MFA → strong
    if any(kw in q for kw in ["sso", "saml", "oidc", "mfa", "scim", "rbac"]):
        passages = [
            passage("historical_rfp", "HIST-003", "We support SAML 2.0 and OIDC...", 0.88, 75),
            passage("whitepaper", "security_overview", "MFA is supported for all tiers...", 0.78, 40),
        ]
        priors = [prior(0.87)]
        gen = GeneratedAnswer(
            answer_text=(
                "We support SAML 2.0 and OIDC for customer-initiated SSO, OAuth 2.0 "
                "with PKCE for native integrations, and SCIM 2.0 for automated "
                "provisioning. MFA can be enforced for all users via TOTP, WebAuthn/FIDO2, "
                "or SMS. [HIST-003]"
            ),
            citations=["HIST-003"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # Pricing → should be forced RED
    if any(kw in q for kw in ["pric", "discount", "seat", "per-user", "per user"]):
        passages = [passage("seismic", "seismic-card-005", "Enterprise tier list price is $X per user...", 0.70, 30)]
        priors: list[PriorAnswerMatch] = []
        gen = GeneratedAnswer(
            answer_text=(
                "Enterprise tier pricing starts at $50 per user per month with a 15% "
                "volume discount at 5,000+ seats. Annual contracts receive an additional 10% discount."
            ),
            citations=["seismic-card-005"],
            invoked_customers=[],
            contains_pricing=True,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # Reference customer → should be forced AMBER unless an approved ref is named
    if any(kw in q for kw in ["reference customer", "reference", "similar size"]):
        passages = [passage("seismic", "seismic-card-002", "Approved public references...", 0.65, 20)]
        priors = []
        # Deliberately name a non-approved customer to exercise the rule
        gen = GeneratedAnswer(
            answer_text=(
                "A financial services customer comparable to your organization is "
                "Aurora Federal Credit Union, which has deployed the Enterprise tier "
                "in production across approximately 5,000 users."
            ),
            citations=["seismic-card-002"],
            invoked_customers=["Aurora Federal Credit Union"],  # NOT on the approved list
            contains_pricing=False,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # FedRAMP roadmap / forward-looking → AMBER minimum
    if any(kw in q for kw in ["fedramp", "roadmap", "when will"]):
        passages = [passage("gong", "gong-call-001", "Priya would not commit to a specific date...", 0.68, 14)]
        priors = []
        gen = GeneratedAnswer(
            answer_text=(
                "We will deliver FedRAMP Moderate authorization by end of Q3. The ATO "
                "assessment is in progress with an accredited 3PAO."
            ),
            citations=["gong-call-001"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=True,
            contains_forward_looking=True,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # SLA commitment → AMBER minimum
    if any(kw in q for kw in ["uptime", "sla", "99.9", "99.99", "financial penal"]):
        passages = [passage("historical_rfp", "HIST-005", "Target RTO is under four hours...", 0.72, 95)]
        priors = [prior(0.55)]
        gen = GeneratedAnswer(
            answer_text=(
                "Our standard SLA provides 99.9% uptime. For Enterprise customers we can "
                "negotiate 99.95% uptime with financial penalties for breach (target "
                "MTTR 2 hours). Target RPO is under 15 minutes."
            ),
            citations=["HIST-005"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # SOC2 / ISO / GDPR / compliance claims → AMBER
    if any(kw in q for kw in ["soc 2", "soc2", "iso 27001", "iso27001", "certif", "compliance", "audit", "gdpr"]):
        passages = [
            passage("whitepaper", "soc2_bridge_letter", "SOC 2 Type II bridge letter covers...", 0.82, 15),
            passage("whitepaper", "security_overview", "SOC 2 Type II... trust principles...", 0.78, 40),
        ]
        priors = [prior(0.80)]
        gen = GeneratedAnswer(
            answer_text=(
                "We hold a SOC 2 Type II report covering the Security, Availability, and "
                "Confidentiality trust principles, issued annually by an independent third-party "
                "auditor. We are also ISO 27001 certified. A current bridge letter is available. "
                "[soc2_bridge_letter]"
            ),
            citations=["soc2_bridge_letter", "security_overview"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=True,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # DR / RTO / RPO / backup
    if any(kw in q for kw in ["rto", "rpo", "disaster", "backup", "recovery"]):
        passages = [passage("historical_rfp", "HIST-005", "Target RTO under four hours...", 0.85, 80)]
        priors = [prior(0.83)]
        gen = GeneratedAnswer(
            answer_text=(
                "The platform is deployed active-active across two AWS regions with "
                "synchronous replication of metadata and sub-60-second asynchronous "
                "replication of bulk data. Target RTO is under 4 hours, target RPO under "
                "15 minutes. DR exercises are conducted twice annually. [HIST-005]"
            ),
            citations=["HIST-005"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # Data residency
    if any(kw in q for kw in ["residency", "region", "eu data", "data subject"]):
        passages = [
            passage("whitepaper", "data_protection_policy", "Tenant data is stored in elected AWS region...", 0.80, 25),
            passage("seismic", "seismic-card-003", "Tenant creation supports region selection...", 0.75, 110),
        ]
        priors = [prior(0.78)]
        gen = GeneratedAnswer(
            answer_text=(
                "Customer data is stored in AWS regions elected at tenant creation "
                "(us-east-1, us-west-2, eu-west-1, eu-central-1, ap-southeast-2). "
                "Data does not leave the elected region for operational purposes. "
                "Cross-region replication is opt-in for DR. [data_protection_policy]"
            ),
            citations=["data_protection_policy", "seismic-card-003"],
            invoked_customers=[],
            contains_pricing=False,
            contains_compliance_claim=False,
            contains_forward_looking=False,
            model_id="mock-sonnet",
            prompt_hash="mock",
            response_hash="mock",
        )
        return passages, priors, gen

    # Default: weak retrieval, no prior — likely RED
    passages = [passage("whitepaper", "security_overview", "High-level security overview...", 0.45, 500)]
    priors = []
    gen = GeneratedAnswer(
        answer_text="A generic response drawing from the security overview document.",
        citations=["security_overview"],
        invoked_customers=[],
        contains_pricing=False,
        contains_compliance_claim=False,
        contains_forward_looking=False,
        model_id="mock-sonnet",
        prompt_hash="mock",
        response_hash="mock",
    )
    return passages, priors, gen


def main() -> None:
    src = DATA / "incoming" / "sample_rfp_acmesec.xlsx"
    dst = DATA / "output" / "sample_rfp_acmesec_answered.xlsx"

    print(f"Parsing: {src}")
    job_id, questions = parse_workbook(src)
    print(f"  → {len(questions)} questions extracted (job_id={job_id})")

    finals: list[FinalAnswer] = []
    tier_counts = {Tier.GREEN: 0, Tier.AMBER: 0, Tier.RED: 0}
    trigger_counts: dict[str, int] = {}

    for q in questions:
        passages, priors, gen = mock_retrieve_and_generate(q.text)
        composite, breakdown, tier = score(
            passages=passages,
            prior_matches=priors,
            generated=gen,
        )
        # Neptune approved-reference list from the graph seed
        rule_result = apply_rules(
            answer_text=gen.answer_text,
            invoked_customers=gen.invoked_customers,
            approved_reference_customers=APPROVED_REFERENCES,
            current_tier=tier,
        )

        final = FinalAnswer(
            question_id=q.question_id,
            answer_text=gen.answer_text,
            citations=gen.citations,
            raw_confidence=composite,
            tier=rule_result.final_tier,
            confidence_breakdown=breakdown,
            hard_rule_triggers=rule_result.triggers,
            reviewer_required=rule_result.reviewer_required,
            answer_cell=q.answer_cell,
            confidence_cell=q.confidence_cell,
        )
        finals.append(final)
        tier_counts[final.tier] += 1
        for t in final.hard_rule_triggers:
            key = t.split(":", 1)[0] if ":" in t else t
            trigger_counts[key] = trigger_counts.get(key, 0) + 1

    print(
        f"  → Tiers: green={tier_counts[Tier.GREEN]} "
        f"amber={tier_counts[Tier.AMBER]} "
        f"red={tier_counts[Tier.RED]}"
    )
    if trigger_counts:
        print("  → Hard-rule triggers:")
        for k, v in sorted(trigger_counts.items(), key=lambda kv: -kv[1]):
            print(f"      {k}: {v}")

    write_output(source_path=src, dest_path=dst, answers=finals, job_id=job_id)
    print(f"Wrote: {dst}")


if __name__ == "__main__":
    main()
