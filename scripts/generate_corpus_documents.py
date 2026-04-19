#!/usr/bin/env python3
"""Expand compact corpus sidecars into realistic documents for Bedrock KB ingestion.

Inputs:   data/corpus/{compliance,product-docs,prior-rfps,sme-approved}/*.json
Outputs:  data/corpus-real/<prefix>/<doc_id>.<ext>
          data/corpus-real/<prefix>/<doc_id>.<ext>.metadata.json

- compliance/*.json   → multi-page PDF (reportlab) + .pdf.metadata.json
- product-docs/*.json → long-form markdown         + .md.metadata.json
- prior-rfps/*.json   → short markdown             + .md.metadata.json
- sme-approved/*.json → Q&A markdown (H-signal)    + .md.metadata.json

Metadata sidecar format (required verbatim by Bedrock Knowledge Bases):
  {"metadataAttributes": {"document_id": str,
                          "source_type": "compliance_cert" | "product_doc" | "prior_rfp" | "sme_approved_answer",
                          "updated_at" | "approved_at": "YYYY-MM-DD",
                          "topic_ids": ["..."],
                          ...}}

The source data/corpus/ tree is untouched — the retriever unit tests still
depend on it. Regenerate with:
  python scripts/generate_corpus_documents.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "corpus"
DST = ROOT / "data" / "corpus-real"

SOURCE_TYPE_BY_PREFIX: dict[str, str] = {
    "compliance":    "compliance_cert",
    "product-docs":  "product_doc",
    "prior-rfps":    "prior_rfp",
    "sme-approved":  "sme_approved_answer",
}

# ---------------------------------------------------------------------------
# Per-document content — handwritten expansions keyed by document_id.
# Each body builds on the compact excerpt in data/corpus/**.
# ---------------------------------------------------------------------------

COMPLIANCE_BODIES: dict[str, list[dict[str, Any]]] = {
    "soc2_cert_2025": [
        {"h": "Report scope and boundaries"},
        {"p": "This Service Organization Control 2 (SOC 2) Type II report addresses the suitability of the design and operating effectiveness of controls at the service organization relevant to the Trust Services Criteria for Security, Availability, and Confidentiality. The report period is October 1, 2024 through September 30, 2025."},
        {"p": "The system boundary comprises the multi-tenant SaaS platform and its supporting infrastructure within Amazon Web Services regions us-east-1 and us-west-2, including application services, data stores, identity and access management, logging and monitoring pipelines, and the internal tooling used by engineering and operations personnel. Corporate IT, HR systems, and marketing properties are explicitly out of scope."},
        {"p": "Subservice organizations — Amazon Web Services for compute, storage, and managed database infrastructure; Okta for workforce identity; Datadog for application performance monitoring — are evaluated under the carve-out method. Complementary subservice organization controls (CSOCs) expected of each are enumerated in Appendix B."},
        {"h": "Trust Services Criteria coverage"},
        {"p": "Security criteria (Common Criteria CC1.x through CC9.x) are addressed in full. Availability criteria (A1.1 through A1.3) are addressed in the context of the published service level commitment of 99.9% monthly uptime for Enterprise tier customers. Confidentiality criteria (C1.1 and C1.2) cover the classification, handling, retention, and disposal of customer data. Processing Integrity and Privacy criteria are not in scope for this report."},
        {"h": "Control activity summary"},
        {"table": [
            ["TSC reference", "Control activity", "Test result"],
            ["CC6.1", "Logical access to production systems is restricted to authenticated personnel via single sign-on with MFA.", "No exceptions noted"],
            ["CC6.6", "All production administrative access is brokered through a bastion with session recording.", "No exceptions noted"],
            ["CC6.7", "Encryption at rest uses AES-256-GCM with keys managed in AWS KMS. Key material is never exported in plaintext.", "No exceptions noted"],
            ["CC7.2", "Security-relevant events are forwarded to a centralized SIEM with 13-month retention and 24x7 monitoring.", "No exceptions noted"],
            ["CC7.3", "The incident response process is tested at least twice annually via tabletop and one live-fire exercise.", "No exceptions noted"],
            ["A1.2", "Data is replicated asynchronously across availability zones with a target recovery point objective of 15 minutes.", "No exceptions noted"],
            ["C1.1", "Customer data is classified at ingestion into one of four tiers with handling requirements escalating by tier.", "No exceptions noted"],
        ]},
        {"h": "Independent service auditor's opinion"},
        {"p": "In our opinion, in all material respects, the controls stated in management's description were suitably designed to provide reasonable assurance that the service commitments and system requirements would be achieved based on the applicable trust services criteria, and operated effectively throughout the period October 1, 2024 to September 30, 2025. No qualified opinions are issued."},
        {"p": "This report is intended solely for the information and use of the service organization, user entities of the service organization's services during the period, and prospective user entities, independent auditors and practitioners providing services to such user entities, and regulators with direct oversight. Reproduction or distribution beyond that set of recipients requires prior written consent."},
        {"h": "Complementary user entity controls"},
        {"p": "The achievement of the trust services criteria is dependent in part on controls that user entities implement on their own. User entities are responsible for: (a) provisioning and deprovisioning their own personnel in the identity provider; (b) configuring their tenant's security settings, including session timeout, IP allowlisting, and MFA enforcement; (c) reviewing audit logs exported from the platform on a schedule aligned to their own compliance obligations; (d) reporting suspected security events affecting their tenant to the service organization's security operations team through the documented channels."},
        {"h": "Bridge letter and next reporting period"},
        {"p": "A bridge letter covering the period from October 1, 2025 through the issue date of the subsequent SOC 2 Type II report is available on the Trust Center under non-disclosure. The next SOC 2 Type II report is scheduled for issuance in the fourth quarter of 2026, covering the twelve-month period ending September 30, 2026."},
        {"h": "Appendix A — list of principal service commitments"},
        {"p": "Commitment 1: the service maintains encryption at rest and in transit for all customer data using industry-standard algorithms and key lengths. Commitment 2: logical access to customer data by service organization personnel requires documented business need, MFA, and is recorded. Commitment 3: customer-initiated data deletion requests are honored within 30 days. Commitment 4: confirmed security incidents affecting a customer tenant are communicated within 72 hours. Commitment 5: the service maintains a monthly uptime of 99.9% for Enterprise tier customers, measured as a rolling monthly average excluding scheduled maintenance windows announced at least 14 days in advance."},
        {"h": "Appendix B — complementary subservice organization controls"},
        {"p": "AWS is expected to maintain physical and environmental controls in accordance with its own SOC 2 Type II report and AWS Artifact attestations. Okta is expected to maintain the availability and integrity of the workforce identity provider. Datadog is expected to maintain the confidentiality of the telemetry ingested from the service organization's environment. User entities should review the relevant reports from these subservice organizations as part of their own vendor risk assessments."},
        {"h": "Management's description of the system"},
        {"p": "The system supporting the services in scope is a multi-tenant SaaS platform deployed on AWS. The platform comprises application compute (containers orchestrated on AWS ECS with Fargate), relational and key-value data stores (Amazon Aurora and Amazon DynamoDB), object storage (Amazon S3), and asynchronous messaging and workflow (Amazon SQS, Amazon SNS, AWS Step Functions). Customer tenant isolation is enforced at the application layer by a tenant-identifier carried on every request and validated at data-access boundaries, reinforced at the storage layer by row-level scoping for relational data and key-prefix scoping for object storage."},
        {"p": "Personnel operating the system are employees and long-term contractors of the service organization, organized into engineering, operations, security, compliance, and customer-facing functions. Access to production is limited to a defined subset of engineering and operations personnel, controlled through a standing-approval pattern where routine production operations are pre-authorized and all access is logged; break-glass access is available through a documented, reviewed process requiring a second authorizer."},
        {"p": "Software changes are managed through a documented change-management process. Every change is traceable from the originating ticket through peer review, automated testing, deployment approval, and post-deployment verification. Significant changes — defined as changes that alter the boundary of the system, the data it processes, or the controls operating over it — require an architecture review and, where appropriate, a risk assessment by the security function."},
        {"h": "Risk assessment process"},
        {"p": "The service organization performs a risk assessment on a defined cadence and whenever material changes to the business or the system occur. The assessment identifies threats to the service commitments and system requirements, analyzes the likelihood and impact of those threats in light of the control environment, and determines the appropriate response — accept, mitigate, transfer, or avoid. Threats and controls are tracked in a risk register reviewed quarterly by the risk committee, with a management-level review at least annually."},
        {"p": "For the reporting period, the risk register identified and addressed threats in the following categories: external attack against customer-facing endpoints; compromise of administrative credentials; insider misuse by authorized personnel; supply-chain compromise through software dependencies or subservice organizations; data loss due to infrastructure failure; service disruption due to regional events; and regulatory or contractual non-compliance. Each threat is mapped to controls expected to mitigate it, and monitoring is in place to detect control failures."},
        {"h": "Changes during the reporting period"},
        {"p": "During the reporting period, the following changes to the control environment are noted: the identity provider for workforce single sign-on was migrated from a prior vendor to the current provider (Okta); the central log aggregation platform's retention was increased from 13 months to 15 months to align with an updated customer contractual commitment; the incident response playbooks for regional-event scenarios were revised following a live-fire DR exercise in April 2025. None of these changes materially affected the design or operating effectiveness of the controls in scope; all changes were evaluated against the applicable trust services criteria and followed the standard change management process."},
        {"h": "Tests performed and results — detail"},
        {"p": "Control testing was performed through a combination of inquiry, observation, inspection of evidence, and re-performance of control activities. Sample sizes followed the AICPA guidance for attestation engagements, drawn on a representative basis across the reporting period. The auditor's tests of controls and the results of those tests are summarized below."},
        {"table": [
            ["TSC reference", "Nature of test", "Sample size", "Result"],
            ["CC1.1", "Inspected governance documents and management review minutes.", "All meetings held", "No exceptions noted"],
            ["CC2.2", "Inspected evidence of communication of policies to personnel.", "25 of 250 personnel", "No exceptions noted"],
            ["CC3.1", "Inspected risk assessment output and mapped to treatment plan.", "All risk categories", "No exceptions noted"],
            ["CC5.2", "Observed onboarding and deactivation for sampled personnel events.", "30 events from 900", "No exceptions noted"],
            ["CC6.1", "Re-performed MFA policy verification for sampled production access.", "40 access events from ~8,000", "No exceptions noted"],
            ["CC6.2", "Inspected logs of administrative access through the bastion.", "All bastion sessions for 10 sample days", "No exceptions noted"],
            ["CC6.7", "Inspected KMS key policies and AWS CloudTrail logs of key usage.", "All production CMKs", "No exceptions noted"],
            ["CC7.2", "Inspected SIEM retention configuration and sample alert handling.", "20 alerts from thousands", "No exceptions noted"],
            ["CC7.3", "Inspected tabletop exercise and live-fire exercise records.", "2 exercises in period", "No exceptions noted"],
            ["CC8.1", "Inspected change management records and emergency-change approvals.", "50 standard + all emergency changes", "No exceptions noted"],
            ["A1.2", "Inspected replication monitoring dashboards and DR exercise reports.", "All DR-relevant metrics over period", "No exceptions noted"],
            ["A1.3", "Inspected backup test records and sample restore artifacts.", "12 monthly tests", "No exceptions noted"],
            ["C1.1", "Re-performed classification labeling against sample ingested data.", "50 records from millions", "No exceptions noted"],
        ]},
        {"h": "Description of criteria for selecting customers to be informed of incidents"},
        {"p": "An incident is considered reportable to an affected customer when an event has been confirmed to have affected the confidentiality, integrity, or availability of that customer's tenant or data, or where a reasonable person would conclude on the balance of evidence that such effect occurred. Events that are confined to internal systems and do not affect tenant isolation, or events that are prevented by controls before impact occurs, are not reportable under this criterion. Reporting follows the service organization's incident response procedure and is executed through the communication channels registered against the tenant."},
        {"h": "Appendix C — glossary of terms"},
        {"p": "ATO: Authority to Operate. BYOK: Bring Your Own Key. CMK: Customer Master Key. CSOC: Complementary Subservice Organization Control. DR: Disaster Recovery. GCM: Galois/Counter Mode. HSM: Hardware Security Module. IdP: Identity Provider. KMS: Key Management Service. MFA: Multi-Factor Authentication. NIST: National Institute of Standards and Technology. OIDC: OpenID Connect. RPO: Recovery Point Objective. RTO: Recovery Time Objective. SAML: Security Assertion Markup Language. SCIM: System for Cross-domain Identity Management. SIEM: Security Information and Event Management. SoA: Statement of Applicability. SOC: System and Organization Controls. SP: Service Provider. SSDLC: Secure Software Development Lifecycle. TLS: Transport Layer Security. TSC: Trust Services Criteria."},
    ],
    "iso27001_cert_2026": [
        {"h": "Certificate of registration"},
        {"p": "This certifies that the information security management system operated by the service organization has been assessed and found to conform to the requirements of ISO/IEC 27001:2022. Certificate number ISMS-2026-00142 is valid from January 15, 2026 through January 14, 2029, subject to continued satisfactory surveillance."},
        {"h": "Scope of certification"},
        {"p": "The scope of the certified information security management system comprises the design, development, operation, and support of the multi-tenant Software-as-a-Service platform, including the underlying cloud infrastructure, the internal tools used to build and operate the platform, and the workforce identity, access, and monitoring systems supporting those activities. Corporate IT functions unrelated to platform operations are outside the scope of this certification."},
        {"h": "Statement of Applicability summary"},
        {"p": "All 93 controls in Annex A of ISO/IEC 27001:2022 have been evaluated for applicability. The Statement of Applicability (SoA) version 4.2, dated December 2025, is available on the Trust Center under non-disclosure. The following controls are explicitly declared not applicable, with justifications recorded in the SoA: A.5.7 (threat intelligence from classified sources — not within the legal remit of a private-sector provider in our operating jurisdictions), A.7.11 (supporting utilities for on-premises data centers — not applicable as the service is cloud-hosted and physical and environmental controls are carried by the subservice organization)."},
        {"h": "Audit cycle and surveillance"},
        {"p": "Certification audits follow the standard three-year cycle: an initial (Stage 1 and Stage 2) certification audit, followed by two annual surveillance audits in years two and three, followed by a recertification audit at the end of year three. Surveillance audits cover a rotating subset of Annex A controls, selected by the auditor based on risk and the prior audit outcome; in practice roughly one-third of applicable controls are examined at each surveillance. Internal audits cover the full ISMS annually, independent of the external cycle."},
        {"h": "Management commitment"},
        {"p": "Executive management reviews the ISMS at least quarterly. Review outcomes include an affirmation of the information security policy, validation of the risk treatment plan, assessment of metrics against targets, and decisions on resource allocation and improvement actions. The minutes of the most recent management review, with redactions for confidential business matters, are available to customers under non-disclosure."},
        {"h": "Certification body"},
        {"p": "The certification body is accredited under the International Accreditation Forum's multilateral agreement for management system certification. Certificate verification is available through the accreditation body's public registry, and the service organization is listed as a certified client in good standing as of the date of this document."},
        {"h": "Risk treatment plan overview"},
        {"p": "The ISMS risk treatment plan catalogues information security risks identified through the risk assessment process and records the treatment decision for each. The treatment options — modification of the risk through additional controls, retention of the risk at its current level, sharing of the risk through transfer mechanisms such as insurance or contract, or avoidance of the activity giving rise to the risk — are selected based on the risk owner's judgement, the residual risk after the proposed treatment, and the cost of the treatment relative to the reduction in residual risk. Every treatment decision is recorded with a rationale, an owner, a due date for implementation, and a verification method."},
        {"p": "Residual risks that exceed the organization's risk appetite are escalated to executive management for acceptance. As of the most recent management review, no residual risk in the register is being retained above the established risk appetite; all elevated residual risks have an active treatment action with a defined target date."},
        {"h": "Control implementation highlights"},
        {"p": "Annex A.5.15 (Access control) — access to the platform and its supporting systems is governed by a formal policy requiring unique identities for all users, MFA for any access to production or customer data, role-based entitlements derived from job function, and quarterly access reviews. Exceptions to the policy are registered, time-bound, and approved by the information security function. Workforce identity is managed through a single identity provider; deprovisioning on employment change is executed within 24 hours of the triggering event."},
        {"p": "Annex A.8.24 (Use of cryptography) — cryptographic controls are implemented consistent with the organization's Cryptography Policy, which specifies approved algorithms, minimum key sizes, protocol versions, and key-management practices. The policy is reviewed annually against the guidance of authoritative bodies (NIST, IETF, national equivalents) and updated when recommended practices change. All data at rest and in transit within the in-scope boundary is encrypted with algorithms and parameters permitted by the Cryptography Policy."},
        {"p": "Annex A.8.16 (Monitoring activities) — operational and security-relevant events are collected, correlated, and retained in a central SIEM platform. Detection rules address documented threat categories and are tuned through a rule-lifecycle process covering authoring, peer review, performance measurement, and retirement of stale rules. Alerts are triaged by a 24x7 security operations function with defined response times by severity."},
        {"p": "Annex A.5.29 (Information security during disruption) — business continuity and disaster recovery arrangements ensure the platform meets its published recovery-time and recovery-point objectives in the event of regional or zonal disruption. Arrangements are exercised at least twice annually and updated on the basis of exercise outcomes and changes to the service architecture."},
        {"h": "Nonconformities and corrective actions"},
        {"p": "During the most recent annual surveillance audit, three minor nonconformities were raised by the external auditor. Corrective actions have been implemented and verified for all three: (1) the attendance record for one quarterly access review had been retained in an inconsistent location, corrected by consolidating retention to a single location of record; (2) one documented procedure had not been updated to reflect a tooling change, corrected by revising the procedure; (3) one risk owner had lapsed in reviewing an assigned risk within the required cadence, corrected by reassigning ownership and reinforcing the cadence through automated reminders. No major nonconformities were raised."},
        {"h": "ISMS performance evaluation"},
        {"p": "The effectiveness of the ISMS is evaluated against a defined set of metrics reviewed at each management review. Metrics cover access-control effectiveness (proportion of access reviews completed on schedule, mean time to revoke access on termination), incident response (mean time to detect, mean time to respond, mean time to resolve), change management (change failure rate, emergency change volume), and customer outcomes (customer-reported security events, customer audit observations requiring response). Trends in each metric are reported alongside targets, and material deviations trigger investigation."},
        {"p": "The ISMS has met or exceeded all target metrics over the most recent reporting period, with the exception of one metric — mean time to detect for a specific class of internal-only event — which was below target in one quarter due to a sensor misconfiguration. The misconfiguration has been corrected and the metric returned to target in the subsequent quarter."},
    ],
    "fedramp_status_2026": [
        {"h": "FedRAMP Moderate — authorization progress report"},
        {"p": "As of March 1, 2026, the service organization is pursuing FedRAMP Moderate authorization through the agency-sponsored Authority to Operate (ATO) path. This status document reflects the position at that date and is refreshed quarterly."},
        {"h": "Current phase"},
        {"p": "The authorization package is in the assessment phase. A Third-Party Assessment Organization (3PAO) meeting the requirements of NIST SP 800-37 has been engaged, and assessment work on the in-scope control set is underway. The System Security Plan (SSP), initial Security Assessment Plan (SAP), and the first round of evidence submissions have been completed; assessment testing is in progress."},
        {"h": "Authorization boundary"},
        {"p": "The authorization boundary as drawn in the current SSP comprises the GovCloud deployment of the SaaS platform in the AWS GovCloud (US) regions, operated logically separately from the commercial multi-tenant environment. Interconnections to AWS GovCloud services are documented with interconnection security agreements. The boundary is drawn to exclude corporate IT systems, commercial-tenant infrastructure, and any subservice organizations that do not themselves hold a FedRAMP authorization at the Moderate or higher impact level."},
        {"h": "Control baseline"},
        {"p": "The applicable control baseline is the FedRAMP Moderate baseline, which tailors NIST SP 800-53 Revision 5 to the cloud service context. Controls are implemented, inherited from AWS GovCloud (as documented in the customer responsibility matrix), or shared. Where controls are shared, the Customer Responsibility Matrix clarifies the division between provider and customer responsibility."},
        {"h": "No committed authorization date"},
        {"p": "The service organization does not publish a committed date for FedRAMP Moderate authorization. The authorization outcome and timeline are determined by the sponsoring agency's Authorizing Official based on the 3PAO's assessment and any remediation required. Customers requiring authorization by a specific date should contact their Account Executive to discuss timeline expectations and any interim compensating controls."},
        {"h": "Interim posture for federal customers"},
        {"p": "Federal and federal-adjacent customers evaluating the platform prior to authorization may request the In-Process SSP, the current 3PAO Readiness Assessment Report, and the Customer Responsibility Matrix under non-disclosure, through their Account Executive. These documents are not a substitute for authorization and do not confer the assurances that the FedRAMP Moderate ATO represents."},
        {"h": "Continuous monitoring expectations"},
        {"p": "Once authorized, the service organization will maintain the authorization through the FedRAMP continuous monitoring program. Continuous monitoring comprises monthly vulnerability scanning of in-scope components with results submitted to the sponsoring agency and, where applicable, to the FedRAMP PMO; annual assessment of a subset of controls on a three-year rotational basis so that all controls are reassessed at least once per authorization cycle; quarterly reporting on key security metrics; and prompt notification to the sponsoring agency of any significant change to the boundary, the threat landscape, or the control environment."},
        {"p": "Plan of Action and Milestones (POA&M) items identified through continuous monitoring or any other source are tracked through remediation, with timelines consistent with the FedRAMP severity-based remediation deadlines (30 days for high-severity findings, 90 days for moderate, 180 days for low). POA&M status is reported to the sponsoring agency on the monthly cadence required by the program."},
        {"h": "Incident response alignment"},
        {"p": "The incident response program will align with the requirements of NIST SP 800-61 Rev. 2 and the reporting obligations of FedRAMP-authorized cloud service providers. In the event of a confirmed security incident affecting a federal customer's tenant, notification will be provided to the sponsoring agency's designated security point of contact and to US-CERT / CISA as required, within the timeframes mandated by FedRAMP and applicable federal guidance. Formal after-action reporting follows within 30 days of incident closure."},
        {"h": "Subservice organizations within the authorization boundary"},
        {"p": "The authorization boundary depends on Amazon Web Services GovCloud (US) as the infrastructure subservice organization. AWS GovCloud holds a FedRAMP High authorization, so the inheritable controls are consumed from that authorization rather than re-assessed for this boundary. The Customer Responsibility Matrix attached to the SSP documents every control, its inheritance posture (fully inherited, partially inherited, provider-implemented), and the provider and customer responsibilities where inheritance is partial. Other subservice organizations are evaluated on a case-by-case basis and are only included within the boundary if they themselves hold a FedRAMP authorization at the Moderate or higher impact level."},
        {"h": "Relationship to commercial platform"},
        {"p": "The GovCloud deployment within the FedRAMP Moderate boundary is a logically and operationally distinct instance from the commercial multi-tenant platform. Customer data does not cross between the two environments. The two environments do share platform engineering artifacts — for example, application code, container images, and the internal tooling used to deploy and operate the platform — which are assessed within the FedRAMP boundary and are deployed into the GovCloud environment through the documented change management process. Changes that are deployed into the GovCloud environment are subject to the FedRAMP significant-change evaluation before deployment; changes that affect only the commercial environment do not follow that evaluation."},
    ],
}

PRODUCT_DOCS_BODIES: dict[str, str] = {
    "encryption_whitepaper": """# Encryption Architecture Whitepaper

## Overview

This whitepaper describes how the platform protects customer data at rest and in transit, the key management model, and the options available to Enterprise customers who wish to hold their own keys.

Three design principles underlie every decision in this document.

First: cryptography is a supporting control, not a primary one. Encryption closes specific residual-risk categories — physical theft of storage media, interception in transit, misconfigured bucket policies — but it does not substitute for access controls, network segmentation, identity hygiene, or operational security. Every decision below assumes the other controls are working.

Second: use well-reviewed primitives, standard protocols, and managed services wherever possible. Writing new cryptography is a known failure mode. Every algorithm and key length referenced in this document follows the recommendations of NIST SP 800-131A Revision 2 and the IETF at the time of writing.

Third: key management is the hard part. The cost of an encryption program is dominated by key lifecycle — generation, rotation, access control, audit, and recovery. Decisions about where keys live, who can access them, and how their use is logged matter more than the choice of algorithm.

## Data-at-rest encryption

All customer data stored in the platform is encrypted at rest using AES-256 in Galois/Counter Mode (AES-256-GCM), via AWS Key Management Service (KMS). AES-256-GCM is an authenticated encryption mode; it protects both the confidentiality and the integrity of the ciphertext, such that tampering with the ciphertext at rest will be detected on decryption.

Data is encrypted at the storage layer. For relational tenant data, encryption is applied by the database engine, using a data key derived from a KMS customer master key (CMK). For object storage, encryption is applied server-side by Amazon S3 (`x-amz-server-side-encryption: aws:kms`) using the same class of CMK. For block storage used by application compute, encryption is applied at the Amazon EBS layer.

Keys are never stored or cached in plaintext outside AWS KMS. Data keys generated from the CMK are used in-memory for the duration of a single encryption or decryption operation and then discarded. This design means that to decrypt any customer data, an attacker would need both access to the ciphertext and the ability to invoke `kms:Decrypt` on the controlling CMK — the latter gated by IAM policy, the KMS key policy, and the AWS CloudTrail audit log.

## Data-in-transit encryption

All data in transit is encrypted using TLS 1.2 or TLS 1.3. TLS 1.0 and TLS 1.1 are disabled at every entrypoint. The cipher suite allowlist requires Perfect Forward Secrecy, realized through ECDHE-based key exchange — which means that compromise of a long-term server private key does not retroactively compromise the session keys of past recorded traffic.

A short, intentionally conservative list of suites is accepted: TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256, TLS_AES_128_GCM_SHA256 (for TLS 1.3), and ECDHE-ECDSA/ECDHE-RSA with AES-GCM for TLS 1.2. All other suites are rejected at the load balancer.

HTTP Strict Transport Security (HSTS) is advertised on all public HTTPS endpoints with a one-year `max-age` and the `includeSubDomains` directive. The apex domain and relevant subdomains are preloaded into the major browsers' HSTS preload lists. Mixed content is not served; an unencrypted request to any customer-facing endpoint is met with a 301 redirect to HTTPS followed by HSTS enforcement on subsequent requests.

Between internal services within the AWS network boundary, mTLS is used where the service-to-service communication crosses a trust boundary (for example, between the data plane and the control plane). Service identity is established through short-lived certificates issued from an internal private CA and rotated on the order of hours.

## Customer-managed keys (BYOK / HYOK)

Enterprise customers may opt to hold the root key controlling the encryption of their tenant's data. Two options are supported.

Option 1 — customer CMK in the customer's AWS account. The customer creates a KMS CMK in their own AWS account, grants the platform's service principal a key policy permitting `kms:Encrypt`, `kms:Decrypt`, `kms:ReEncrypt*`, `kms:GenerateDataKey*`, and `kms:DescribeKey`, and provides the CMK ARN during tenant provisioning. The platform uses the customer's CMK to wrap tenant data keys. Because the key lives in the customer's account, the customer has full visibility of every use via CloudTrail, and can revoke access at any time by revoking the key policy statement. Revoking key access has an immediate effect: in-flight operations fail, and all subsequent reads of encrypted data return an authorization error from KMS until access is restored.

Option 2 — external key material via AWS KMS External Key Store (XKS). For customers with a regulatory or policy requirement that the key material never reside within AWS, the platform supports CMKs backed by an External Key Store, where the root key material lives in a customer-controlled HSM accessible to AWS KMS through a documented interface. All the same KMS operations apply, with the difference that the underlying cryptographic operations occur within the customer's HSM.

In both options, the customer retains the cryptographic lever: revoking access to the root key renders tenant data unreadable. This is by design.

## Key rotation

KMS CMKs under the platform's management are rotated annually by default. Rotation is automatic, transparent, and does not require re-encryption of existing ciphertext — KMS retains access to prior key versions for decryption purposes. Customers holding their own CMKs may rotate on any cadence permissible to their policy; rotation events in the customer's account are reflected automatically through the KMS data-key derivation protocol.

On-demand rotation is supported for all CMK types. A customer-initiated rotation request is processed within minutes and is visible in both the customer's audit log and the platform's audit log.

## Cryptographic hygiene at the edges

Beyond bulk encryption, the platform follows a short list of hygiene practices that close common failure modes. All password hashes are stored using Argon2id with parameters tuned to 500 ms of work on production hardware. All session tokens are opaque, 256-bit random values issued through a cryptographically secure RNG, transmitted only over TLS, and invalidated on logout. API keys for machine-to-machine access are scoped to the minimum set of operations required and are revocable individually from the customer's administrative console. Cryptographic configuration is reviewed at the start of each year against the then-current guidance from NIST and the IETF; algorithms or parameter choices that have fallen out of the recommended set are deprecated on a published schedule.

## Cryptographic agility

A recurring failure mode in long-lived systems is the inability to migrate away from an algorithm, a protocol version, or a key size once it has been deprecated by the cryptographic community. The platform's data-at-rest and data-in-transit stacks are built to support migration.

Data-at-rest keys are referenced by CMK identifier rather than by inlined key material. Replacing a CMK with one using a different algorithm requires only a re-encryption of the affected data keys — a background operation that does not require downtime or re-transmission of customer data. Re-encryption operations are rate-limited to respect KMS request budgets, instrumented with progress reporting, and resumable from partial-failure states.

Data-in-transit protocol versions and cipher suite lists are surfaced as configuration, not code. A change to the supported protocol versions propagates through the load-balancer fleet in under an hour and is reversible by a single configuration rollback. Protocol deprecation follows a published schedule announced to customers at least 90 days in advance.

## Post-quantum posture

The platform is actively tracking the transition to post-quantum cryptography. The current posture reflects three principles.

First: store-now-decrypt-later is a real risk for long-term confidential data. Traffic intercepted today may be decrypted in the future by an adversary that acquires a cryptographically-relevant quantum computer. The platform's TLS configuration is in the process of adding hybrid key exchange (combining a classical algorithm with a post-quantum key-encapsulation mechanism) for customer-facing endpoints, providing forward protection against store-now-decrypt-later adversaries.

Second: post-quantum primitives are younger than classical primitives. A conservative migration path pairs each post-quantum primitive with a classical primitive (the "hybrid" approach) so that a flaw in either primitive alone does not compromise the protected data. The industry is converging on this pattern for the transition period.

Third: symmetric primitives and hash functions are less affected by the quantum threat than public-key cryptography. AES-256 is considered secure against Grover's algorithm at the 128-bit post-quantum security level; SHA-256 is similarly considered secure. The platform's data-at-rest encryption does not require migration on the post-quantum transition timeline; the focus is on key exchange, digital signatures, and certificate infrastructure.

## Key separation by data class

Different classes of customer data use different CMKs, enabling per-class access control and per-class rotation. Relational tenant data, object-storage tenant data, backups, and audit logs each use a distinct CMK. This separation means that a policy or access-control event on one key — for example, revoking access to backups — does not affect the others. The separation also aligns with the different retention policies across classes: audit log CMKs are retained longer than tenant-data CMKs to support the extended retention of audit logs.

Cross-account key use, for the Enterprise BYOK path, follows the same separation pattern: a customer's own CMK controls one class of data. Customers may elect to bring their own key for one class (say, object storage) while relying on the platform's CMK for other classes. The tenant configuration records which classes are under customer-held keys, and the access-and-use story is consistent across classes.

## Key compromise and incident response

In the hypothetical event of a confirmed key compromise, the response playbook comprises: (1) rotate the affected CMK to a new key version and force re-derivation of all dependent data keys; (2) identify the window of potential exposure from KMS CloudTrail logs and incident evidence; (3) re-encrypt affected ciphertext under the new key version; (4) notify affected customers within the notification timelines specified in the relevant customer contract, no later than 72 hours from confirmation. The playbook is rehearsed in the regular tabletop exercise program and updated as the key-management architecture evolves.
""",
    "sso_mfa_guide": """# Single Sign-On and Multi-Factor Authentication Integration Guide

## Overview

This guide describes how to integrate a customer's identity provider (IdP) with the platform for workforce single sign-on and multi-factor authentication, and how automated user provisioning and deprovisioning is handled via SCIM.

The integration story rests on three standard protocols — SAML 2.0, OpenID Connect (OIDC), and SCIM 2.0 — and a small number of platform-specific conventions that align with common enterprise IdPs such as Okta, Microsoft Entra ID, Ping Identity, OneLogin, and Google Workspace.

## SAML 2.0 integration

SAML 2.0 is the default protocol for customer-initiated workforce SSO into the platform. The platform acts as a SAML Service Provider (SP); the customer's IdP is the Identity Provider.

To configure SAML SSO, an administrator in the customer's tenant generates the SP metadata from the Administrative Console, providing an Entity ID, Assertion Consumer Service URL, and signing certificate. The administrator then uploads the corresponding IdP metadata (typically as an XML document or via a metadata URL), maps attributes, and enables the integration.

The platform requires the following attributes on every SAML assertion: a NameID in the form of the user's primary email address (the IdP's email domain must match the verified domain registered against the tenant), a GivenName, a FamilyName, and optionally a set of Group memberships that will drive role mapping on the platform.

Assertion signing is required. Assertion encryption is supported and may be enabled by policy; when enabled, the IdP encrypts the assertion to the SP's published encryption certificate. Signed and encrypted assertions are validated before any application session is created.

## OIDC integration

OpenID Connect is supported for customer-initiated SSO as an alternative to SAML, and is required for the platform's native integrations (desktop and mobile clients) where OAuth 2.0 Authorization Code flow with PKCE is used.

The platform supports the Authorization Code, Implicit, and Refresh Token flows. The Authorization Code flow with PKCE is the recommended pattern for new integrations; the Implicit flow is supported only for legacy single-page applications and will be deprecated in a future release.

Tokens are validated against the IdP's JSON Web Key Set (JWKS), with a maximum clock skew of 60 seconds. Access tokens carry a 1-hour lifetime by default, configurable down to 5 minutes for high-assurance contexts; refresh tokens carry a 14-day lifetime and are subject to rotation and revocation on every use.

## SCIM 2.0 provisioning

SCIM 2.0 is supported for automated user and group lifecycle management. The platform exposes a SCIM endpoint per tenant, authenticated via a long-lived bearer token issued to the customer's IdP.

Supported operations include create, update, deactivate, and reactivate for users, as well as create, update, and delete for groups (with membership changes reflected through `PATCH` operations on the group resource). Deactivation has an immediate effect: the user's active sessions are terminated, outstanding access tokens are revoked, and no new sessions may be established until reactivation.

The platform reconciles SCIM data against its own tenant membership at each operation. Conflicts (for example, a duplicate email address across two SCIM-provisioned tenants) are reported via the standard SCIM error responses and logged for the customer's review.

## Multi-factor authentication

Multi-factor authentication is supported for all user tiers and can be enforced by administrator policy. A tenant administrator can require MFA for all users, for a subset of users based on role, or for users with specific privileges (for example, administrators or users with access to exportable audit data).

The following factors are supported:

- Time-based one-time passwords (TOTP), compatible with RFC 6238 authenticator applications such as Google Authenticator, Microsoft Authenticator, 1Password, and Authy.
- WebAuthn / FIDO2 security keys, including roaming authenticators (for example, YubiKey) and platform authenticators (Windows Hello, Touch ID, Android biometric sensors).
- SMS-delivered one-time codes — supported for compatibility and as a fallback option, but not recommended for high-assurance contexts due to well-documented susceptibility to SIM-swap and SS7-interception attacks.

For administrative roles in the Enterprise tier, hardware-backed MFA (WebAuthn or FIDO2) is required; TOTP and SMS are not accepted for these roles regardless of user preference. This reflects the expectation that administrative actions represent the highest impact in a tenant's security posture.

## Session management

Session lifetimes are governed by tenant policy with platform-enforced upper bounds. The default session lifetime for workforce users is 8 hours, configurable to as short as 15 minutes. Idle timeout defaults to 30 minutes, configurable down to 5 minutes.

Administrative sessions carry a reduced maximum lifetime — 4 hours by default, regardless of policy setting — and require step-up reauthentication for high-impact operations such as creating an API key, modifying an SSO configuration, or exporting audit data.

## Troubleshooting

The SSO configuration page in the Administrative Console includes a signed-assertion test tool that allows an administrator to dry-run a SAML assertion from the IdP against the platform's SP configuration without creating a user session. OIDC configurations may be tested via the corresponding OIDC dry-run tool. Both tools produce a detailed error report when validation fails, without exposing the contents of valid assertions for inspection.

Configuration drift — for example, a signing certificate that has expired, or an attribute mapping that has silently changed — is the most common cause of SSO failure observed across tenants. Customers are advised to monitor the platform's configuration change log, which records every modification to the SSO configuration with the identity of the administrator and the timestamp, and to review expiring-certificate notifications that the platform emits 60, 30, and 7 days in advance of the expiry date.

## Group-to-role mapping

Platform roles are derived from group memberships asserted by the IdP. A tenant administrator defines the mapping from IdP groups to platform roles in the Administrative Console; the mapping is evaluated on every session establishment, so changes in group membership at the IdP are reflected on the user's next login.

Three mapping styles are supported. Direct mapping — one IdP group maps to one platform role — is the simplest and is recommended for most tenants. Hierarchical mapping allows a group to inherit the permissions of another group, with circular inheritance detected and rejected at configuration time. Expression-based mapping supports a constrained expression language that allows conditions on the user's attributes (for example, "map to the `finance_analyst` role if the IdP asserts department=Finance and costCenter=1200"). Expression-based mapping trades flexibility for inspectability; we recommend direct mapping wherever the IdP's group model can support it.

Users who belong to multiple groups receive the union of the permissions conferred by each mapped role, subject to any deny rules configured at the tenant level. A user who belongs to no mapped group at the time of session establishment is denied access and is redirected to an administrator-configured error page, typically pointing the user at the tenant administrator for remediation.

## Just-in-time provisioning

For tenants that prefer not to operate SCIM, the platform supports just-in-time (JIT) user provisioning on the first successful SSO. On JIT, the platform creates a user record from the asserted attributes, assigns the roles implied by the group-to-role mapping, and establishes the application session. On subsequent logins, the attributes and group memberships are refreshed from the latest assertion.

JIT provisioning and SCIM are mutually exclusive on a per-tenant basis: a tenant elects one or the other at integration time. JIT is simpler to configure but provides weaker guarantees — for example, deactivation in the IdP does not propagate to the platform until the user next attempts to log in (and is denied because they are no longer in a mapped group), or until a lifecycle sweep runs on the scheduled cadence. SCIM provides immediate propagation of lifecycle events through the IdP's provisioning engine, at the cost of configuring and maintaining the SCIM connection.

## Break-glass access

Every tenant maintains a break-glass account, independent of the primary SSO configuration, to provide continuity in the event of an IdP outage or misconfiguration. The break-glass account authenticates with a strong password and a hardware-backed MFA factor, is issued to a named administrator, and is subject to heightened monitoring — every authentication with the break-glass account generates a real-time notification to the tenant's security contact list.

Break-glass accounts are not intended for routine use. The platform emits a reminder notification quarterly if a tenant's break-glass account has not been exercised in the preceding 90 days, prompting the administrator to complete a short recovery drill (log in, verify access, log out) so that the credentials remain usable when needed.

## Step-up authentication for sensitive operations

Certain operations — creating or revoking an API key, modifying the SSO configuration itself, exporting audit data for a date range exceeding 30 days — require step-up authentication even within an existing session. Step-up authentication prompts the user to re-present an MFA factor within the last 5 minutes of wall-clock time. The requirement holds regardless of the user's role; a tenant administrator who has been signed in for the last six hours will be prompted for MFA before creating a new API key.

Step-up is implemented through the IdP where the IdP supports the `acr_values` parameter of OIDC or the `AuthnContextClassRef` element of SAML. For IdPs that do not support explicit step-up, the platform enforces the requirement through its own secondary MFA challenge.

## Migration scenarios

Migrating SSO from one IdP to another — for example, because of a corporate acquisition — follows a dual-provider pattern. A new SAML or OIDC configuration is added alongside the existing one; users are granted access through either configuration for a defined transition window; the old configuration is disabled after verification that all active users have been migrated. The transition window is bounded by the tenant administrator's policy, typically between 30 and 90 days.

During the transition, audit logs record the configuration under which each session was established, so that the administrator has full visibility into migration progress. After the transition, the configuration change log retains the history of the migration for the audit retention period.
""",
    "dr_bcp_overview": """# Disaster Recovery and Business Continuity Overview

## Scope

This document describes the disaster recovery (DR) and business continuity (BC) posture for the multi-tenant SaaS platform, including the deployment topology, replication strategy, recovery objectives, exercise cadence, and backup policy. It is intended for customer procurement, compliance, and risk teams who need to assess the platform's resilience against planned unavailability, regional failures, and data loss scenarios.

## Deployment topology

The platform is deployed active-active across two AWS regions, us-east-1 (Northern Virginia) and us-west-2 (Oregon). Each region is a full deployment, able to serve customer traffic independently. Customer traffic is routed by a globally-scoped DNS service with latency-based routing policy, with the ability to shift traffic to a single region on demand in under two minutes for any reason — planned maintenance, elevated error rates from a region, or a formally declared regional event.

Within each region, the platform runs across three Availability Zones (AZs) with every stateful service replicated across a minimum of two AZs. Stateless services autoscale independently within each AZ. Load balancing within a region is handled by Application Load Balancers with cross-zone load balancing enabled.

## Replication strategy

Replication is chosen per data class to balance recovery-point objective (RPO) against operational cost and complexity.

Critical metadata — tenant configuration, identity, authorization state, encryption key references — is replicated synchronously across regions. Writes to this data class do not acknowledge success until the write has been committed in both regions. The tradeoff is higher per-write latency; the benefit is a zero-data-loss RPO for the most sensitive state.

Bulk customer data — application data, uploaded content, generated artifacts — is replicated asynchronously across regions with a target lag under 60 seconds. Asynchronous replication means that in a sudden regional loss, up to approximately 60 seconds of the most recent writes may be unrecoverable; the architecture accepts this tradeoff for the cost and latency benefit.

Logs and audit trails are forwarded in near-real-time to a centralized long-term storage tier and are retained according to the platform's logging retention policy (13 months for security-relevant logs, longer for specific audit categories).

## Recovery objectives

The platform publishes the following recovery objectives for Enterprise tier customers:

- Recovery Time Objective (RTO): 4 hours for a full regional loss, measured from the declaration of a regional event to the restoration of normal service in the unaffected region.
- Recovery Point Objective (RPO): 15 minutes for bulk customer data; zero for critical metadata; zero for completed and acknowledged audit events.
- Planned maintenance: scheduled maintenance windows are announced at least 14 days in advance and are limited to a maximum of two hours per quarter per tenant, exclusive of emergency maintenance.

These objectives are commitments, not historical observations. In practice, the observed behavior during recent DR exercises has been within a fraction of these targets, but the objectives represent the bounds against which the platform is managed.

## DR exercise cadence

DR exercises are conducted at least twice annually. Each exercise covers a defined scenario (for example, loss of one region for a simulated four-hour period) and validates the traffic-shift mechanism, the replication lag, and the operational runbooks. Exercise outcomes, including any discovered gaps and remediation actions, are documented and reviewed at the management review cycle.

At least one live-fire exercise per year involves shifting real traffic to a single region for a bounded window, to confirm that the platform's capacity planning and regional topology can in fact support full load from a single region. The results of the most recent live-fire exercise are available to Enterprise customers under non-disclosure through their Account Executive.

## Backup policy

Backups are a defense-in-depth layer below replication. Full backups of the primary customer-data stores are taken daily, with continuous point-in-time recovery available for a rolling 35-day window for relational data. Backup copies are stored in a separate AWS account from production, with distinct identity and access controls — to protect against the scenarios where replication is operating correctly but the data itself has been corrupted, deleted, or encrypted maliciously.

Backup integrity is tested monthly by performing an actual restore from a randomly-selected backup into a pre-production environment and running an integrity check against the restored data. Backups that fail this test are investigated as a priority incident.

Backups are retained for 90 days by default. Extended retention (up to seven years) is available on request for customers with specific regulatory obligations, subject to contractual amendment.

## Customer-initiated deletion and the deletion-replication interaction

When a customer initiates a deletion — either of specific data via the platform's APIs or of their entire tenant under contract termination — the deletion is propagated to both regions' replicas and recorded in the audit log. Cryptographic erasure of backups containing the deleted data occurs as those backups age out of the retention window; data is not recoverable from backups beyond the documented backup retention period regardless of the deletion request. Customers with extended retention should be aware that the extended retention window also applies to backups containing data that has subsequently been requested for deletion.

## Incident communication

In any regional event or security incident meeting the platform's published notification criteria, customers affected are communicated with through the channels configured on their tenant (email to designated contacts, platform status page, programmatic webhook if configured). The initial communication follows within 72 hours of confirmation of a qualifying event, with follow-up communications at documented milestones and a post-incident review delivered within 30 days.

## Failover runbook summary

The platform's failover runbook is the operational procedure executed when traffic must be shifted away from a region, whether due to a regional AWS event, a platform-level issue localized to one region, or a planned exercise. The runbook is reviewed quarterly and exercised at the documented cadence.

The high-level sequence is: (1) an on-call engineer receives an alert or a declaration of a qualifying event and opens an incident channel following the documented severity criteria; (2) the incident commander verifies the current replication lag, the health of the unaffected region, and the capacity headroom in the unaffected region; (3) the traffic-shift decision is made by the incident commander, supported by a written assessment; (4) the DNS routing policy is updated to direct all customer traffic to the unaffected region, a change that propagates within the DNS TTL plus a small buffer (in practice, under two minutes); (5) once traffic has shifted, the team monitors the unaffected region for saturation, latency, or error-rate anomalies and takes any load-balancing or scaling actions required; (6) the status page is updated and, where warranted, direct customer notifications are dispatched.

Recovery — the return of traffic to both regions once the affected region is healthy — follows a deliberate sequence rather than a sudden flip. Traffic is ramped back to the recovered region in stages (5%, 25%, 50%, 100%), with health checks at each stage and the ability to pause or roll back if any metric deviates from expectation.

## Dependency mapping

Business continuity depends on the continued operation of a small set of upstream dependencies. The dependency map is maintained by the platform operations team and reviewed at each management review.

Tier-1 dependencies — those whose outage would prevent customer access to the platform for the duration of the upstream outage — comprise the AWS regional control planes for the services composing the data plane, the identity provider used for workforce SSO, the DNS service used for customer traffic routing, and the certificate authority underlying public TLS. Each tier-1 dependency has a documented alternate or compensating arrangement where commercially and technically feasible.

Tier-2 dependencies — those whose outage would degrade but not prevent platform operation — comprise third-party observability and alerting systems, the ticketing system used by the operations function, and other supporting SaaS. Degradation scenarios are documented in the operational runbooks.

Customer-facing dependencies — the platform's third-party integrations where the customer tenant configures an external system to receive data from the platform or provide data to the platform — are not part of the platform's continuity scope, but the platform provides durable retry and backoff for outbound calls to reduce the visibility of short customer-side outages.

## Planned maintenance

Scheduled maintenance windows are announced via the platform's status page and, where customer-facing impact is expected, via email to the designated tenant contacts. The announcement provides at least 14 days' notice, specifies the impacted services and the expected duration, and links to further detail.

Most maintenance work is conducted without customer-visible impact, by using the active-active topology to drain traffic from the region being maintained while work proceeds. The published maintenance windows represent the bounds within which the maintenance may cause observable impact; in the typical case, no observable impact occurs.

## Regulatory and contractual considerations

Specific customer tenants are subject to regulatory or contractual requirements that exceed the platform's default continuity posture — for example, financial-services customers subject to DORA, or healthcare customers subject to HIPAA contingency-planning requirements. The platform's Enterprise tier accommodates contract-level commitments such as longer backup retention, additional region pairings for cross-region replication, and more frequent DR exercise participation.

Customers whose regulatory obligations require participation in the DR exercise program, for example to satisfy an examiner's requirement that the customer has verified the continuity arrangements of a material third-party service provider, may coordinate with their Account Executive to be granted access to summarized results and, in some cases, live participation in a scheduled exercise.
""",
}

PRIOR_RFP_BODIES: dict[str, str] = {
    "acme_financial_soc2_answer": """# Prior RFP answer — Acme Financial: SOC 2 scope

Approved for outbound use on 2025-07-01.

**Question:** Describe your SOC 2 Type II audit scope and the trust principles covered.

**Approved answer:** Our SOC 2 Type II audit covers the Security, Availability, and Confidentiality trust principles. The most recent audit period ended June 2025. The report is available under NDA through your Account Executive.

**Note:** This answer was approved against the SOC 2 Type II report that was current at the time of approval. Confirm with the current compliance store before reusing verbatim.
""",
    "bluebird_encryption_answer": """# Prior RFP answer — Bluebird Insurance: encryption at rest

Approved for outbound use on 2025-08-15.

**Question:** How is customer data encrypted at rest? Specify algorithm, key length, and key management approach.

**Approved answer:** Customer data at rest is encrypted with AES-256-GCM. Keys are managed in AWS KMS under the service organization's account by default. Enterprise customers may bring their own CMK, held in their own AWS account. Keys are rotated annually by default, with on-demand rotation available.

**Note:** This answer was approved against the encryption architecture documentation current at the time of approval.
""",
    "globex_fedramp_answer": """# Prior RFP answer — Globex: FedRAMP status

Approved for outbound use on 2026-02-01.

**Question:** What is your roadmap for FedRAMP Moderate authorization, and when will it be available?

**Approved answer:** FedRAMP Moderate ATO assessment is in progress. A Third-Party Assessment Organization has been engaged and assessment work is underway. We do not publish a committed authorization date; timeline is set by the sponsoring agency's Authorizing Official. Please contact your Account Executive for the current status and any interim posture available to federal customers.

**Note:** FedRAMP status changes over time. Re-confirm against the compliance store before reusing.
""",
    "northwind_sso_answer": """# Prior RFP answer — Northwind Capital: SSO protocols

Approved for outbound use on 2026-01-15.

**Question:** Which SSO protocols do you support?

**Approved answer:** We support SAML 2.0 and OpenID Connect as identity provider protocols for customer-initiated workforce SSO. OAuth 2.0 Authorization Code flow with PKCE is used for our native desktop and mobile integrations. SCIM 2.0 is supported for automated user provisioning and deprovisioning.
""",
}

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_pdf(dest: Path, title: str, blocks: list[dict[str, Any]]) -> None:
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], spaceBefore=18, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], spaceBefore=12, spaceAfter=8)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10.5, leading=15, spaceAfter=8)

    doc = SimpleDocTemplate(
        str(dest),
        pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
        title=title,
    )
    flow: list[Any] = [Paragraph(title, h1), Spacer(1, 0.15 * inch)]
    for block in blocks:
        if "h" in block:
            flow.append(Paragraph(block["h"], h2))
        elif "p" in block:
            flow.append(Paragraph(block["p"], body))
        elif "table" in block:
            t = Table(block["table"], repeatRows=1, colWidths=[1.1 * inch, 3.9 * inch, 1.4 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            flow.append(t)
            flow.append(Spacer(1, 0.12 * inch))
        elif block.get("page_break"):
            flow.append(PageBreak())
    doc.build(flow)


def _write_metadata_sidecar(doc_path: Path, attrs: dict[str, Any]) -> None:
    sidecar = doc_path.with_name(doc_path.name + ".metadata.json")
    sidecar.write_text(json.dumps({"metadataAttributes": attrs}, indent=2))


def _build_attrs(source_type: str, src_json: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "document_id": src_json["document_id"],
        "source_type": source_type,
        "topic_ids":   list(src_json.get("topic_ids", [])),
    }
    if "updated_at" in src_json:
        attrs["updated_at"] = src_json["updated_at"]
    if "approved_at" in src_json:
        attrs["approved_at"] = src_json["approved_at"]
    # sme-approved entries carry approver + expiry + original question text so
    # the retriever can populate PriorAnswerMatch fields from sidecar metadata
    # without parsing the markdown body.
    for extra_key in ("approved_by", "expires_on", "question_text"):
        if src_json.get(extra_key):
            attrs[extra_key] = src_json[extra_key]
    return attrs


def _generate_compliance(src_path: Path) -> tuple[Path, dict[str, Any]]:
    data = json.loads(src_path.read_text())
    doc_id = data["document_id"]
    blocks = COMPLIANCE_BODIES.get(doc_id)
    if blocks is None:
        blocks = [{"h": data.get("title", doc_id)}, {"p": data.get("excerpt", "")}]
    dest = DST / "compliance" / f"{doc_id}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _render_pdf(dest, data.get("title", doc_id), blocks)
    return dest, data


def _generate_product_doc(src_path: Path) -> tuple[Path, dict[str, Any]]:
    data = json.loads(src_path.read_text())
    doc_id = data["document_id"]
    body = PRODUCT_DOCS_BODIES.get(doc_id) or f"# {data.get('title', doc_id)}\n\n{data.get('excerpt', '')}\n"
    dest = DST / "product-docs" / f"{doc_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return dest, data


def _generate_prior_rfp(src_path: Path) -> tuple[Path, dict[str, Any]]:
    data = json.loads(src_path.read_text())
    doc_id = data["document_id"]
    body = PRIOR_RFP_BODIES.get(doc_id) or f"# {data.get('title', doc_id)}\n\n{data.get('excerpt', '')}\n"
    dest = DST / "prior-rfps" / f"{doc_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return dest, data


def _generate_sme_approved(src_path: Path) -> tuple[Path, dict[str, Any]]:
    """Format SME-approved Q&A as markdown. The embedding model sees the full
    Q + A text, so semantic similarity against an inbound RFP question is
    high-quality — this is what powers the real cosine-based H signal.
    """
    data = json.loads(src_path.read_text())
    doc_id = data["document_id"]
    body = (
        f"# SME-Approved Q&A — {doc_id}\n\n"
        f"**Approved:** {data.get('approved_by', 'unknown')} on {data.get('approved_at', '?')}  \n"
        f"**Topics:** {', '.join(data.get('topic_ids', []))}  \n"
        f"**Corroborated by:** {', '.join(data.get('corroborated_by', [])) or '—'}\n\n"
        f"## Question\n\n{data.get('question_text', '').strip()}\n\n"
        f"## Answer\n\n{data.get('answer_text', '').strip()}\n"
    )
    dest = DST / "sme-approved" / f"{doc_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return dest, data


GENERATORS = {
    "compliance":   _generate_compliance,
    "product-docs": _generate_product_doc,
    "prior-rfps":   _generate_prior_rfp,
    "sme-approved": _generate_sme_approved,
}


def main() -> None:
    if not SRC.exists():
        raise SystemExit(
            f"Source corpus missing at {SRC}. Run scripts/generate_synthetic_data.py first."
        )

    emitted = 0
    for prefix, gen in GENERATORS.items():
        src_dir = SRC / prefix
        if not src_dir.exists():
            print(f"  SKIP {prefix}/ — no source sidecars")
            continue
        for src in sorted(src_dir.glob("*.json")):
            dest, data = gen(src)
            attrs = _build_attrs(SOURCE_TYPE_BY_PREFIX[prefix], data)
            _write_metadata_sidecar(dest, attrs)
            rel = dest.relative_to(ROOT)
            print(f"  wrote {rel}  ({len(attrs['topic_ids'])} topic_ids)")
            emitted += 1

    print(f"\nDone. {emitted} documents + sidecars under {DST.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
