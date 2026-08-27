"""Synthetic data generator for the RFP Redlining Copilot demo.

Produces all artifacts under rfp-copilot/data/:
- incoming/sample_rfp_acmesec.xlsx  (a realistic-shaped security questionnaire)
- historical/won_rfp_*.xlsx         (prior SME-approved answers — the reference corpus)
- seismic/content_cards.json        (Seismic content export mock)
- gong/transcripts.json             (Gong call transcript mock)
- whitepapers/*.md                  (policies, overviews, bridge letters)
- graph/customers.json              (customer reference approval status)
- graph/products.json               (product / topic / competency graph seed)
- corpus/compliance/*.json          (6 primary source sidecars — compliance_store)
- corpus/product-docs/*.json        (10 primary source sidecars — product_docs)
- corpus/prior-rfps/*.json          (6 completed questionnaire extracts, attributed)
- corpus/sme-approved/*.json        (2 hand-written Q&A pairs — the H signal)

Fully fictitious. No real customer names.

Run:   python scripts/generate_synthetic_data.py
Reset: remove data/ artifacts; script is idempotent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

RNG = Random(42)  # deterministic demo
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = datetime.now(timezone.utc).date().isoformat()

# --------------------------------------------------------------------------
# Incoming RFP: a security questionnaire with ~30 questions of varied shape
# --------------------------------------------------------------------------

INCOMING_RFP_QUESTIONS: list[dict[str, str]] = [
    # Section A: Corporate & compliance
    {"section": "A. Corporate & Compliance", "id": "SEC-001", "q": "What compliance certifications does your organization currently hold? Please list certification name, scope, issuing body, and most recent audit date."},
    {"section": "A. Corporate & Compliance", "id": "SEC-002", "q": "Describe your SOC 2 Type II audit scope and the trust principles covered."},
    {"section": "A. Corporate & Compliance", "id": "SEC-003", "q": "Are you ISO 27001 certified? If yes, provide certificate number and expiration."},
    {"section": "A. Corporate & Compliance", "id": "SEC-004", "q": "Provide a reference customer of similar size in our industry (financial services, 5,000+ employees) who has deployed your platform in production."},
    {"section": "A. Corporate & Compliance", "id": "SEC-005", "q": "Describe your approach to GDPR compliance for EU data subjects."},

    # Section B: Data protection
    {"section": "B. Data Protection", "id": "SEC-006", "q": "How is customer data encrypted at rest? Specify algorithm, key length, and key management approach."},
    {"section": "B. Data Protection", "id": "SEC-007", "q": "How is customer data encrypted in transit? Specify protocol versions and cipher suites supported."},
    {"section": "B. Data Protection", "id": "SEC-008", "q": "Do you support customer-managed encryption keys (BYOK / HYOK)? If yes, which key-management services are supported?"},
    {"section": "B. Data Protection", "id": "SEC-009", "q": "Describe your approach to data classification and handling of sensitive data."},
    {"section": "B. Data Protection", "id": "SEC-010", "q": "What is your data retention and deletion policy? Include timelines for customer-initiated deletion."},

    # Section C: Identity & access
    {"section": "C. Identity & Access", "id": "SEC-011", "q": "Which SSO protocols do you support? (SAML 2.0, OIDC, OAuth 2.0, etc.)"},
    {"section": "C. Identity & Access", "id": "SEC-012", "q": "Do you support multi-factor authentication for all users? Which factors are supported?"},
    {"section": "C. Identity & Access", "id": "SEC-013", "q": "Describe your approach to role-based access control and least-privilege enforcement."},
    {"section": "C. Identity & Access", "id": "SEC-014", "q": "Do you support SCIM for automated user provisioning and deprovisioning?"},

    # Section D: Availability & resilience
    {"section": "D. Availability & Resilience", "id": "SEC-015", "q": "What is your SLA commitment for platform uptime? Provide monthly and annual figures."},
    {"section": "D. Availability & Resilience", "id": "SEC-016", "q": "Describe your disaster recovery capabilities including RTO and RPO targets."},
    {"section": "D. Availability & Resilience", "id": "SEC-017", "q": "How are backups performed, stored, and tested?"},

    # Section E: Operations & incident response
    {"section": "E. Operations & Incident Response", "id": "SEC-018", "q": "Describe your incident response process, including notification timelines to affected customers."},
    {"section": "E. Operations & Incident Response", "id": "SEC-019", "q": "Do you conduct penetration testing? How often, by whom, and are reports available to customers?"},
    {"section": "E. Operations & Incident Response", "id": "SEC-020", "q": "Describe your vulnerability management program, including SLAs for critical patches."},
    {"section": "E. Operations & Incident Response", "id": "SEC-021", "q": "How do you detect and respond to insider threats?"},

    # Section F: Application security
    {"section": "F. Application Security", "id": "SEC-022", "q": "Describe your secure software development lifecycle (SSDLC) practices."},
    {"section": "F. Application Security", "id": "SEC-023", "q": "Do you perform static and dynamic code analysis? Which tools are in use?"},
    {"section": "F. Application Security", "id": "SEC-024", "q": "How do you manage dependencies and third-party library vulnerabilities?"},

    # Section G: Privacy & legal
    {"section": "G. Privacy & Legal", "id": "SEC-025", "q": "Provide your standard Data Processing Agreement (DPA) and list sub-processors."},
    {"section": "G. Privacy & Legal", "id": "SEC-026", "q": "In which regions is customer data physically stored? Is data residency configurable by customer?"},
    {"section": "G. Privacy & Legal", "id": "SEC-027", "q": "Describe your process for responding to customer data subject access requests (DSARs)."},

    # Section H: Commercial
    {"section": "H. Commercial", "id": "SEC-028", "q": "Provide standard per-user pricing for your Enterprise tier, including any volume discounts available at 5,000+ seats."},
    {"section": "H. Commercial", "id": "SEC-029", "q": "What is your roadmap for FedRAMP Moderate authorization, and when will it be available?"},
    {"section": "H. Commercial", "id": "SEC-030", "q": "Are you willing to commit to a 99.99% uptime SLA with financial penalties for breach?"},
]


def _make_rfp_workbook(
    dest: Path,
    *,
    title: str,
    prospect: str,
    questions: list[dict[str, str]],
    with_answers: bool = False,
    win_loss: str | None = None,
) -> None:
    wb = Workbook()
    ws = wb.active
    if ws is None:
        return
    ws.title = "Questionnaire"

    # Header block (mixed formatting — the parser must ignore these)
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = f"Prospect: {prospect}"
    ws["A3"] = f"Sent: {datetime.now(timezone.utc).date().isoformat()}"
    ws["A4"] = "Please respond to each question in the Answer column. Additional context may be provided in the Notes column."
    ws["A4"].alignment = Alignment(wrap_text=True)
    ws.merge_cells("A4:D4")
    ws.row_dimensions[4].height = 30

    # Column headers
    ws["A6"] = "ID"
    ws["B6"] = "Question"
    ws["C6"] = "Answer"
    ws["D6"] = "Confidence"
    for col in "ABCD":
        ws[f"{col}6"].font = Font(bold=True)
        ws[f"{col}6"].fill = PatternFill(start_color="FFD9D9D9", end_color="FFD9D9D9", fill_type="solid")

    # Column widths
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 70
    ws.column_dimensions["C"].width = 70
    ws.column_dimensions["D"].width = 12

    row = 7
    current_section: str | None = None
    for q in questions:
        if q["section"] != current_section:
            current_section = q["section"]
            ws.cell(row=row, column=1, value=current_section).font = Font(bold=True, italic=True)
            ws.cell(row=row, column=1).fill = PatternFill(start_color="FFF2F2F2", end_color="FFF2F2F2", fill_type="solid")
            ws.merge_cells(start_row=row, end_row=row, start_column=1, end_column=4)
            row += 1

        ws.cell(row=row, column=1, value=q["id"])
        cell_b = ws.cell(row=row, column=2, value=q["q"])
        cell_b.alignment = Alignment(wrap_text=True, vertical="top")
        if with_answers and "a" in q:
            cell_c = ws.cell(row=row, column=3, value=q["a"])
            cell_c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 48
        row += 1

    if with_answers and win_loss:
        # Meta row for historical RFPs to help Kendra metadata tagging
        meta_row = row + 2
        ws.cell(row=meta_row, column=1, value="WIN/LOSS").font = Font(bold=True)
        ws.cell(row=meta_row, column=2, value=win_loss)

    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(dest))


# --------------------------------------------------------------------------
# Historical RFPs (reference corpus) — condensed, with SME-approved answers
# --------------------------------------------------------------------------

HISTORICAL_Q_AND_A: list[dict[str, str]] = [
    {
        "section": "Data Protection",
        "id": "HIST-001",
        "q": "How is customer data encrypted at rest?",
        "a": "All customer data is encrypted at rest using AES-256-GCM with keys managed by AWS KMS. Customer-managed keys (BYOK) are supported for Enterprise customers via AWS KMS CMKs in the customer's account, referenced by key-policy. Key rotation is enabled by default on an annual cadence.",
    },
    {
        "section": "Data Protection",
        "id": "HIST-002",
        "q": "How is data encrypted in transit?",
        "a": "All data in transit is encrypted with TLS 1.2 or TLS 1.3. We require Perfect Forward Secrecy (ECDHE cipher suites), disable SSLv3 and TLS 1.0/1.1 at the load balancer, and publish HSTS headers with a one-year max-age.",
    },
    {
        "section": "Identity & Access",
        "id": "HIST-003",
        "q": "Which SSO protocols are supported?",
        "a": "We support SAML 2.0 and OIDC as identity providers for customer-initiated SSO. OAuth 2.0 Authorization Code flow with PKCE is used for our native integrations. SCIM 2.0 is supported for automated user provisioning.",
    },
    {
        "section": "Identity & Access",
        "id": "HIST-004",
        "q": "Is MFA supported? Which factors?",
        "a": "MFA is supported for all user tiers and can be enforced by administrator policy. Supported factors: TOTP authenticator apps, WebAuthn/FIDO2 security keys, and SMS (not recommended for high-assurance contexts). Hardware keys are required for administrative roles in our Enterprise tier.",
    },
    {
        "section": "Availability",
        "id": "HIST-005",
        "q": "Describe DR capabilities and RTO/RPO targets.",
        "a": "Our platform is deployed active-active across two AWS regions with synchronous replication of critical metadata and asynchronous replication (< 60 seconds) for bulk customer data. Target RTO is under four hours and target RPO is under fifteen minutes. DR exercises are conducted twice annually.",
    },
    {
        "section": "Operations",
        "id": "HIST-006",
        "q": "Describe your incident response process.",
        "a": "Security incidents are triaged by our 24x7 Security Operations Center within 15 minutes of detection. A named Incident Commander coordinates response following the NIST 800-61 framework. Customers affected by a confirmed security incident are notified within 72 hours, with an initial advisory containing scope, impact, and remediation status.",
    },
    {
        "section": "Privacy",
        "id": "HIST-007",
        "q": "Provide your DPA and list of sub-processors.",
        "a": "Our standard Data Processing Agreement is available on our Trust Center and incorporates the EU Standard Contractual Clauses. Our sub-processor list is maintained publicly and updated at least 30 days before any change. Sub-processors are audited annually against our third-party risk program.",
    },
    {
        "section": "Privacy",
        "id": "HIST-008",
        "q": "Where is customer data stored? Is residency configurable?",
        "a": "Customer data is stored in AWS regions elected at tenant creation. Supported regions: us-east-1, us-west-2, eu-west-1, eu-central-1, ap-southeast-2. Data does not leave the elected region for operational purposes. Cross-region replication is available as an opt-in feature for disaster recovery.",
    },
    {
        "section": "Application Security",
        "id": "HIST-009",
        "q": "Describe your SSDLC practices.",
        "a": "We follow a security development lifecycle aligned with OWASP SAMM. All code changes require peer review and pass automated SAST (Semgrep), SCA (Snyk), and container image scanning before merge. Threat modeling is performed for every new service using STRIDE. Annual penetration testing is conducted by an independent third party; executive summaries are available under NDA.",
    },
    {
        "section": "Application Security",
        "id": "HIST-010",
        "q": "How do you manage third-party library vulnerabilities?",
        "a": "We maintain a software bill of materials (SBOM) for every deployable artifact. Vulnerabilities are triaged daily with SLAs of 24 hours for critical, 7 days for high, and 30 days for medium. Patch status is visible in our internal risk register and summarized quarterly to customers under NDA.",
    },
]

WON_RFPS: list[tuple[str, str, list[dict[str, str]], str]] = [
    (
        "won_rfp_acme_financial_2025",
        "Acme Financial (fictional prospect, closed won 2025-Q3)",
        HISTORICAL_Q_AND_A[:6],
        "WON",
    ),
    (
        "won_rfp_bluebird_insurance_2026",
        "Bluebird Insurance (fictional prospect, closed won 2026-Q1)",
        HISTORICAL_Q_AND_A[3:],
        "WON",
    ),
    (
        "lost_rfp_globex_2025",
        "Globex (fictional prospect, closed lost 2025-Q4)",
        HISTORICAL_Q_AND_A[:5],
        "LOST",
    ),
]

# --------------------------------------------------------------------------
# Seismic + Gong mocks
# --------------------------------------------------------------------------

SEISMIC_CARDS: list[dict[str, str]] = [
    {
        "card_id": "seismic-card-001",
        "title": "Enterprise Security Overview One-Pager",
        "body": "High-level customer-facing overview: SOC 2 Type II (Trust Principles: Security, Availability, Confidentiality), ISO 27001, active FedRAMP Moderate authorization in progress. Encryption at rest AES-256-GCM, in transit TLS 1.2+. MFA mandatory for admin roles.",
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat(),
        "owner": "Product Marketing",
    },
    {
        "card_id": "seismic-card-002",
        "title": "FAQ — Reference Customer Stories (Financial Services)",
        "body": "Approved public references in the financial services vertical include logos listed in our public customer page. Manager must consult the Reference Customer List in Neptune before naming any customer in an outbound document.",
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=20)).date().isoformat(),
        "owner": "Sales Enablement",
    },
    {
        "card_id": "seismic-card-003",
        "title": "Data Residency Configuration Matrix",
        "body": "Tenant creation supports region selection among us-east-1, us-west-2, eu-west-1, eu-central-1, ap-southeast-2. Cross-region replication is an opt-in feature for DR. Residency is enforced at the storage layer via KMS CMK scoping.",
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=110)).date().isoformat(),
        "owner": "Security Engineering",
    },
    {
        "card_id": "seismic-card-004",
        "title": "SSDLC Summary Deck",
        "body": "Secure SDLC aligned to OWASP SAMM. SAST: Semgrep. SCA: Snyk. Container scanning: Trivy + ECR scan. Threat modeling: STRIDE per service. Annual third-party pentest. SBOM generated per build.",
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat(),
        "owner": "Application Security",
    },
    {
        "card_id": "seismic-card-005",
        "title": "Pricing — Internal Only (DO NOT SHARE)",
        "body": "Enterprise tier list price is $X per user per month with standard volume tiers. All pricing must be validated by Deal Desk before appearing in customer-facing documents.",
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat(),
        "owner": "Deal Desk",
    },
]

GONG_TRANSCRIPTS: list[dict[str, object]] = [
    {
        "call_id": "gong-call-001",
        "title": "Discovery call — prospect asks about FedRAMP timeline",
        "date": (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat(),
        "participants": ["AE: Jordan Avery", "SE: Priya Menon", "Prospect: VP InfoSec"],
        "snippets": [
            "Prospect asked when FedRAMP Moderate authorization will be live. Priya responded that the ATO assessment is in progress and she would not commit to a specific date in writing.",
            "Prospect pushed for an SLA with financial penalties; Jordan redirected to standard 99.9% SLA and noted financial penalties are negotiated at contract.",
        ],
    },
    {
        "call_id": "gong-call-002",
        "title": "Technical deep-dive — encryption and key management",
        "date": (datetime.now(timezone.utc) - timedelta(days=22)).date().isoformat(),
        "participants": ["SE: Priya Menon", "Prospect: Security Architect"],
        "snippets": [
            "Discussed BYOK via AWS KMS CMK in the customer's account. Confirmed that key deletion by the customer results in data becoming unreadable within the SLA window.",
            "Confirmed that all data-in-transit uses TLS 1.2 minimum, with HSTS, and that TLS 1.0/1.1 have been deprecated end-to-end.",
        ],
    },
    {
        "call_id": "gong-call-003",
        "title": "Procurement call — DPA and sub-processors",
        "date": (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat(),
        "participants": ["AE: Jordan Avery", "Legal: Sam Okafor", "Prospect: Procurement Lead"],
        "snippets": [
            "Walked through standard DPA. Confirmed EU SCCs are incorporated. Public sub-processor list is updated with 30-day advance notice of changes.",
            "Prospect asked to add a specific customer logo to the reference list. Jordan deferred pending approval from the reference customer.",
        ],
    },
]

# --------------------------------------------------------------------------
# Whitepapers (policies / overviews)
# --------------------------------------------------------------------------

WHITEPAPERS: dict[str, str] = {
    "security_overview": """# Security Overview

Our platform is designed around a defense-in-depth architecture with
mandatory controls at the network, compute, data, identity, and
application layers.

## Compliance
- SOC 2 Type II — annual audit, trust principles Security, Availability, Confidentiality
- ISO 27001 — certified
- FedRAMP Moderate — ATO in progress (no committed date)
- GDPR — EU SCCs incorporated into standard DPA

## Encryption
- At rest: AES-256-GCM, AWS KMS managed; BYOK available on Enterprise tier
- In transit: TLS 1.2+ with Perfect Forward Secrecy, HSTS, TLS 1.0/1.1 disabled
""",
    "soc2_bridge_letter": """# SOC 2 Type II Bridge Letter (Current)

This bridge letter covers the period between our most recent SOC 2
Type II report and the next scheduled audit. No material changes to
the control environment have occurred during the bridge period.

Scope: Security, Availability, Confidentiality trust principles.
Auditor: Independent third-party firm.
Next report: scheduled for issuance in Q3.
""",
    "data_protection_policy": """# Data Protection Policy

## Data classification
All customer data is classified at ingestion. Four tiers: Public,
Internal, Confidential, Restricted. Handling requirements escalate
by tier.

## Retention
Customer data is retained for the duration of the customer agreement
plus 90 days. Customer-initiated deletion is honored within 30 days;
cryptographic erasure of backups completes within the backup retention
window of 90 days.

## Residency
Tenant data is stored in the customer's elected AWS region and does
not leave that region for operational purposes.
""",
    "incident_response_overview": """# Incident Response Overview

Detection → Triage → Containment → Eradication → Recovery → Lessons
Learned. Aligned to NIST SP 800-61. 24x7 SOC. Customer notification
within 72 hours of a confirmed security incident affecting them.
""",
}

# --------------------------------------------------------------------------
# Graph seed data
# --------------------------------------------------------------------------

# Six fictitious Echoworx reference accounts. Every name is invented; any
# resemblance to a real organisation is coincidental.
#
# The asymmetry here is load-bearing for the demo. Hard rule 3 in
# lambdas/hard_rules/rules.py demotes any answer that names a customer which
# is not in the approved list the retriever builds from this table
# (public_reference == True AND approval_expires >= today). Exactly two rows
# clear that bar, so a questionnaire question naming Northwind Mutual passes
# the gate and an otherwise identical question naming Helix Reinsurance fires
# `unapproved_reference:Helix Reinsurance`.
CUSTOMERS: list[dict[str, object]] = [
    {
        "customer_id": "cust-nwm-001",
        "name": "Northwind Mutual",
        "industry": "insurance",
        "employees": 9400,
        "contract_size_usd": 510000,
        "public_reference": True,
        "approval_expires": (datetime.now(timezone.utc) + timedelta(days=210)).date().isoformat(),
        "deployed_products": ["encryption_gateway", "enterprise_tier"],
    },
    {
        "customer_id": "cust-ctb-002",
        "name": "Cascadia Trust Bank",
        "industry": "banking",
        "employees": 15200,
        "contract_size_usd": 880000,
        "public_reference": True,
        "approval_expires": (datetime.now(timezone.utc) + timedelta(days=365)).date().isoformat(),
        "deployed_products": ["encryption_gateway", "enterprise_tier", "byok"],
    },
    {
        "customer_id": "cust-mhg-003",
        "name": "Meridian Health Group",
        "industry": "healthcare",
        # Never approved for public naming — the MSA carries a mutual
        # non-publicity clause.
        "public_reference": False,
        "employees": 6100,
        "contract_size_usd": 295000,
        "approval_expires": None,
        "deployed_products": ["encryption_gateway"],
    },
    {
        "customer_id": "cust-prd-004",
        "name": "Province of Rideau - Digital Services",
        "industry": "government",
        "employees": 28000,
        "contract_size_usd": 1450000,
        # Public-sector procurement rules bar vendor endorsement.
        "public_reference": False,
        "approval_expires": None,
        "deployed_products": ["encryption_gateway", "enterprise_tier", "data_residency_ca"],
    },
    {
        "customer_id": "cust-acp-005",
        "name": "Anvil Capital Partners",
        "industry": "financial_services",
        "employees": 2300,
        "contract_size_usd": 210000,
        "public_reference": False,
        "approval_expires": None,
        "deployed_products": ["encryption_gateway"],
    },
    {
        "customer_id": "cust-hxr-006",
        "name": "Helix Reinsurance",
        "industry": "insurance",
        "employees": 7700,
        "contract_size_usd": 640000,
        # Approval LAPSED rather than never granted. Stored as False so the
        # retriever's boolean filter excludes it either way; the stale date is
        # kept so a reviewer can see *why* it is excluded.
        "public_reference": False,
        "approval_expires": (datetime.now(timezone.utc) - timedelta(days=95)).date().isoformat(),
        "deployed_products": ["encryption_gateway", "enterprise_tier"],
    },
]

PRODUCTS_AND_TOPICS: dict[str, object] = {
    "products": [
        {"id": "platform", "name": "Core Platform", "tiers": ["team", "business", "enterprise"]},
        {"id": "enterprise_tier", "name": "Enterprise Tier", "features": ["BYOK", "audit_export", "priority_sla"]},
    ],
    "topics": [
        {"id": "encryption_at_rest", "label": "Encryption at rest"},
        {"id": "encryption_in_transit", "label": "Encryption in transit"},
        {"id": "key_management", "label": "Key management / BYOK"},
        {"id": "sso", "label": "Single sign-on"},
        {"id": "mfa", "label": "Multi-factor authentication"},
        {"id": "scim", "label": "SCIM provisioning"},
        {"id": "dr_bcp", "label": "Disaster recovery / BCP"},
        {"id": "incident_response", "label": "Incident response"},
        {"id": "ssdlc", "label": "Secure SDLC"},
        {"id": "pentest", "label": "Penetration testing"},
        {"id": "sbom", "label": "Software bill of materials"},
        {"id": "dpa", "label": "Data processing agreement"},
        {"id": "data_residency", "label": "Data residency"},
        {"id": "soc2", "label": "SOC 2"},
        {"id": "iso27001", "label": "ISO 27001"},
        {"id": "fedramp", "label": "FedRAMP"},
        {"id": "gdpr", "label": "GDPR"},
    ],
    "smes": [
        {"id": "sme-001", "name": "Priya Menon", "domains": ["encryption_at_rest", "encryption_in_transit", "key_management"]},
        {"id": "sme-002", "name": "Jordan Avery", "domains": ["dpa", "data_residency", "gdpr"]},
        {"id": "sme-003", "name": "Sam Okafor", "domains": ["incident_response", "pentest", "ssdlc"]},
        {"id": "sme-004", "name": "Alex Rivera", "domains": ["soc2", "iso27001", "fedramp"]},
    ],
}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _write_markdown(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# --------------------------------------------------------------------------
# Reference corpus JSON sidecars — expanded into real documents by
# scripts/generate_corpus_documents.py and uploaded by scripts/seed_s3_corpus.py.
#
#   compliance/    6 docs  source_type=compliance_cert   (updated_at)
#   product-docs/ 10 docs  source_type=product_doc       (updated_at)
#   prior-rfps/    6 docs  source_type=prior_rfp         (answered_at + attribution)
#   sme-approved/  2 docs  source_type=sme_approved_answer (approved_at + expires_on)
#
# Every organisation named here is invented.
#
# Freshness: `soc2_type2_2024_expired` is deliberately dated ~26 months back
# with an expires_on already in the past. scorer.score_f() buckets anything
# older than 730 days at 0.2 (the floor), Provenance.is_expired flips True, and
# render_citation() suffixes the source line with "(EXPIRED)". It sits directly
# against `soc2_type2_2026`, which is inside 90 days and therefore scores 1.0.
# --------------------------------------------------------------------------

_AUD_CERT = "certification"
_AUD_INTERNAL = "internal"
_AUD_CUSTOMER = "customer"

CORPUS_PRIMARY: list[dict] = [
    # ---- compliance/ (6) ---------------------------------------------------
    {
        "document_id": "soc2_type2_2026",
        "title": "SOC 2 Type II Report Summary — FY2026",
        "excerpt": (
            "SOC 2 Type II report covering Security, Availability and Confidentiality. "
            "Report period 2025-04-01 to 2026-03-31, issued 2026-06-15 by an independent "
            "CPA firm. No exceptions noted. Covers the email encryption gateway, the "
            "recipient portal and the tenant administration plane."
        ),
        "updated_at": "2026-06-15",
        "expires_on": "2027-06-15",
        "audience": _AUD_CERT,
        "topic_ids": ["soc2", "incident_response"],
        "prefix": "compliance",
    },
    {
        "document_id": "iso27001_soa_extract_2026",
        "title": "ISO/IEC 27001:2022 Statement of Applicability — Extract",
        "excerpt": (
            "Extract from Statement of Applicability v6.1 against ISO/IEC 27001:2022 "
            "Annex A. Certificate ISMS-2026-04417, valid 2026-02-10 to 2029-02-09. "
            "Includes applicability decisions and justifications for the 93 Annex A controls."
        ),
        "updated_at": "2026-02-10",
        "expires_on": "2029-02-09",
        "audience": _AUD_CERT,
        "topic_ids": ["iso27001"],
        "prefix": "compliance",
    },
    {
        "document_id": "iso27018_attestation_2026",
        "title": "ISO/IEC 27018:2019 Attestation — Public Cloud PII Processing",
        "excerpt": (
            "Attestation of conformance to ISO/IEC 27018:2019 for the protection of "
            "personally identifiable information processed in public cloud. Assessed "
            "alongside the ISO/IEC 27001 surveillance audit, February 2026."
        ),
        "updated_at": "2026-02-10",
        "expires_on": "2029-02-09",
        "audience": _AUD_CERT,
        "topic_ids": ["iso27001", "gdpr", "data_residency"],
        "prefix": "compliance",
    },
    {
        "document_id": "pentest_exec_summary_2026",
        "title": "Independent Penetration Test — Executive Summary (Apr 2026)",
        "excerpt": (
            "Executive summary of the annual grey-box penetration test of the encryption "
            "gateway, recipient portal and administration API, conducted 2026-04-06 to "
            "2026-04-24 by an independent testing firm. Two medium and four low findings; "
            "all remediated and retested by 2026-04-28."
        ),
        "updated_at": "2026-04-28",
        "expires_on": "2027-04-28",
        "audience": _AUD_CERT,
        "topic_ids": ["pentest", "sbom"],
        "prefix": "compliance",
    },
    {
        "document_id": "soc2_type2_2024_expired",
        "title": "SOC 2 Type II Report Summary — FY2024 (SUPERSEDED)",
        "excerpt": (
            "SOC 2 Type II report covering Security and Availability for the period "
            "2023-04-01 to 2024-03-31, issued 2024-06-30. Superseded by the FY2026 "
            "report. Retained for audit history only; must not be cited as current "
            "evidence of the control environment."
        ),
        "updated_at": "2024-06-30",
        "expires_on": "2025-06-30",  # already lapsed — drives the suppression demo
        "audience": _AUD_CERT,
        "topic_ids": ["soc2"],
        "prefix": "compliance",
    },
    {
        "document_id": "dpa_art28_subprocessors_2026",
        "title": "Data Processing Agreement (GDPR Art. 28) and Sub-processor Register",
        "excerpt": (
            "Standard Data Processing Agreement incorporating the 2021 EU Standard "
            "Contractual Clauses and the UK International Data Transfer Addendum, plus "
            "the current sub-processor register with processing purpose and location "
            "for each entry. Register version 2026.05, published 2026-05-20."
        ),
        "updated_at": "2026-05-20",
        "audience": _AUD_CERT,
        "topic_ids": ["dpa", "gdpr"],
        "prefix": "compliance",
    },
    # ---- product-docs/ (10) ------------------------------------------------
    {
        "document_id": "encryption_at_rest_aes256gcm",
        "title": "Encryption at Rest — AES-256-GCM Envelope Design",
        "excerpt": (
            "Message bodies, attachments and metadata at rest are sealed with "
            "AES-256-GCM under per-message data keys. Data keys are wrapped by a "
            "tenant key held in a managed key service and are never written to disk "
            "in the clear."
        ),
        "updated_at": "2026-05-04",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["encryption_at_rest"],
        "prefix": "product-docs",
    },
    {
        "document_id": "encryption_in_transit_tls",
        "title": "Encryption in Transit — TLS Profile and SMTP Delivery",
        "excerpt": (
            "All external connections negotiate TLS 1.2 or TLS 1.3 with forward-secret "
            "suites only. TLS 1.0 and 1.1 are refused at every listener. Opportunistic "
            "SMTP TLS can be raised to enforced TLS with MTA-STS and DANE per partner domain."
        ),
        "updated_at": "2026-05-04",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["encryption_in_transit"],
        "prefix": "product-docs",
    },
    {
        "document_id": "key_management_byok_hsm",
        "title": "Key Management — Hierarchy, Rotation, BYOK and HSM Options",
        "excerpt": (
            "Three-level key hierarchy: per-message data keys, per-tenant key-encryption "
            "keys, and a root key. Tenants may supply a customer-managed key (BYOK) or "
            "hold root key material in a dedicated HSM partition. Default rotation is "
            "every 365 days with on-demand rotation available to tenant administrators."
        ),
        "updated_at": "2026-06-02",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["key_management"],
        "prefix": "product-docs",
    },
    {
        "document_id": "sso_saml_oidc",
        "title": "Single Sign-On — SAML 2.0 and OpenID Connect",
        "excerpt": (
            "Administrative and end-user sign-in federate through SAML 2.0 or OpenID "
            "Connect against the tenant's identity provider. Signed assertions are "
            "mandatory; encrypted assertions are optional. Group claims drive role "
            "assignment inside the tenant."
        ),
        "updated_at": "2026-04-14",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["sso"],
        "prefix": "product-docs",
    },
    {
        "document_id": "mfa_factors_policy",
        "title": "Multi-Factor Authentication — Supported Factors and Policy",
        "excerpt": (
            "Supported second factors are WebAuthn/FIDO2 roaming and platform "
            "authenticators, TOTP per RFC 6238, and push approval. Tenant policy can "
            "require a second factor for every sign-in, for administrative roles only, "
            "or for step-up on sensitive operations."
        ),
        "updated_at": "2026-04-14",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["mfa"],
        "prefix": "product-docs",
    },
    {
        "document_id": "scim_provisioning",
        "title": "SCIM 2.0 User and Group Provisioning",
        "excerpt": (
            "A per-tenant SCIM 2.0 endpoint supports create, update, deactivate and "
            "reactivate for users and create, update and delete for groups. "
            "Deactivation takes effect immediately: live sessions end and issued tokens "
            "stop working."
        ),
        "updated_at": "2026-04-14",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["scim"],
        "prefix": "product-docs",
    },
    {
        "document_id": "dr_bcp_rto_rpo",
        "title": "Disaster Recovery and Business Continuity — RTO and RPO",
        "excerpt": (
            "Active-active across two regions per residency zone. Published objectives: "
            "RTO 2 hours and RPO 5 minutes for the gateway, RTO 4 hours and RPO 15 "
            "minutes for the recipient portal. Service commitment is 99.95% monthly "
            "uptime on the Enterprise plan."
        ),
        "updated_at": "2026-03-19",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["dr_bcp"],
        "prefix": "product-docs",
    },
    {
        "document_id": "ssdlc_secure_development",
        "title": "Secure Development Lifecycle — Design, Build and Release Controls",
        "excerpt": (
            "Every change carries a peer code review, static analysis, dependency "
            "composition analysis and container image scanning before it may merge. "
            "Threat models are produced per service and revisited when the trust "
            "boundary moves."
        ),
        "updated_at": "2026-06-22",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["ssdlc"],
        "prefix": "product-docs",
    },
    {
        "document_id": "sbom_policy",
        "title": "Software Bill of Materials — Generation and Disclosure Policy",
        "excerpt": (
            "A CycloneDX 1.5 SBOM is generated for every deployable artifact at build "
            "time, signed, and retained for the supported life of the release. "
            "Customer-facing SBOMs are available on request under the mutual "
            "non-disclosure agreement."
        ),
        "updated_at": "2026-06-22",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["sbom"],
        "prefix": "product-docs",
    },
    {
        "document_id": "data_residency_matrix",
        "title": "Data Residency Matrix — Regions, Pinning and Cross-Border Transfer",
        "excerpt": (
            "Residency zones: Canada, United States, European Union, United Kingdom, "
            "and Australia. A tenant is pinned to one zone at provisioning. Message "
            "bodies, attachments, keys and derived indexes stay inside the elected zone; "
            "cross-zone transfer happens only where the tenant explicitly enables it."
        ),
        "updated_at": "2026-07-08",
        "audience": _AUD_INTERNAL,
        "topic_ids": ["data_residency", "gdpr"],
        "prefix": "product-docs",
    },
]

# --------------------------------------------------------------------------
# prior-rfps/ — completed questionnaire extracts, attributed to the six
# fictitious accounts in CUSTOMERS. Attribution lives in the sidecar rather
# than the prose (see lambdas/shared/provenance.py): the customer name is
# citable and groupable without steering retrieval.
#
# Two of these six accounts are approved public references. The other four are
# not, which is what makes hard rule 3 observable rather than theoretical.
# --------------------------------------------------------------------------

CORPUS_PRIOR_RFPS: list[dict] = [
    {
        "document_id": "northwind_mutual_caiq_2026",
        "title": "Completed CAIQ extract — Northwind Mutual (encryption and key handling)",
        "excerpt": (
            "CAIQ v4.0.3 responses covering EKM-01 through EKM-04. Message bodies at "
            "rest are sealed with AES-256-GCM under per-message data keys wrapped by a "
            "tenant key-encryption key. Rotation runs on a 365-day cycle by default."
        ),
        "customer_name": "Northwind Mutual",
        "customer_id": "cust-nwm-001",
        "questionnaire_id": "caiq-v4-nwm-2026",
        "questionnaire_name": "CAIQ v4.0.3",
        "answered_at": "2026-05-12",
        "approved_at": "2026-05-12",
        "approved_by": "r.calloway@echoworx.example",
        "audience": _AUD_CUSTOMER,
        "topic_ids": ["encryption_at_rest", "key_management"],
    },
    {
        "document_id": "cascadia_trust_caiq_2026",
        "title": "Completed CAIQ extract — Cascadia Trust Bank (identity and access)",
        "excerpt": (
            "CAIQ v4.0.3 responses covering IAM-01 through IAM-14. SAML 2.0 and OpenID "
            "Connect federation, WebAuthn and TOTP second factors, and SCIM 2.0 "
            "lifecycle provisioning with immediate deactivation."
        ),
        "customer_name": "Cascadia Trust Bank",
        "customer_id": "cust-ctb-002",
        "questionnaire_id": "caiq-v4-ctb-2026",
        "questionnaire_name": "CAIQ v4.0.3",
        "answered_at": "2026-04-02",
        "approved_at": "2026-04-02",
        "approved_by": "d.mensah@echoworx.example",
        "audience": _AUD_CUSTOMER,
        "topic_ids": ["sso", "mfa", "scim"],
    },
    {
        "document_id": "meridian_health_caiq_2026",
        "title": "Completed CAIQ extract — Meridian Health Group (privacy and processing)",
        "excerpt": (
            "CAIQ v4.0.3 responses covering DSP-01 through DSP-19. Standard Data "
            "Processing Agreement with the 2021 EU Standard Contractual Clauses, a "
            "published sub-processor register, and thirty days' notice before any "
            "sub-processor change takes effect."
        ),
        "customer_name": "Meridian Health Group",
        "customer_id": "cust-mhg-003",
        "questionnaire_id": "caiq-v4-mhg-2026",
        "questionnaire_name": "CAIQ v4.0.3",
        "answered_at": "2026-02-18",
        "approved_at": "2026-02-18",
        "approved_by": "s.okonkwo@echoworx.example",
        "audience": _AUD_CUSTOMER,
        "topic_ids": ["dpa", "gdpr", "data_residency"],
    },
    {
        "document_id": "rideau_digital_caiq_2026",
        "title": "Completed CAIQ extract — Province of Rideau, Digital Services (residency)",
        "excerpt": (
            "CAIQ v4.0.3 responses covering DCS-01 through DCS-09. Tenant pinned to the "
            "Canadian residency zone; message bodies, attachments, key material and "
            "derived indexes remain in Canada. Support access from outside Canada "
            "requires a named approval per session."
        ),
        "customer_name": "Province of Rideau - Digital Services",
        "customer_id": "cust-prd-004",
        "questionnaire_id": "caiq-v4-prd-2026",
        "questionnaire_name": "CAIQ v4.0.3",
        "answered_at": "2026-01-27",
        "approved_at": "2026-01-27",
        "approved_by": "s.okonkwo@echoworx.example",
        "audience": _AUD_CUSTOMER,
        "topic_ids": ["data_residency", "iso27001"],
    },
    {
        "document_id": "anvil_capital_caiq_2025",
        "title": "Completed CAIQ extract — Anvil Capital Partners (resilience and response)",
        "excerpt": (
            "CAIQ v4.0.3 responses covering BCR-01 through BCR-11 and SEF-01 through "
            "SEF-08. Active-active regional topology, twice-yearly recovery exercises, "
            "and confirmed-incident notification to affected tenants within 72 hours."
        ),
        "customer_name": "Anvil Capital Partners",
        "customer_id": "cust-acp-005",
        "questionnaire_id": "caiq-v4-acp-2025",
        "questionnaire_name": "CAIQ v4.0.3",
        "answered_at": "2025-11-06",
        "approved_at": "2025-11-06",
        "approved_by": "l.fontaine@echoworx.example",
        "audience": _AUD_CUSTOMER,
        "topic_ids": ["dr_bcp", "incident_response"],
    },
    {
        "document_id": "helix_reinsurance_caiq_2025",
        "title": "Completed CAIQ extract — Helix Reinsurance (assurance and supply chain)",
        "excerpt": (
            "CAIQ v4.0.3 responses covering A&A-01 through A&A-06 and STA-01 through "
            "STA-14. SOC 2 Type II and ISO/IEC 27001 evidence, annual independent "
            "penetration testing, and a signed CycloneDX software bill of materials per "
            "deployable artifact."
        ),
        "customer_name": "Helix Reinsurance",
        "customer_id": "cust-hxr-006",
        "questionnaire_id": "caiq-v4-hxr-2025",
        "questionnaire_name": "CAIQ v4.0.3",
        "answered_at": "2025-09-22",
        "approved_at": "2025-09-22",
        "approved_by": "l.fontaine@echoworx.example",
        "audience": _AUD_CUSTOMER,
        "topic_ids": ["soc2", "pentest", "sbom"],
    },
]

# --------------------------------------------------------------------------
# sme-approved/ — hand-written Q&A. This is the only source that feeds the H
# signal (scorer.score_h), and the retriever only queries it for the
# `unclassified` class, so these two pairs deliberately sit on the long-tail
# topics at the bottom of the fixture rather than on anything an authoritative
# primary already covers.
#
# Question 38 (unified endpoint management) is deliberately NOT covered here.
# It is the question the SME answers on camera, and the follow-up workbook
# re-asks it verbatim once that answer has been ingested.
# --------------------------------------------------------------------------

CORPUS_SME_APPROVED: list[dict] = [
    {
        "document_id": "sme-echoworx-0001",
        "question_text": (
            "Can recipients open encrypted messages using a third-party secure email "
            "gateway from another vendor, without enrolling in our recipient portal?"
        ),
        "answer_text": (
            "Yes. Where the recipient's organisation operates its own secure email "
            "gateway, delivery is negotiated gateway-to-gateway over enforced TLS with "
            "MTA-STS, and the message is delivered into the recipient's ordinary "
            "mailbox with no enrolment step and no portal account. S/MIME and OpenPGP "
            "delivery are also available where the recipient publishes a usable "
            "certificate or key. The recipient portal is the fallback for recipients "
            "whose organisation offers no interoperable path, not the default. "
            "Interoperability with another vendor's gateway is a delivery-policy "
            "setting per partner domain, configured by the tenant administrator."
        ),
        "topic_ids": [],
        "approved_by": "r.calloway@echoworx.example",
        "approved_at": "2026-07-14",
        "expires_on": "2027-07-14",
        "corroborated_by": ["encryption_in_transit_tls"],
    },
    {
        "document_id": "sme-echoworx-0002",
        "question_text": (
            "What administrative and delivery event records can a tenant export to its "
            "own SIEM, and in what format?"
        ),
        "answer_text": (
            "Tenant administrators can export three event streams: administrative "
            "actions in the tenant plane, message delivery and disposition events, and "
            "recipient authentication events. Records are emitted as newline-delimited "
            "JSON conforming to a published schema, and can be pulled from a "
            "per-tenant export endpoint or pushed to a customer-owned object store "
            "bucket on a five-minute cadence. Field-level documentation and a mapping "
            "to the Elastic Common Schema are supplied so the stream can be onboarded "
            "into a customer SIEM without bespoke parsing. Records are retained in the "
            "platform for 400 days independently of what the tenant exports."
        ),
        "topic_ids": [],
        "approved_by": "d.mensah@echoworx.example",
        "approved_at": "2026-07-14",
        "expires_on": "2027-07-14",
        "corroborated_by": ["scim_provisioning"],
    },
]


def main() -> None:
    print(f"Generating synthetic data under {DATA}")

    # Incoming RFP
    _make_rfp_workbook(
        DATA / "incoming" / "sample_rfp_acmesec.xlsx",
        title="Acme Corp — Vendor Security Questionnaire",
        prospect="Acme Corp (fictional)",
        questions=INCOMING_RFP_QUESTIONS,
        with_answers=False,
    )
    print(f"  ✓ incoming/sample_rfp_acmesec.xlsx ({len(INCOMING_RFP_QUESTIONS)} questions)")

    # Historical RFPs
    for name, prospect, qa, win_loss in WON_RFPS:
        _make_rfp_workbook(
            DATA / "historical" / f"{name}.xlsx",
            title=f"{name.replace('_', ' ').title()}",
            prospect=prospect,
            questions=qa,
            with_answers=True,
            win_loss=win_loss,
        )
        print(f"  ✓ historical/{name}.xlsx ({len(qa)} Q&A, {win_loss})")

    # Seismic
    _write_json(DATA / "seismic" / "content_cards.json", SEISMIC_CARDS)
    print(f"  ✓ seismic/content_cards.json ({len(SEISMIC_CARDS)} cards)")

    # Gong
    _write_json(DATA / "gong" / "transcripts.json", GONG_TRANSCRIPTS)
    print(f"  ✓ gong/transcripts.json ({len(GONG_TRANSCRIPTS)} transcripts)")

    # Whitepapers
    for slug, body in WHITEPAPERS.items():
        _write_markdown(DATA / "whitepapers" / f"{slug}.md", body)
    print(f"  ✓ whitepapers/ ({len(WHITEPAPERS)} docs)")

    # Graph seed
    _write_json(DATA / "graph" / "customers.json", CUSTOMERS)
    _write_json(DATA / "graph" / "products_and_topics.json", PRODUCTS_AND_TOPICS)
    print(f"  ✓ graph/customers.json ({len(CUSTOMERS)} customers)")
    print("  ✓ graph/products_and_topics.json")

    # Reference corpus sidecars. The corpus is authored as a fixed set, so the
    # target directories are cleared first — a stale document left behind from
    # an earlier corpus would still be uploaded by seed_s3_corpus.py and would
    # break the per-prefix counts that scripts/verify_fixture.py asserts.
    for prefix in ("compliance", "product-docs", "prior-rfps", "sme-approved"):
        for stale in (DATA / "corpus" / prefix).glob("*.json"):
            stale.unlink()

    # Primary sources — compliance/ and product-docs/
    per_prefix: dict[str, int] = {}
    for doc in CORPUS_PRIMARY:
        prefix = str(doc["prefix"])
        doc_id = doc["document_id"]
        payload = {k: v for k, v in doc.items() if k != "prefix"}
        _write_json(DATA / "corpus" / prefix / f"{doc_id}.json", payload)
        per_prefix[prefix] = per_prefix.get(prefix, 0) + 1
    print(f"  ✓ corpus/compliance/ ({per_prefix.get('compliance', 0)} sidecars)")
    print(f"  ✓ corpus/product-docs/ ({per_prefix.get('product-docs', 0)} sidecars)")

    # Prior questionnaires — attribution carried in the sidecar, not the prose.
    for doc in CORPUS_PRIOR_RFPS:
        _write_json(DATA / "corpus" / "prior-rfps" / f"{doc['document_id']}.json", doc)
    print(f"  ✓ corpus/prior-rfps/ ({len(CORPUS_PRIOR_RFPS)} completed questionnaire extracts)")

    # SME-approved Q&A — the only source of the H signal.
    for doc in CORPUS_SME_APPROVED:
        _write_json(DATA / "corpus" / "sme-approved" / f"{doc['document_id']}.json", doc)
    print(f"  ✓ corpus/sme-approved/ ({len(CORPUS_SME_APPROVED)} hand-written Q&A pairs)")

    expired = [d for d in CORPUS_PRIMARY if str(d.get("expires_on", "9999")) < TODAY]
    for doc in expired:
        print(
            f"  ! corpus/compliance/{doc['document_id']} is intentionally EXPIRED "
            f"(updated_at={doc['updated_at']}, expires_on={doc['expires_on']})"
        )

    print("Done.")


if __name__ == "__main__":
    main()
