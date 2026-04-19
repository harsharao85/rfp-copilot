"""Seed the reference corpus and DynamoDB tables for the RFP Copilot demo.

Uploads:
  - data/corpus/compliance/*.json  → s3://BUCKET/compliance/
  - data/corpus/product-docs/*.json → s3://BUCKET/product-docs/
Writes:
  - CustomerRefsTable  — from data/graph/customers.json
  - LibraryFeedbackTable — synthetic prior-approved Q&A enabling H>0 (GREEN scoring)

Usage:
  python3.13 scripts/seed_reference_data.py

Requires AWS credentials with s3:PutObject, dynamodb:PutItem on the target resources.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import boto3

ROOT = Path(__file__).parent.parent
CORPUS_DIR = ROOT / "data" / "corpus"

REFERENCE_CORPUS_BUCKET = "rfp-copilot-dev-storage-referencecorpusbucketeced4-zxt34jdeqioy"
CUSTOMER_REFS_TABLE = "rfp-copilot-dev-data-CustomerRefsTableF30403AC-KZ05QYSOZFL7"
LIBRARY_FEEDBACK_TABLE = "rfp-copilot-dev-data-LibraryFeedbackTableB9B77862-142EH4ZTF5PQ2"
REGION = "us-east-1"

s3 = boto3.client("s3", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION)


def upload_corpus() -> None:
    prefixes = {
        "compliance": CORPUS_DIR / "compliance",
        "product-docs": CORPUS_DIR / "product-docs",
    }
    total = 0
    for prefix, directory in prefixes.items():
        for path in sorted(directory.glob("*.json")):
            key = f"{prefix}/{path.name}"
            s3.upload_file(str(path), REFERENCE_CORPUS_BUCKET, key)
            print(f"  uploaded s3://{REFERENCE_CORPUS_BUCKET}/{key}")
            total += 1
    print(f"Corpus: {total} files uploaded.\n")


def seed_customer_refs() -> None:
    table = ddb.Table(CUSTOMER_REFS_TABLE)
    customers = json.loads((ROOT / "data" / "graph" / "customers.json").read_text())
    with table.batch_writer() as batch:
        for c in customers:
            # Table PK is camelCase customerId; JSON source uses snake_case customer_id.
            item = {**c, "customerId": c.pop("customer_id", c.get("customerId", ""))}
            batch.put_item(Item=item)
    print(f"CustomerRefs: {len(customers)} records written.\n")


# Prior-approved Q&A pairs seeded into LibraryFeedback.
# Each entry has topic_ids that match the retriever's topic taxonomy so that
# _dynamo_prior_matches() returns them and the H signal becomes > 0.
# H>0 is required to reach GREEN (composite >= 0.80).
PRIOR_ANSWERS = [
    {
        "question_text": "How is customer data encrypted at rest?",
        "answer_text": (
            "All customer data is encrypted at rest using AES-256-GCM. "
            "Keys are managed by AWS KMS with automatic annual rotation. "
            "Enterprise customers may supply their own CMK (BYOK) via an "
            "AWS KMS key policy in their own account. [encryption_whitepaper]"
        ),
        "topic_ids": ["encryption", "data_protection", "kms"],
        "approved_by": "sarah.chen@example.com",
        "approved_at": "2026-01-15T10:00:00Z",
        "expires_on": "2027-01-15",
        "corroborated_by": ["encryption_whitepaper"],
    },
    {
        "question_text": "How is data encrypted in transit?",
        "answer_text": (
            "All data in transit is protected by TLS 1.2 or 1.3. "
            "We require PFS (ECDHE cipher suites), disable TLS 1.0/1.1, "
            "and publish HSTS headers with a one-year max-age. [encryption_whitepaper]"
        ),
        "topic_ids": ["encryption", "tls", "data_protection"],
        "approved_by": "sarah.chen@example.com",
        "approved_at": "2026-01-15T10:05:00Z",
        "expires_on": "2027-01-15",
        "corroborated_by": ["encryption_whitepaper"],
    },
    {
        "question_text": "Which SSO protocols are supported?",
        "answer_text": (
            "We support SAML 2.0 and OIDC for customer-initiated SSO. "
            "SCIM 2.0 is supported for automated user provisioning. "
            "OAuth 2.0 Authorization Code + PKCE is used for native integrations. "
            "[sso_mfa_guide]"
        ),
        "topic_ids": ["sso", "saml", "oidc", "scim"],
        "approved_by": "james.okafor@example.com",
        "approved_at": "2026-02-03T14:00:00Z",
        "expires_on": "2027-02-03",
        "corroborated_by": ["sso_mfa_guide"],
    },
    {
        "question_text": "Is MFA supported and which factors?",
        "answer_text": (
            "MFA is supported for all user tiers and can be enforced by administrator policy. "
            "Supported factors: TOTP authenticator apps, WebAuthn/FIDO2 hardware keys, "
            "and SMS (not recommended for high-assurance environments). "
            "Hardware keys are required for administrative roles in the Enterprise tier. "
            "[sso_mfa_guide]"
        ),
        "topic_ids": ["mfa", "authentication", "sso"],
        "approved_by": "james.okafor@example.com",
        "approved_at": "2026-02-03T14:10:00Z",
        "expires_on": "2027-02-03",
        "corroborated_by": ["sso_mfa_guide"],
    },
    {
        "question_text": "Describe your DR capabilities and RTO/RPO targets.",
        "answer_text": (
            "The platform is deployed active-active across two AWS regions. "
            "Critical metadata uses synchronous replication; bulk data uses "
            "asynchronous replication with a lag under 60 seconds. "
            "Target RTO is under four hours; target RPO is under fifteen minutes. "
            "DR exercises are conducted twice annually. [dr_bcp_overview]"
        ),
        "topic_ids": ["disaster_recovery", "availability", "rto", "rpo", "backup"],
        "approved_by": "sarah.chen@example.com",
        "approved_at": "2026-01-20T09:00:00Z",
        "expires_on": "2027-01-20",
        "corroborated_by": ["dr_bcp_overview"],
    },
    {
        "question_text": "What are your backup procedures?",
        "answer_text": (
            "Automated daily snapshots of all customer data are retained for 35 days. "
            "Weekly snapshots are retained for 12 months. "
            "Cross-region replication copies each snapshot to the secondary region. "
            "Restore drills are performed quarterly. [dr_bcp_overview]"
        ),
        "topic_ids": ["backup", "disaster_recovery", "data_protection"],
        "approved_by": "sarah.chen@example.com",
        "approved_at": "2026-01-20T09:30:00Z",
        "expires_on": "2027-01-20",
        "corroborated_by": ["dr_bcp_overview"],
    },
    {
        "question_text": "Describe your incident response process.",
        "answer_text": (
            "Security incidents are triaged by our 24×7 Security Operations Center "
            "within 15 minutes of detection. A named Incident Commander follows the "
            "NIST 800-61 framework. Customers affected by a confirmed security incident "
            "are notified within 72 hours with scope, impact, and remediation status. "
            "[soc2_cert_2025]"
        ),
        "topic_ids": ["incident_response", "security_operations", "soc2"],
        "approved_by": "priya.sharma@example.com",
        "approved_at": "2026-03-01T11:00:00Z",
        "expires_on": "2027-03-01",
        "corroborated_by": ["soc2_cert_2025"],
    },
    {
        "question_text": "What access controls govern internal access to customer data?",
        "answer_text": (
            "Access to customer data by internal personnel requires a formal access "
            "request, manager approval, and documented business justification. "
            "All access is logged to an immutable audit trail. Privileged access is "
            "reviewed quarterly and revoked within 24 hours of role change. "
            "Least-privilege is enforced via IAM policies scoped to individual services. "
            "[soc2_cert_2025]"
        ),
        "topic_ids": ["access_control", "iam", "audit", "soc2"],
        "approved_by": "priya.sharma@example.com",
        "approved_at": "2026-03-01T11:30:00Z",
        "expires_on": "2027-03-01",
        "corroborated_by": ["soc2_cert_2025"],
    },
]


def seed_library_feedback() -> None:
    table = ddb.Table(LIBRARY_FEEDBACK_TABLE)
    with table.batch_writer() as batch:
        for qa in PRIOR_ANSWERS:
            item = {
                "answerId": str(uuid.uuid4()),
                "version": "1",
                "sme_approved": True,
                **qa,
            }
            batch.put_item(Item=item)
    print(f"LibraryFeedback: {len(PRIOR_ANSWERS)} prior-approved Q&A records written.\n")


if __name__ == "__main__":
    print("=== Seeding reference corpus ===")
    upload_corpus()
    print("=== Seeding CustomerRefs ===")
    seed_customer_refs()
    print("=== Seeding LibraryFeedback ===")
    seed_library_feedback()
    print("Done. Re-upload an RFP to verify GREEN scoring.")
