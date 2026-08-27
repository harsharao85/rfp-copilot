#!/usr/bin/env python3
"""Expand compact corpus sidecars into realistic documents for Bedrock KB ingestion.

Inputs:   data/corpus/{compliance,product-docs,prior-rfps,sme-approved}/*.json
Outputs:  data/corpus-real/<prefix>/<doc_id>.<ext>
          data/corpus-real/<prefix>/<doc_id>.<ext>.metadata.json

- compliance/*.json   → multi-page PDF (reportlab) + .pdf.metadata.json
- product-docs/*.json → long-form markdown         + .md.metadata.json
- prior-rfps/*.json   → questionnaire extract md   + .md.metadata.json
- sme-approved/*.json → Q&A markdown (H-signal)    + .md.metadata.json

Metadata sidecar format (required verbatim by Bedrock Knowledge Bases):
  {"metadataAttributes": {"document_id": str,
                          "source_type": "compliance_cert" | "product_doc" | "prior_rfp" | "sme_approved_answer",
                          "updated_at" | "approved_at": "YYYY-MM-DD",
                          "topic_ids": ["..."],
                          ...}}

Sidecars additionally carry the flat Provenance attributes that
lambdas/retriever/handler.py:_hydrate_provenance() feeds into
shared.provenance.Provenance.from_sidecar() — audience, customer_name,
customer_id, questionnaire_name, answered_at, approved_by, expires_on. That
is what turns a citation from a bare document id into a source line an SME can
act on, and what lets the divergence detector group prior answers by customer.

The source data/corpus/ tree is untouched. Regenerate with:
  python scripts/generate_corpus_documents.py

Content note: every organisation, auditor, person and certificate number below
is invented. The corpus describes a fictitious cloud email-encryption service.

Rule hygiene (deliberate, do not "improve"): the product documents that back
the GREEN questions in data/incoming/echoworx-caiq-v4-40q.xlsx avoid the
vocabulary that lambdas/hard_rules/rules.py demotes on — "audit", "certified",
"compliant", named frameworks, pricing words, RTO/RPO, "will support", "by
<year>". Those documents say "event record" rather than "audit log" for that
reason. A green cell that silently turns amber because a whitepaper said
"audit log" is a fixture bug, not a policy finding.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "corpus"
DST = ROOT / "data" / "corpus-real"

SOURCE_TYPE_BY_PREFIX: dict[str, str] = {
    "compliance":    "compliance_cert",
    "product-docs":  "product_doc",
    "prior-rfps":    "prior_rfp",
    "sme-approved":  "sme_approved_answer",
}

SOURCE_ID_BY_PREFIX: dict[str, str] = {
    "compliance":    "compliance_store",
    "product-docs":  "product_docs",
    "prior-rfps":    "prior_rfps",
    "sme-approved":  "sme_approved_answers",
}

# ---------------------------------------------------------------------------
# compliance/ — rendered as PDFs. These back the AMBER compliance questions, so
# framework names and attestation language are expected here.
# ---------------------------------------------------------------------------

COMPLIANCE_BODIES: dict[str, list[dict[str, Any]]] = {
    "soc2_type2_2026": [
        {"h": "Report identification"},
        {"p": "This document summarises the System and Organization Controls 2 (SOC 2) Type II report on the suitability of design and operating effectiveness of controls relevant to the Trust Services Criteria for Security, Availability and Confidentiality. The report period runs from 1 April 2025 to 31 March 2026. The report was issued on 15 June 2026 by an independent CPA firm registered with the AICPA peer review program. The summary is prepared for prospective customers; the full report is released under a mutual non-disclosure agreement."},
        {"p": "This summary supersedes the FY2024 summary in its entirety. Where an earlier report is held on file by a customer, the FY2026 report is the only one that describes the control environment as it operates today."},
        {"h": "System boundary"},
        {"p": "The system in scope is the cloud email encryption service: the inbound and outbound encryption gateway, the policy engine that selects a delivery method per message, the recipient portal used where no interoperable delivery path exists, the key management plane, and the tenant administration plane through which customer administrators configure policy and review event records. Corporate information technology, the marketing website and the internal customer relationship system are outside the boundary."},
        {"p": "Subservice organisations are treated under the carve-out method. Infrastructure hosting, managed key storage and the workforce identity provider are carved out; the complementary subservice organisation controls expected of each are listed in Appendix B. Customers are expected to obtain and review those providers' own reports as part of their supply chain assessment."},
        {"h": "Trust Services Criteria coverage"},
        {"p": "The Common Criteria CC1.1 through CC9.2 are addressed in full. Availability criteria A1.1 through A1.3 are addressed against the published service commitment of 99.95% monthly uptime for the Enterprise plan. Confidentiality criteria C1.1 and C1.2 address classification, handling, retention and disposal of message content and metadata. Processing Integrity and Privacy criteria are not within the scope of this report; conformance to ISO/IEC 27018:2019 is attested separately."},
        {"h": "Control activities and test results"},
        {"table": [
            ["Criterion", "Control activity", "Result"],
            ["CC6.1", "Access to production requires federated sign-in with a hardware-backed second factor. Standing access is reviewed quarterly.", "No exceptions noted"],
            ["CC6.6", "Administrative sessions to production are brokered through a recorded access path; direct network reachability is not available.", "No exceptions noted"],
            ["CC6.7", "Message bodies, attachments and metadata at rest are encrypted with AES-256-GCM under per-message data keys wrapped by a tenant key-encryption key.", "No exceptions noted"],
            ["CC6.7", "External connections require TLS 1.2 or above with forward-secret cipher suites. TLS 1.0 and 1.1 are refused at every listener.", "No exceptions noted"],
            ["CC7.2", "Security-relevant events are centralised, correlated and retained for 400 days, with continuous monitoring by a 24x7 operations function.", "No exceptions noted"],
            ["CC7.3", "The incident response procedure is exercised twice per period, once as a tabletop and once as a live scenario.", "No exceptions noted"],
            ["CC7.4", "Confirmed incidents affecting a tenant are notified to that tenant's registered security contacts within 72 hours of confirmation.", "No exceptions noted"],
            ["CC8.1", "Changes are peer reviewed, statically analysed and scanned for vulnerable dependencies before merge; emergency changes are reviewed retrospectively within five business days.", "No exceptions noted"],
            ["A1.2", "Each residency zone is served from two regions in an active-active configuration with continuous replication of tenant state.", "No exceptions noted"],
            ["C1.1", "Message content is classified at ingestion and handled under the retention schedule elected by the tenant.", "No exceptions noted"],
            ["C1.2", "Deletion requests are executed within thirty days and confirmed to the requesting tenant in writing.", "No exceptions noted"],
        ]},
        {"h": "Independent service auditor's opinion"},
        {"p": "In the auditor's opinion, in all material respects, the controls stated in management's description were suitably designed and operated effectively throughout the period 1 April 2025 to 31 March 2026 to provide reasonable assurance that the service commitments and system requirements would be achieved based on the applicable trust services criteria. The opinion is unqualified. No exceptions were noted in the tests of controls described in Section 4 of the full report."},
        {"h": "Incident notification commitment"},
        {"p": "Management commits that where an event has been confirmed to have affected the confidentiality, integrity or availability of a tenant's data, that tenant's registered security contacts are notified within 72 hours of confirmation. The initial notification states what is known, what is not yet known, the immediate containment position and the point of contact for follow-up. A written post-incident review is delivered within thirty days of incident closure. Events prevented by controls before impact, and events confined to internal systems without effect on tenant isolation, are not notifiable under this commitment; they are recorded and reviewed internally."},
        {"h": "Complementary user entity controls"},
        {"p": "Achievement of the trust services criteria depends in part on controls operated by customers. Customers are responsible for: provisioning and deprovisioning their own personnel in their identity provider, and keeping the federation configuration current; configuring tenant policy, including which delivery methods are permitted to which recipient domains; enforcing a second factor on their own administrators; reviewing the event records exported from the tenant plane on a cadence aligned to their own obligations; and reporting suspected security events affecting their tenant through the documented channel rather than through general support."},
        {"h": "Bridge letter"},
        {"p": "A bridge letter covering 1 April 2026 to the date of this summary is available under non-disclosure. It affirms that no material change to the control environment has occurred between the end of the report period and the date of issue. The next Type II report is expected to cover the twelve months ending 31 March 2027."},
        {"h": "Appendix A — principal service commitments"},
        {"p": "Commitment 1: message content is encrypted at rest and in transit using algorithms and key lengths consistent with current guidance from recognised standards bodies. Commitment 2: access to tenant content by service personnel requires a recorded business justification, federated sign-in with a second factor, and time-bounded elevation. Commitment 3: customer-initiated deletion is completed within thirty days. Commitment 4: confirmed incidents affecting a tenant are notified within 72 hours. Commitment 5: the Enterprise plan maintains 99.95% monthly uptime measured as a rolling monthly average, excluding maintenance announced at least fourteen days in advance."},
        {"h": "Appendix B — complementary subservice organisation controls"},
        {"p": "The infrastructure provider is expected to maintain physical and environmental controls, hypervisor isolation and regional availability consistent with its own SOC 2 Type II report. The managed key service provider is expected to maintain the confidentiality and availability of wrapped key material and to log every key operation. The workforce identity provider is expected to maintain the integrity and availability of workforce authentication. Customers should obtain these providers' reports directly."},
    ],
    "iso27001_soa_extract_2026": [
        {"h": "Certificate particulars"},
        {"p": "The information security management system operated by the service organisation has been assessed against ISO/IEC 27001:2022 and found to conform. Certificate ISMS-2026-04417 was issued on 10 February 2026 and is valid to 9 February 2029, subject to satisfactory surveillance in each intervening year. The certification body is accredited under the International Accreditation Forum multilateral recognition arrangement, and the certificate is verifiable in the accreditation body's public register."},
        {"h": "Scope statement"},
        {"p": "The certified scope is: the design, development, operation and support of the cloud email encryption service, including the encryption gateway, the policy engine, the recipient portal, the key management plane, the tenant administration plane, and the engineering and operational functions that build and run them. The scope covers all residency zones in which the service is offered. Corporate functions with no role in operating the service are excluded from scope, and the exclusion is recorded with justification in the Statement of Applicability."},
        {"h": "Statement of Applicability — extract"},
        {"p": "Statement of Applicability version 6.1, dated 22 January 2026, evaluates all 93 controls in Annex A of ISO/IEC 27001:2022. Ninety are applicable and implemented. Three are declared not applicable, each with a recorded justification: A.7.11 (supporting utilities) and A.7.12 (cabling security), both physical controls carried by the carved-out infrastructure provider and not operable by the service organisation; and A.5.7 (threat intelligence) is applicable but limited to commercially available and open sources, as classified national sources are not lawfully available to a private-sector provider in the operating jurisdictions."},
        {"table": [
            ["Annex A control", "Implementation summary", "Status"],
            ["A.5.15 Access control", "Unique identities, federated sign-in, second factor for any access to tenant content, entitlements derived from role, quarterly review.", "Implemented"],
            ["A.5.23 Cloud services security", "Supplier assessment before onboarding, contractual security terms, annual reassessment, exit plan per supplier.", "Implemented"],
            ["A.5.29 Security during disruption", "Continuity arrangements per residency zone, exercised twice yearly, objectives published to customers.", "Implemented"],
            ["A.5.30 ICT readiness for continuity", "Recovery objectives set per component, verified in exercise, deviations tracked to closure.", "Implemented"],
            ["A.8.9 Configuration management", "Infrastructure defined as code, drift detected continuously, out-of-band change blocked by policy.", "Implemented"],
            ["A.8.12 Data leakage prevention", "Egress controls on production networks, content inspection on administrative export paths.", "Implemented"],
            ["A.8.16 Monitoring activities", "Central event pipeline, detection rules under a documented lifecycle, 24x7 triage with severity-based response times.", "Implemented"],
            ["A.8.24 Use of cryptography", "Cryptography policy setting approved algorithms, minimum key sizes and protocol versions; reviewed annually against published guidance.", "Implemented"],
            ["A.8.28 Secure coding", "Secure coding standard, mandatory peer review, static analysis and dependency analysis gating merge.", "Implemented"],
            ["A.5.7 Threat intelligence", "Commercial and open sources only; classified sources unavailable to the organisation.", "Applicable, limited"],
            ["A.7.11 Supporting utilities", "Carried by the infrastructure provider under the carve-out.", "Not applicable"],
            ["A.7.12 Cabling security", "Carried by the infrastructure provider under the carve-out.", "Not applicable"],
        ]},
        {"h": "Surveillance and internal audit"},
        {"p": "The certification cycle comprises the initial Stage 1 and Stage 2 assessment, annual surveillance in years two and three, and recertification at the end of year three. Surveillance covers a rotating subset of applicable controls selected by the external auditor on the basis of risk and prior findings; in practice roughly a third of applicable controls are examined at each surveillance visit. The internal audit programme covers the whole management system every twelve months, independently of the external cycle, and its findings feed the management review."},
        {"h": "Nonconformities"},
        {"p": "The most recent surveillance raised two minor nonconformities and no major nonconformities. The first concerned a supplier reassessment completed nine days after its due date; corrected by moving reassessment scheduling into the same tracking system as the rest of the supplier lifecycle. The second concerned a detection rule retired without the retirement rationale being recorded; corrected by making the rationale field mandatory in the rule lifecycle workflow. Both corrective actions have been implemented and verified effective."},
        {"h": "Management review"},
        {"p": "Executive management reviews the management system quarterly. Each review considers the risk register and treatment plan, performance against the defined security metrics, the status of internal and external audit findings, customer-raised security observations, and resourcing. Minutes are retained, and a redacted extract is available to customers under non-disclosure on request."},
    ],
    "iso27018_attestation_2026": [
        {"h": "Attestation of conformance"},
        {"p": "This attests that the controls operated by the service organisation for the processing of personally identifiable information in public cloud have been assessed against ISO/IEC 27018:2019 and found to conform. The assessment was conducted alongside the ISO/IEC 27001:2022 surveillance activity in February 2026 by the same accredited certification body, and covers the same certified scope."},
        {"h": "Why this standard, and what it adds"},
        {"p": "ISO/IEC 27001 establishes the management system. ISO/IEC 27018 layers onto it a specific set of obligations for an organisation acting as a processor of personally identifiable information in public cloud. The obligations that customers most often ask about are the ones addressed below: processing only on documented instruction, restrictions on secondary use, disclosure of sub-processors, notification of law enforcement requests, and return or deletion of data at the end of the relationship."},
        {"h": "Processing on documented instruction"},
        {"p": "The service organisation processes personally identifiable information contained in message content and metadata solely to deliver the contracted service and solely on the documented instruction of the customer, as recorded in the Data Processing Agreement and the tenant's configured policy. There is no use of customer content for marketing, for product analytics, or for the training of machine learning models. Where the organisation is required by law to process outside the customer's instruction, that requirement is recorded and, unless prohibited, communicated to the customer."},
        {"h": "Sub-processing and disclosure"},
        {"p": "Sub-processors are disclosed in a published register that names each entity, the processing it performs, and the country in which that processing occurs. Customers are notified at least thirty days before a new sub-processor begins processing, and may object within that window under the terms of the Data Processing Agreement. Each sub-processor is bound by contractual terms no less protective than those the organisation owes its customers, and is reassessed annually."},
        {"h": "Government and law enforcement access"},
        {"p": "Requests from law enforcement or government for customer content are refused unless the request is legally binding on the organisation. Where a request is legally binding, the organisation seeks to redirect the requester to the customer. Where redirection fails and notification is not legally prohibited, the customer is notified before disclosure so that it may seek relief. The organisation publishes an aggregate transparency figure annually. Because message bodies at rest are encrypted under tenant key-encryption keys, a tenant holding its own root key material materially constrains what could be disclosed in response to any such request."},
        {"h": "Residency and cross-border transfer"},
        {"p": "Personally identifiable information is processed in the residency zone elected by the tenant at provisioning. Cross-border transfer occurs only where the tenant enables a feature that requires it, or where support access is granted on a per-session, named-approval basis. Transfers out of the European Economic Area rely on the 2021 Standard Contractual Clauses supplemented by a transfer impact assessment; transfers out of the United Kingdom rely on the International Data Transfer Addendum."},
        {"h": "Return and deletion"},
        {"p": "On termination, the customer may export its data through the documented export path for a period of thirty days. After that period, or immediately on written instruction, content is deleted. Deletion is confirmed in writing. Content held in backup images is rendered unrecoverable as those images age out of the retention window, which does not exceed thirty-five days; the confirmation of deletion states this explicitly rather than implying instantaneous erasure everywhere."},
    ],
    "pentest_exec_summary_2026": [
        {"h": "Engagement summary"},
        {"p": "An independent testing firm conducted a grey-box penetration test of the cloud email encryption service between 6 April 2026 and 24 April 2026. The engagement covered the internet-facing encryption gateway and its SMTP listeners, the recipient portal including its authentication and session handling, the tenant administration plane and its API, and the key management plane's externally reachable interfaces. Testers were provided with tenant administrator credentials for two purpose-built tenants and with architecture documentation, but not with source code."},
        {"p": "The engagement was scoped as an annual assurance activity and was conducted under a signed rules-of-engagement document. Testing was performed against a dedicated pre-production environment configured to match production, with a controlled subset of tests replayed against production under supervision."},
        {"h": "Findings summary"},
        {"table": [
            ["Ref", "Severity", "Finding", "Status"],
            ["PT-2026-01", "Medium", "Recipient portal session cookie lacked the SameSite attribute on one legacy delivery path, permitting a cross-site request under a narrow set of browser conditions.", "Remediated and retested 21 Apr 2026"],
            ["PT-2026-02", "Medium", "The administration API accepted an unbounded page-size parameter on the event export endpoint, allowing an authenticated administrator to exhaust worker capacity for that tenant.", "Remediated and retested 23 Apr 2026"],
            ["PT-2026-03", "Low", "Verbose error response on a malformed S/MIME certificate disclosed the internal library version.", "Remediated and retested 21 Apr 2026"],
            ["PT-2026-04", "Low", "Password reset response timing differed measurably between known and unknown recipient addresses.", "Remediated and retested 23 Apr 2026"],
            ["PT-2026-05", "Low", "Content Security Policy on the portal permitted inline style attributes.", "Remediated and retested 24 Apr 2026"],
            ["PT-2026-06", "Low", "One dependency in the gateway image was two patch releases behind, with no known exploitable path in this configuration.", "Remediated and retested 24 Apr 2026"],
        ]},
        {"h": "Findings not identified"},
        {"p": "No critical or high-severity findings were identified. Specifically, the testers were unable to achieve cross-tenant access to message content, to recover plaintext message bodies from storage or from intercepted transport, to bypass the second-factor requirement on administrative sign-in, or to cause the policy engine to select a weaker delivery method than tenant policy permitted. Attempts to force fallback from enforced TLS to opportunistic TLS by manipulating partner-domain discovery were unsuccessful."},
        {"h": "Remediation and retest"},
        {"p": "All six findings were remediated and retested within the engagement window; the testing firm issued a clean retest letter dated 28 April 2026. The two medium findings were fixed within seven and nine days of report respectively, inside the thirty-day internal target for medium severity. The organisation's internal targets are seven days for critical, thirty days for high, ninety days for medium and best-effort for low, measured from report date to verified fix."},
        {"h": "Availability of the full report"},
        {"p": "This executive summary may be shared with prospective customers on request. The full technical report, including reproduction steps and the retest letter, is released under a mutual non-disclosure agreement through the account team. The organisation does not release the full report without a non-disclosure agreement in place, because reproduction steps for a fixed finding remain useful to an attacker against unpatched forks and integrations."},
        {"h": "Supply chain assurance"},
        {"p": "The engagement was complemented by the standing software supply chain controls: a signed CycloneDX software bill of materials is produced for every deployable artifact, dependency composition analysis runs on every change and nightly against the released set, and container images are scanned before promotion. The bill of materials for a given release is available to customers under non-disclosure and is the basis on which the organisation answers component-level vulnerability enquiries."},
    ],
    "soc2_type2_2024_expired": [
        {"h": "SUPERSEDED — retained for history only"},
        {"p": "This is the summary of the FY2024 SOC 2 Type II report. It has been superseded by the FY2026 report issued 15 June 2026 and must not be cited as current evidence of the control environment. It is retained so that a customer holding the FY2024 report on file can reconcile what has changed. Any answer that relies on this document without also citing the current report is wrong."},
        {"h": "Report identification"},
        {"p": "SOC 2 Type II report on the suitability of design and operating effectiveness of controls relevant to the Trust Services Criteria for Security and Availability. Report period 1 April 2023 to 31 March 2024. Issued 30 June 2024 by an independent CPA firm. The report was valid for reliance until 30 June 2025 under the customary twelve-month reliance convention."},
        {"h": "Scope differences against the current report"},
        {"p": "The FY2024 report differs from the FY2026 report in three material respects. First, Confidentiality was not in scope in FY2024; it was added in FY2026. Second, the tenant administration plane was assessed as a single component in FY2024, whereas FY2026 assesses the administration plane and the key management plane separately. Third, the recipient portal was in scope in both periods, but the interoperable gateway-to-gateway delivery path was introduced after the FY2024 period and is therefore covered only by the FY2026 report."},
        {"h": "Control activities as tested in FY2024"},
        {"table": [
            ["Criterion", "Control activity", "Result"],
            ["CC6.1", "Access to production requires federated sign-in with a second factor.", "No exceptions noted"],
            ["CC6.7", "Message content at rest is encrypted with AES-256-GCM.", "No exceptions noted"],
            ["CC7.2", "Security-relevant events are centralised and retained for 365 days.", "No exceptions noted"],
            ["CC7.4", "Confirmed incidents affecting a tenant are notified within 72 hours of confirmation.", "No exceptions noted"],
            ["A1.2", "Each residency zone is served from two regions.", "No exceptions noted"],
        ]},
        {"h": "Why this document is still in the corpus"},
        {"p": "Superseded evidence is the single largest source of wrong questionnaire answers. Removing it from the corpus entirely would hide the problem rather than solve it: a reviewer who has the old report in their own files will still ask about it. It is retained here with an expiry already in the past so that the pipeline treats it as expired evidence, marks any citation of it accordingly, and prefers the current report against the same question."},
    ],
    "dpa_art28_subprocessors_2026": [
        {"h": "Standard Data Processing Agreement"},
        {"p": "The standard Data Processing Agreement is offered under Article 28 of Regulation (EU) 2016/679 and its United Kingdom equivalent. It is executed as an annex to the master agreement and does not require negotiation for the great majority of customers. Version 2026.05 was published on 20 May 2026 and is the version offered to all new and renewing customers."},
        {"h": "Roles and instructions"},
        {"p": "The customer is the controller and the service organisation is the processor in respect of personal data contained in message content, message metadata and administrative event records. The agreement records the subject matter, duration, nature and purpose of processing, the types of personal data and the categories of data subjects, in the form Article 28(3) requires. The organisation processes only on the customer's documented instruction, which comprises the agreement itself and the tenant configuration the customer sets."},
        {"h": "Article 28(3) obligations"},
        {"table": [
            ["Article 28(3)", "Obligation", "Where addressed"],
            ["(a)", "Process only on documented instruction, including for transfers.", "Clause 3, Clause 9"],
            ["(b)", "Ensure persons authorised to process are bound by confidentiality.", "Clause 4"],
            ["(c)", "Take measures required by Article 32.", "Clause 5 and Annex II"],
            ["(d)", "Respect conditions for engaging another processor.", "Clause 6 and Annex III"],
            ["(e)", "Assist the controller in responding to data subject requests.", "Clause 7"],
            ["(f)", "Assist with Articles 32 to 36, including breach notification.", "Clause 8"],
            ["(g)", "Delete or return personal data at the end of provision.", "Clause 10"],
            ["(h)", "Make available information to demonstrate compliance and allow audits.", "Clause 11"],
        ]},
        {"h": "Sub-processor register, version 2026.05"},
        {"table": [
            ["Sub-processor", "Purpose", "Processing location"],
            ["Fictitious infrastructure provider", "Compute, storage and network for the service", "Elected residency zone"],
            ["Fictitious managed key service", "Storage and use of wrapped key material", "Elected residency zone"],
            ["Fictitious observability provider", "Operational telemetry; no message content", "European Union"],
            ["Fictitious transactional messaging provider", "Delivery of recipient notification messages", "European Union, United States"],
            ["Fictitious support platform provider", "Support ticketing and correspondence", "European Union"],
            ["Fictitious identity provider", "Workforce authentication for service personnel", "European Union"],
        ]},
        {"h": "Sub-processor change process"},
        {"p": "The register is published and versioned. Customers may subscribe to change notifications. A new sub-processor is announced at least thirty days before it begins processing, and the announcement states the purpose and the processing location. A customer that objects on reasonable data protection grounds within the notice period may raise the objection under Clause 6; where the objection cannot be resolved, the customer may terminate the affected service without penalty for the remainder of the term."},
        {"h": "International transfer"},
        {"p": "Where personal data is transferred out of the European Economic Area, the 2021 Standard Contractual Clauses apply in the controller-to-processor module, supplemented by a transfer impact assessment which is available on request. Where personal data is transferred out of the United Kingdom, the International Data Transfer Addendum applies. Customers electing the European Union or United Kingdom residency zones and not enabling cross-zone features will not have message content transferred outside that zone in the ordinary operation of the service."},
        {"h": "Assistance with data subject rights"},
        {"p": "The organisation assists the controller with requests for access, rectification, erasure, restriction, portability and objection. In practice most such requests are satisfied by the controller directly through the tenant administration plane, which supports search and export scoped to a data subject and supports deletion. Where a request cannot be satisfied through the tenant plane, the organisation assists on request within the timescales the agreement sets."},
        {"h": "Breach notification"},
        {"p": "The organisation notifies the controller without undue delay and in any event within 72 hours of becoming aware of a personal data breach affecting that controller's personal data, providing the information Article 33(3) requires to the extent it is then available, and supplementing it as further information becomes available. The notification goes to the security contacts registered against the tenant."},
    ],
}

# ---------------------------------------------------------------------------
# product-docs/ — long-form markdown. Chunked on "## " headings by the
# retrieval stub in scripts/verify_fixture.py, and by Bedrock's default
# chunking in the deployed path.
# ---------------------------------------------------------------------------

PRODUCT_DOCS_BODIES: dict[str, str] = {
    "encryption_at_rest_aes256gcm": """# Encryption at Rest — AES-256-GCM Envelope Design

## Scope of this document

This document describes how message bodies, attachments, message metadata and
derived search indexes are protected while stored by the cloud email encryption
service. It covers the algorithm and mode, the envelope structure, where each
kind of key lives, and what an attacker who obtains stored ciphertext can and
cannot do with it. Key rotation, bring-your-own-key and hardware security
module options are described in the key management document; this document
covers the data path.

## Algorithm and mode

Data at rest is sealed with AES-256 in Galois/Counter Mode, written throughout
as AES-256-GCM. Three properties drive that choice.

The mode is authenticated. GCM produces an authentication tag alongside the
ciphertext, and decryption fails closed if the tag does not verify. Silent
corruption of stored ciphertext, whether from a storage fault or from
tampering, surfaces as a decryption failure rather than as plausible-looking
garbage delivered to a recipient.

The key length is 256 bits. This is not because 128-bit AES is considered
breakable, but because the symmetric margin is the cheapest margin available:
the performance difference is small on modern hardware with AES-NI, and a
256-bit symmetric key retains a 128-bit security level against the best known
quantum search technique.

The nonce is 96 bits, generated from a cryptographically secure random source
per encryption operation and stored alongside the ciphertext. Nonce reuse under
the same key is catastrophic in GCM, so the design never reuses a data key
across more messages than the birthday bound allows — in practice each message
gets its own data key, which makes the question moot.

## Envelope structure

Each stored object carries an envelope with four parts: the AES-256-GCM
ciphertext, the 96-bit nonce, the 128-bit authentication tag, and the wrapped
data key. The wrapped data key is the data key itself, encrypted under the
tenant key-encryption key held in the managed key service.

The envelope is versioned. A version byte precedes the structure so that the
algorithm, the mode or the envelope layout can be changed without a flag day:
readers dispatch on the version, writers emit the current version, and a
background re-wrapping process moves older envelopes forward. This matters more
than it sounds. The common reason an organisation cannot move off a
cryptographic primitive is not that the new primitive is hard, it is that the
stored format has no version field and every reader must be changed at once.

## Where keys live and where they do not

Data keys exist in plaintext only inside process memory, only for the duration
of a single encrypt or decrypt operation, and are zeroed after use. They are
never written to disk in the clear, never logged, and never included in a
diagnostic capture — the structured event pipeline redacts the field by schema
rather than by pattern match, so a new field name cannot accidentally leak one.

Tenant key-encryption keys never leave the managed key service. The service
holds a handle, not the key. To decrypt anything, a process must be able to
call the unwrap operation on the tenant's key-encryption key, and every such
call is recorded by the key service independently of the platform's own event
records.

The practical consequence: an attacker who obtains a complete copy of stored
objects — every ciphertext, every nonce, every tag, every wrapped data key —
has no path to plaintext without also obtaining the ability to call the unwrap
operation against the tenant's key-encryption key.

## Separation by data class

Four data classes are separated under distinct key-encryption keys within a
tenant: message bodies and attachments, message metadata, derived search
indexes, and event records. Separation costs little and buys two things. A
policy change that restricts access to one class does not disturb the others.
And the retention schedules differ per class, so the keys age out on different
schedules without entangling them.

Derived search indexes deserve a note. An index over encrypted content is a
classic place where confidentiality leaks back out, because index structures
reveal term frequency. The service does not build a plaintext inverted index
over message bodies. Recipient-visible search operates on the metadata class —
sender, recipients, subject where the tenant permits it, timestamps — and that
class is sealed with the same envelope as everything else.

## Storage-layer redundancy

Sealing happens before the object reaches storage, so every replica of every
object is sealed. Replication between regions inside a residency zone moves
ciphertext, not plaintext; the receiving region has no more access to the
plaintext than the network in between does. Backup images are taken of the
sealed objects. There is no point in the storage path where an object exists in
the clear.

## What this control does not do

Encryption at rest closes a specific set of exposures: theft of storage media,
recovery of decommissioned hardware, an infrastructure-level misconfiguration
that exposes an object store, and a backup image that ends up somewhere it
should not. It does not defend against an attacker who has obtained a valid
tenant session, a service process that is entitled to unwrap the data key and
has been compromised, or a recipient who forwards a decrypted message onward.
Those exposures are addressed by access control, workload isolation and
delivery policy, described in their own documents. Stating the boundary
plainly is more useful to a reviewer than implying that one control covers
everything.
""",
    "encryption_in_transit_tls": """# Encryption in Transit — TLS Profile and SMTP Delivery

## Scope of this document

Two distinct transport problems live under the heading "encryption in transit"
for an email encryption service, and conflating them produces confused
answers. The first is the ordinary web and API transport between a client and
the service. The second is message delivery over SMTP to a recipient's mail
infrastructure, which the service does not control. This document covers both,
in that order.

## Web and API transport

Every externally reachable listener requires TLS 1.2 or TLS 1.3. TLS 1.0 and
TLS 1.1 are refused, not merely deprioritised: a client offering only those
versions receives a handshake failure. SSL 3.0 and earlier have not been
accepted for many years.

The accepted cipher suite list is short and forward-secret only. Under TLS 1.3
the service accepts TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256 and
TLS_AES_128_GCM_SHA256. Under TLS 1.2 it accepts ECDHE key exchange with ECDSA
or RSA authentication and an AEAD bulk cipher — the AES-GCM and
ChaCha20-Poly1305 families. Static RSA key exchange, CBC-mode suites and
anything without forward secrecy are absent from the list. The consequence of
requiring forward secrecy is that recorded traffic does not become readable
later if a long-term server private key is compromised.

HTTP Strict Transport Security is asserted on every public web endpoint with a
one-year max-age and the includeSubDomains directive, and the relevant domains
are on the browser preload lists. A plaintext request to a customer-facing
endpoint receives a redirect, and every subsequent request from that browser is
upgraded before it leaves the client.

Certificates are issued from a public certification authority with certificate
transparency logging, use ECDSA P-256 keys, and are renewed automatically well
ahead of expiry. Renewal failure is alerted on days before it could become
customer-visible, because an expired certificate is one of the few failures
that takes an otherwise healthy service completely offline.

## Service-to-service transport

Inside the service boundary, connections between components use mutual TLS.
Workload identity is established by short-lived certificates issued from an
internal private certification authority, with lifetimes measured in hours and
automatic renewal. A component presents its own certificate and validates its
peer's, so a process that has somehow obtained network reachability to an
internal endpoint still cannot talk to it without a valid workload identity.

## SMTP delivery to recipient infrastructure

This is where the honest answer is more complicated than the one a
questionnaire usually invites.

Ordinary SMTP negotiates TLS opportunistically. A sending server offers
STARTTLS, and if the receiving server does not support it, the message is
delivered in the clear. Opportunistic TLS is also downgradable by an active
network attacker who strips the STARTTLS advertisement. So "we use TLS for
email" is, on its own, a weak statement about email.

The service therefore supports three stronger positions, selectable by the
tenant per partner domain.

Enforced TLS requires a successful TLS negotiation with a validated
certificate for the named partner domain. If negotiation fails or validation
fails, the message is not delivered in the clear — it is queued, and if the
condition persists it falls back to the tenant's configured alternative method
rather than to plaintext.

MTA-STS publishes and consumes policy over HTTPS so that a sending server
learns, out of band, that a receiving domain requires TLS. This closes the
STARTTLS-stripping downgrade for domains that publish a policy. The service
publishes its own MTA-STS policy in enforce mode and honours published policies
on outbound delivery.

DANE uses DNSSEC-signed TLSA records to bind the expected certificate to the
receiving host, which closes the same downgrade for domains that have deployed
DNSSEC. The service validates TLSA records on outbound delivery where they are
present and DNSSEC validates.

## When transport security is not enough

Transport protects the hop. It does not protect the message once it lands. A
message delivered over enforced TLS into a recipient mailbox sits in that
mailbox in whatever state the recipient's own infrastructure keeps it.

Where the tenant's policy requires protection beyond the hop, the policy engine
selects a message-level method instead: S/MIME or OpenPGP where the recipient
publishes a usable certificate or key, or delivery into the recipient portal
where no interoperable path exists. The selection is a per-domain policy
decision the tenant administrator makes, not a per-message guess, and the
decision taken for each message is recorded in the delivery event stream.

## Reporting and verification

The service publishes SMTP TLS Reporting endpoints and consumes reports for its
own domains, so a partner's failure to negotiate is visible rather than silent.
Tenant administrators see, per partner domain, the negotiated version, the
selected suite and the validation outcome for delivery attempts. A reviewer who
wants to verify the external position independently can inspect the published
MTA-STS policy and the TLSA records directly.
""",
    "key_management_byok_hsm": """# Key Management — Hierarchy, Rotation, BYOK and HSM Options

## Scope of this document

Key management is where most of the real work in an encryption programme sits.
The algorithm choice is settled and uncontroversial; what varies between
providers, and what a security reviewer should press on, is where key material
lives, who can cause it to be used, how its use is recorded, and what happens
when the customer wants it destroyed. This document answers those questions.

## The key hierarchy

There are three levels.

A per-message data key seals one message envelope with AES-256-GCM. It is
generated per message from a cryptographically secure random source, exists in
plaintext only in process memory for the duration of a single operation, and is
stored only in wrapped form alongside the ciphertext it sealed.

A per-tenant key-encryption key wraps that tenant's data keys. It lives in a
managed key service and is never exported. Every wrap and unwrap operation
against it is recorded by the key service.

A root key protects the key-encryption keys. Where the tenant elects the
default, the root key is held by the service in a managed key service. Where
the tenant elects to bring its own, the root key is held by the tenant, and the
following sections describe what that changes.

Four key-encryption keys exist per tenant, one for each data class: message
bodies and attachments, message metadata, derived indexes, and event records. A
tenant may bring its own key for one class and leave the others under the
default arrangement.

## Rotation

The default rotation period for a key-encryption key is 365 days. Rotation
generates a new key version, marks it current for new wrap operations, and
retains prior versions for unwrap. Existing envelopes are not rewritten at
rotation time — a stored envelope names the key version that wrapped it, and
unwrapping an older envelope uses the version it names. This is what makes
rotation cheap enough to actually happen on schedule.

A tenant administrator may trigger rotation on demand from the tenant
administration plane, or through the administration API. On-demand rotation
takes effect within minutes and appears in the tenant's event stream. There is
no limit on how often a tenant may rotate, and no charge attached to doing so.

A background re-wrapping process moves envelopes forward onto the current key
version over time, so that retiring an old version eventually becomes possible.
Re-wrapping is rate-limited, resumable, and reports progress per tenant. A
tenant that needs every envelope moved onto a new version within a bounded
window — after a suspected compromise, for example — can request that the
re-wrap be prioritised, and progress is visible while it runs.

Data keys are not rotated, because each one is used exactly once.

## Bring your own key

Two arrangements are offered, and they differ in where the root key material
physically resides.

The first arrangement places a customer-managed key in the customer's own
account with the managed key service. The customer creates the key, grants the
service's workload identity permission to perform wrap, unwrap and describe
operations against it, and supplies the key identifier at tenant provisioning
or later through the administration plane. Two consequences follow. The
customer sees every use of its key in its own key service records, independent
of anything the platform reports. And the customer can revoke the grant
unilaterally at any moment, which takes effect immediately: in-flight
operations fail, and every subsequent read of sealed content returns an
authorisation error from the key service until the grant is restored.

The second arrangement uses an external key store, where root key material
resides in a hardware security module the customer operates, and the managed
key service reaches it over a documented interface. The cryptographic
operations occur inside the customer's hardware security module. This suits
organisations whose own policy or regulator requires that root key material
never reside in a cloud provider's custody at all.

## Hardware security module options

Three postures are available. Under the default, key-encryption keys are held
in the managed key service's shared, validated hardware backing. Under the
dedicated posture, the tenant is assigned an isolated hardware partition, not
shared with other tenants. Under the external posture described above, the
hardware belongs to the customer and sits outside the service boundary
entirely.

The dedicated and external postures are elected at provisioning and can be
changed later, though changing them requires a re-wrap of the affected key
hierarchy and is coordinated with the account team rather than being a
self-service toggle.

## Revocation, destruction and the shape of the guarantee

The customer holding root key material holds a real lever, and it is worth
stating exactly what that lever does and does not do.

Revoking the grant makes sealed content unreadable for as long as the
revocation stands. It is reversible. Nothing is destroyed.

Scheduling destruction of the root key is not reversible once the scheduled
window elapses. After destruction, sealed content protected under that key is
unrecoverable by the customer and by the service alike. This is the property
the arrangement exists to provide, and it is also the reason destruction runs
on a mandatory waiting period rather than taking effect on the button press.

Neither operation reaches backward through copies the customer has already
exported, forwarded messages already delivered in the clear to a recipient
under a tenant policy that permitted it, or plaintext a recipient has saved
elsewhere. A reviewer should read the guarantee as covering content held by the
service, which is what it covers.

## Recording of key use

Every wrap and unwrap operation is recorded twice: once by the key service, and
once in the tenant's own exportable event stream. The two records are produced
by different systems, which is the point — a discrepancy between them is
detectable. Records name the workload identity that performed the operation,
the key version, the tenant, the data class and the outcome. They do not
contain key material or plaintext.
""",
    "sso_saml_oidc": """# Single Sign-On — SAML 2.0 and OpenID Connect

## Scope of this document

The service federates sign-in for two distinct populations, and reviewers
frequently conflate them. Tenant administrators and internal senders are the
customer's own workforce, and they federate against the customer's identity
provider. Message recipients are, by definition, outside the customer's
identity provider, and their authentication is covered separately in the
recipient portal documentation. This document covers workforce federation.

## SAML 2.0

SAML 2.0 is the default protocol for workforce federation. The service acts as
a service provider; the customer's identity provider issues assertions.

Configuration is self-service from the tenant administration plane. The
administrator downloads the service provider metadata, which contains the
entity identifier, the assertion consumer service endpoint and the signing and
encryption certificates. The administrator then supplies identity provider
metadata, either as an uploaded document or as a metadata URL the service
refreshes periodically. Supplying a metadata URL is strongly preferred: it lets
a signing certificate rollover at the identity provider propagate without
anyone remembering to update the tenant, which is the single most common cause
of a federation outage.

Assertions must be signed. Assertion encryption is optional and, when enabled,
uses the published encryption certificate. Signature validation, audience
restriction, recipient and destination checks, and the NotBefore and
NotOnOrAfter conditions are all enforced before any session is established.
Clock skew tolerance is sixty seconds and is not configurable upward.

Required attributes are a name identifier carrying the user's primary email
address in a domain verified against the tenant, a given name and a family
name. Group membership claims are optional and drive role assignment.

Both identity-provider-initiated and service-provider-initiated flows are
supported. RelayState is validated against an allowlist of tenant-owned
destinations so that a federated sign-in cannot be used to redirect a user to
an arbitrary location.

## OpenID Connect

OpenID Connect is supported as an alternative for workforce federation and is
the protocol used by the desktop and mobile clients, which run the
authorisation code flow with PKCE.

Supported flows are authorisation code and authorisation code with PKCE, plus
refresh. The implicit flow is not supported for new configurations. Identity
tokens are validated against the identity provider's published key set, with
issuer, audience, nonce and expiry all checked, and sixty seconds of clock skew
tolerance. Key sets are refreshed on a schedule and on an unknown key
identifier, which lets a provider rotate signing keys without coordination.

Access tokens issued by the service carry a one-hour lifetime by default,
reducible to five minutes by tenant policy. Refresh tokens carry a fourteen-day
lifetime, are rotated on every use, and a replayed refresh token invalidates
the whole chain — which turns a stolen refresh token into a detectable event
rather than a silent persistent foothold.

## Group to role mapping

Roles inside the tenant derive from group claims asserted by the identity
provider. The administrator defines the mapping, which is evaluated at every
session establishment, so a change of group membership at the identity provider
takes effect on the user's next sign-in rather than at some later
reconciliation.

Direct mapping — one claimed group to one tenant role — is the recommended
style and covers most tenants. Hierarchical mapping lets one role inherit
another's permissions, with circular inheritance rejected when the mapping is
saved rather than at evaluation time. A user in several mapped groups receives
the union of the permissions, less any deny rule configured at the tenant
level. A user in no mapped group is denied and shown an administrator-supplied
message rather than a generic error.

## Break-glass access

Every tenant keeps one break-glass account that does not depend on the
federation configuration, so that an identity provider outage or a broken
federation configuration does not lock the customer out of its own tenant. The
break-glass account uses a strong password and a hardware-backed second factor,
is issued to a named individual, and every sign-in with it raises an immediate
notification to the tenant's registered security contacts.

The service reminds the tenant quarterly if the break-glass account has not
been exercised in ninety days, because a break-glass credential that has never
been tested tends not to work on the day it is needed.

## Migration between identity providers

Moving from one identity provider to another runs as a dual configuration. The
new configuration is added alongside the existing one; both are live for a
transition window the administrator sets, typically thirty to ninety days;
users are admitted through either; and the old configuration is removed once
the event records show no sessions being established through it. Each session
record names the configuration that established it, so migration progress is
observable rather than guessed at.

## Diagnosing a broken configuration

The administration plane includes a dry-run tool that validates an assertion or
an identity token against the tenant's configuration without creating a
session, and reports precisely which check failed. Configuration drift — an
expired signing certificate, a changed attribute name, a renamed group — causes
the large majority of federation failures. The service emits notifications
sixty, thirty and seven days before a configured certificate expires, and
records every change to the federation configuration with the identity of the
administrator who made it and the time it took effect.
""",
    "mfa_factors_policy": """# Multi-Factor Authentication — Supported Factors and Policy

## Scope of this document

This document covers the second factor for workforce sign-in to the tenant:
which factors are available, how policy selects between them, how step-up works
for sensitive operations, and how enrolment and recovery are handled. Where the
tenant federates to its own identity provider, the second factor may be
enforced there instead, and the interaction between the two is described below.

## Supported factors

Four factor types are available.

WebAuthn and FIDO2, covering both roaming authenticators such as security keys
and platform authenticators such as a laptop's biometric sensor. This is the
recommended factor and the only one that is phishing-resistant, because the
assertion is bound to the origin and a credential presented to a lookalike
domain simply does not verify.

Time-based one-time passwords per RFC 6238, compatible with any standard
authenticator application. Widely supported and easy to enrol, but
phishing-relayable: a user can be persuaded to read a code into an attacker's
prompt.

Push approval through the service's mobile client, with number matching. The
number-matching requirement exists to defeat approval fatigue — a user cannot
approve by reflex, because approving requires reading a number from the screen
they initiated the sign-in on.

One-time codes delivered by short message service. Available for compatibility
and as a recovery path. This factor is the weakest of the four and is
documented as such: subscriber identity module transfer attacks and signalling
network interception are both well established. Tenant policy may disable it
entirely, and many tenants do.

## Policy

Tenant policy determines who must present a second factor and which factors are
acceptable. Policy can require a second factor for every sign-in, for
administrative roles only, or only for step-up on sensitive operations. It can
restrict the acceptable factor set — for example, permitting only WebAuthn for
users holding administrative roles while allowing the broader set for ordinary
senders.

Where a role can read message content or change delivery policy, the service
requires a phishing-resistant factor and does not permit the tenant to relax
that. This is one of a small number of settings the tenant cannot configure
downward, and it is stated plainly rather than buried, because a reviewer is
entitled to know which controls are floors and which are defaults.

## Interaction with federated sign-in

Where the tenant federates, the second factor is normally enforced at the
customer's identity provider, and the service consumes the result. The service
reads the authentication context class reference in a SAML assertion, or the
corresponding claim in an OpenID Connect identity token, and applies tenant
policy to what it finds. A tenant can require that the assertion demonstrate a
phishing-resistant factor and reject sessions that do not.

Where the identity provider does not express the context, or where the tenant
prefers belt and braces, the service can challenge for its own second factor on
top of the federated sign-in. Doing both is a supported configuration, and some
regulated tenants choose it.

## Step-up for sensitive operations

Certain operations require a fresh factor presentation regardless of how long
the session has been open: creating or revoking an API credential, changing the
federation configuration, changing delivery policy for a partner domain,
changing key management settings, and exporting event records over a range
longer than thirty days.

Fresh means presented within the last five minutes of wall-clock time. An
administrator signed in for six hours is challenged before creating a
credential. The requirement applies to every role, including the highest, and
cannot be configured away.

## Enrolment and recovery

A user enrols a factor on first sign-in, or when policy changes to require one.
The service encourages enrolling two factors of different types, because the
common failure is a single lost device rather than a compromise.

Recovery is deliberately not self-service for administrative roles. A user who
has lost every enrolled factor is recovered by a tenant administrator, and a
tenant administrator who has lost every enrolled factor is recovered through
the break-glass account or, failing that, through a verification procedure with
the account team that involves a named contact and a callback to a number on
record. Self-service recovery by email link is not offered, because it collapses
the second factor into the first.

Every enrolment, every removal and every recovery is written to the tenant's
event stream with the acting identity, the affected user and the time.
""",
    "scim_provisioning": """# SCIM 2.0 User and Group Provisioning

## Scope of this document

This document describes automated lifecycle management of tenant users and
groups through a System for Cross-domain Identity Management endpoint, the
alternative just-in-time path, and the reconciliation behaviour when the two
sides disagree.

## The endpoint

Each tenant exposes a SCIM 2.0 endpoint at a per-tenant base path. The endpoint
is authenticated with a bearer credential the administrator generates in the
tenant administration plane. Credentials are shown once, are individually
revocable, carry an optional expiry, and are scoped to provisioning operations
only — a provisioning credential cannot read message content.

The endpoint implements the standard resource types. For users: create, replace,
patch, deactivate and reactivate. For groups: create, replace, patch and
delete, with membership changes expressed as patch operations against the group
resource. Filtering, pagination and the ServiceProviderConfig, ResourceTypes and
Schemas discovery endpoints are implemented, which is what lets an identity
provider configure itself against the tenant without hand-written mappings.

## Attribute mapping

The core user schema is supported, plus the enterprise user extension. The
external identifier is the stable key; the user name carries the primary email
address, which must be in a domain verified against the tenant. Given name,
family name, display name, active status, title, department and manager are
mapped. Unrecognised attributes are accepted and ignored rather than rejected,
because an identity provider that sends a superset of what a target consumes is
the normal case and failing the whole request over it produces support tickets
rather than security.

Group membership drives role assignment through the same mapping used by
federated sign-in, so a tenant that uses both federation and provisioning
maintains one mapping rather than two.

## Deactivation semantics

Deactivation is immediate and total. Setting a user inactive ends every live
session for that user, revokes every issued token, and refuses new sessions
until the user is reactivated. There is no grace period and no scheduled sweep
in between.

This is the single most important property of the provisioning path and the
reason it is preferred over the just-in-time alternative. When a customer
terminates an employee, the identity provider issues one patch and the user is
out, in seconds, without anyone at the customer remembering that this service
exists.

Deactivation preserves the user record and everything attributed to it. Message
history, event records and role assignments remain intact and attributable. A
reactivated user resumes with the same identity rather than appearing as a new
one, which is what keeps historical records coherent across an employee's
leave, transfer or return.

Hard deletion of a user resource is supported and is distinct from
deactivation: it removes the identity, and it is refused where retention policy
requires the record to persist, with a clear error rather than a silent
partial effect.

## Just-in-time provisioning

Tenants that do not operate provisioning may instead enable just-in-time
creation on first federated sign-in. The service creates the user from the
asserted attributes, assigns roles from the group mapping, and establishes the
session. On each later sign-in the attributes and roles are refreshed from the
current assertion.

The two paths are mutually exclusive per tenant. Just-in-time is simpler to
turn on and materially weaker: a user removed at the identity provider is not
deactivated here until they next attempt to sign in and fail, or until the
lifecycle sweep runs. For a tenant handling regulated content that gap is
usually unacceptable, which is why provisioning is the recommended path and
just-in-time is documented as the fallback.

## Reconciliation and conflicts

Every operation is reconciled against current tenant membership. Conflicts
return the standard SCIM error responses rather than a generic failure: a
duplicate user name returns 409 with a uniqueness error, a malformed patch
returns 400 with the invalidSyntax type, an unknown resource returns 404. The
error body names the offending attribute, so an administrator reading their
identity provider's failure log can act without opening a ticket.

Conflicts are also written to the tenant's event stream, so a systematic
mismatch between the identity provider's view and the tenant's — the usual
symptom of a half-completed migration — is visible as a pattern rather than as
scattered individual failures.

## Rate limiting and bulk changes

The endpoint is rate limited per tenant, with limits set well above the rate a
normal identity provider generates and returning 429 with a Retry-After header
when exceeded. Standards-conforming provisioning clients honour it.

Bulk operations are supported for the initial load, where a tenant onboards
several thousand users at once. A bulk request is processed as a unit with
per-operation results, so a single bad record does not fail the load. Large
initial loads are best coordinated with the account team, not because the
service cannot absorb them, but because a load that half-succeeds against a
misconfigured mapping is easier to prevent than to unpick.
""",
    "dr_bcp_rto_rpo": """# Disaster Recovery and Business Continuity — RTO and RPO

## Scope of this document

This document sets out the resilience posture of the cloud email encryption
service: the topology, what is replicated and how, the published recovery
objectives per component, the exercise programme, the backup regime, and the
dependencies on which continuity rests.

## Topology

Each residency zone is served from two regions operating active-active. Both
regions carry live traffic under normal conditions; neither is a cold standby
that nobody has exercised. Traffic is steered by latency-based routing that can
be collapsed onto a single region within about two minutes, which is the DNS
time-to-live plus propagation.

Within a region, every stateful component spans at least two availability
zones, and stateless components scale independently across three. The loss of
one availability zone is absorbed without a traffic shift and without a
customer-visible event.

Both regions in a zone are inside the residency boundary. A tenant pinned to
the European Union zone fails over between two European Union regions; it does
not fail over to another continent. This constrains the design and is
deliberate: a continuity arrangement that violates the residency commitment is
not a continuity arrangement a regulated customer can accept.

## Replication

Replication is chosen per data class.

Tenant configuration, delivery policy, identity state and key references
replicate synchronously between the two regions. A write is not acknowledged
until it is durable in both. The cost is write latency; the benefit is that
these classes have no data-loss window at all.

Message bodies, attachments and metadata replicate asynchronously with a target
lag under thirty seconds. Sudden loss of a region can therefore lose the most
recent writes within that window.

Event records are forwarded continuously to a durable store and are
acknowledged to the tenant only once durable, so an event that has been
acknowledged has not been lost.

## Published recovery objectives

| Component | Recovery time objective | Recovery point objective |
| --- | --- | --- |
| Encryption gateway and delivery path | 2 hours | 5 minutes |
| Recipient portal | 4 hours | 15 minutes |
| Tenant administration plane | 4 hours | 5 minutes |
| Key management plane | 1 hour | 0 (synchronous) |
| Event export | 4 hours | 0 for acknowledged records |

These are commitments against which the service is managed, not observations.
Measured behaviour in recent exercises has been well inside them; the numbers
above are the bound, and the bound is what belongs in a contract.

The service commitment for the Enterprise plan is 99.95% monthly uptime,
measured as a rolling monthly average, excluding maintenance announced at least
fourteen days in advance. Service credits for a missed month are set out in the
service level agreement and are not described here.

## Exercise programme

Continuity arrangements are exercised at least twice a year. One exercise is a
full regional evacuation: real production traffic for a residency zone is
collapsed onto a single region for a bounded window, held there, and then
ramped back in stages. This exercise is the one that matters, because it is the
only one that proves a single region can actually carry the load — capacity
planning on paper routinely says yes when the answer is no.

The second exercise is scenario-based and rotates: loss of the key management
plane, corruption of a replicated data set, loss of a critical supplier, and
loss of the primary operational communication channel have all been exercised.
Findings and remediation actions from every exercise are tracked to closure and
reviewed at the quarterly management review.

Enterprise customers may request a summary of the most recent exercise under
non-disclosure, and a customer whose own regulator requires evidence of
third-party continuity testing can arrange, through the account team, to
observe a scheduled exercise.

## Backups

Backups sit below replication as a defence against a different failure: not
losing the data, but corrupting or deleting it correctly-and-everywhere.
Replication faithfully copies a destructive mistake to both regions; a backup
does not.

Full backups of every tenant data store are taken daily, with continuous
point-in-time recovery over a rolling thirty-five-day window. Backup copies are
written to an account separate from production, under distinct credentials that
production workloads do not hold, with object lock preventing deletion inside
the retention window even by an administrator of that account.

Restore is tested monthly by restoring a randomly selected backup into an
isolated environment and running an integrity check. A failed restore test is
handled as a priority incident. Testing the restore rather than the backup is
the point: a backup that has never been restored is a hypothesis.

Extended retention beyond the default is available by contract for customers
with statutory retention obligations.

## Dependencies

Continuity rests on a small number of upstream services, mapped and reviewed
quarterly. Losing the infrastructure provider's regional control plane, the
authoritative DNS service, the public certification authority, or the managed
key service would each prevent normal operation for the duration of that
outage; each has a documented compensating arrangement where one is technically
and commercially available. Losing observability tooling, the ticketing system
or the status page provider would degrade operations without preventing
service.

## Communication during an event

During a declared event, the status page is updated first and continues to be
updated at stated intervals even when there is nothing new to say, because
silence is read as absence. Tenants with a configured webhook receive
programmatic notification. Where the event meets the incident notification
criteria, registered security contacts are notified directly within 72 hours of
confirmation, and a written post-incident review follows within thirty days.
""",
    "ssdlc_secure_development": """# Secure Development Lifecycle — Design, Build and Release Controls

## Scope of this document

This document describes how security is built into the way software for the
cloud email encryption service is designed, written, reviewed, built and
released. It covers threat modelling, the controls that gate a change into the
main branch, the build and release path, and how the team handles the
inevitable case where something ships and turns out to be wrong.

## Threat modelling

Every service has a threat model, and the model is a living document rather
than an artefact produced once for an assessor. A model is produced when a
service is created and revisited whenever the trust boundary moves — a new
external interface, a new data class handled, a new dependency inside the
boundary, or a change to who can reach what.

Modelling uses a structured decomposition: what the component holds, who talks
to it, where the boundaries are, and what an attacker on each side of each
boundary could attempt. Each identified threat gets a disposition: mitigated by
a named control, accepted with a recorded rationale and owner, or transferred.
Undispositioned threats block the design from proceeding, which is the only
mechanism that reliably prevents a threat model from becoming decorative.

Models are stored with the service they describe, in the same repository, so
they are reviewed in the same change process as the code and go stale visibly
rather than quietly.

## What gates a change into the main branch

A change cannot merge until five things hold.

A peer code review is recorded from someone other than the author. Reviews are
mandatory, and the branch protection enforces it rather than relying on the
team's discipline. Changes touching cryptographic code, the policy engine, the
key management plane or the tenant isolation boundary require a second reviewer
drawn from a named group with the relevant depth.

Static analysis passes with no new findings above the configured threshold.
Existing findings are tracked with owners and dates rather than being
suppressed silently; a suppression requires a comment naming the reason, and
suppressions are reviewed in bulk quarterly.

Dependency composition analysis passes. A new dependency with a known
vulnerability above the threshold blocks the merge. An existing dependency that
develops a known vulnerability raises a tracked item with a remediation target
by severity, and blocks the next release if the target is missed.

Secret scanning passes on the diff and on the full history of the branch. A
detected credential fails the change and triggers revocation of the detected
credential before anything else happens, on the assumption that a credential in
a diff is already exposed.

The test suite passes, including the tests that specifically exercise tenant
isolation and delivery policy selection. Those tests are treated as security
controls rather than as quality checks, and weakening one requires the same
second review as changing cryptographic code.

## Build and release

Builds run in an isolated environment from a clean checkout, with no
interactive access and no ambient credentials. Build inputs are pinned by
digest rather than by tag, so a rebuilt artefact is the same artefact.

Every deployable artefact is signed at build time, and the deployment path
verifies the signature before an artefact may run. A software bill of materials
is produced for every artefact and retained for the supported life of the
release; the bill of materials policy document covers its contents and
disclosure.

Container images are scanned before promotion between environments. Images are
rebuilt on a schedule regardless of whether the application code changed, so
that base-layer fixes reach production without waiting for a feature.

Infrastructure is defined as code and applied through the same review path as
application changes. Manual change in production is prevented by policy rather
than discouraged by convention, and detected drift raises an alert.

## Environments and data

Development and pre-production environments never contain production message
content. Test data is synthetic and generated. This removes an entire class of
exposure — the pre-production database restored from production and left
reachable — rather than managing it.

Pre-production is configured to match production in structure, so that a
control that works in one works in the other, and so that the penetration test
programme can run against pre-production with confidence that the result
transfers.

## Vulnerability handling and disclosure

Findings arrive from static analysis, dependency analysis, image scanning, the
annual independent test, the bug bounty programme and customers. All are
triaged into one queue with a severity and an owner, because a finding's route
of arrival says nothing about its importance.

Remediation targets by severity are seven days for critical, thirty days for
high, ninety days for medium, and best effort for low, measured from triage to
verified fix. Targets that are going to be missed are escalated before they are
missed, not after.

A published disclosure policy invites external researchers to report findings,
commits to acknowledging within two business days and to keeping the researcher
informed, and undertakes not to pursue legal action against good-faith research
conducted within the stated scope.

## When something ships wrong

The team runs blameless reviews after any change that causes a customer-visible
failure or that introduces a security weakness later found by someone else. The
output is a set of changes to the system or the process, each with an owner and
a date, tracked in the same place as feature work. The review asks what about
the environment made the mistake likely, not who made it. That framing is what
keeps the reports honest, and an honest report is the only kind worth writing.
""",
    "sbom_policy": """# Software Bill of Materials — Generation and Disclosure Policy

## Scope of this document

This document sets out how a software bill of materials is produced for the
cloud email encryption service, what it contains, how long it is kept, who can
obtain it, and how it is used to answer a component-level vulnerability
question quickly.

## Format and generation

A bill of materials is generated for every deployable artefact at build time in
CycloneDX 1.5, in JSON encoding. CycloneDX is used rather than SPDX because its
vulnerability and dependency-relationship extensions carry the information the
support workflow actually needs; an SPDX 2.3 rendering can be produced on
request for customers whose own tooling requires it.

Generation happens inside the build, from the resolved dependency graph, not
afterwards by scanning a finished image. Scanning a finished image finds what a
scanner can recognise; building from the resolved graph records what was
actually linked, including components a scanner would miss because they were
vendored or statically linked.

The document is generated for each artefact and for each container image, and
the two are related in the same document set so that a question about "the
gateway" can be answered without the asker needing to know how the gateway is
packaged.

## Contents

Each entry carries the component name, version, package URL, supplier where
known, declared licence, cryptographic hash of the retrieved artefact, and the
dependency relationship — whether the component is a direct dependency of the
application or is pulled in transitively, and by what.

Transitive depth is recorded rather than flattened. When a report names a
vulnerable component, the practical question is almost always whether the
service reaches the vulnerable code path, and knowing that the component is
four levels down behind a package that does not call the affected function is
most of the answer.

Build-time-only components are marked as such and separated from those present
at runtime, because a vulnerability in a build tool and a vulnerability in a
shipped library warrant different responses.

## Signing and retention

Each document is signed at generation with the same key used for artefact
signing, so a document obtained from any route can be verified as the one this
build produced. The document is stored alongside the artefact it describes and
retained for the supported life of the release plus twelve months.

Documents for every release still in support are retrievable at any time. This
matters when a report lands about a component that was removed two releases
ago: the question is whether any supported release still contains it, and that
question is answered from the stored set rather than from memory.

## Disclosure to customers

A customer under a mutual non-disclosure agreement may obtain the bill of
materials for any release it is running, on request through the account team,
in CycloneDX 1.5 or SPDX 2.3.

Disclosure requires the non-disclosure agreement. The complete component
inventory of a security product is a useful document to an attacker choosing
where to look, and the balance of interest favours giving it to customers who
have a relationship rather than publishing it. Customers who need it for their
own supply chain programme get it; the request is routine and is not treated as
an exception.

## Using it to answer a vulnerability question

When a widely-reported vulnerability lands, the sequence is: query the stored
documents across every supported release for the component and version range;
determine for each match whether the affected code path is reachable in the
service's configuration; assign severity and a remediation target from the
standing schedule; and communicate to customers who have asked or, where the
determination is that customers are affected, to all of them.

The first step takes minutes because the documents are stored and queryable
rather than being regenerated under pressure. The second step is the one that
takes judgement, and having the first step already done is what leaves time for
it.

## Upstream expectations

Suppliers of components used inside the trust boundary are expected to publish
their own bill of materials, or to provide one on request. Where a supplier
cannot, that is recorded as a supply chain risk against the component with an
owner, and it weighs against the component in the review that happens whenever
a replacement is considered.
""",
    "data_residency_matrix": """# Data Residency Matrix — Regions, Pinning and Cross-Border Transfer

## Scope of this document

This document states where tenant data is held, what "held" covers, how a
tenant is pinned to a residency zone, the narrow circumstances in which data or
access crosses a zone boundary, and the transfer safeguards that apply when it
does.

## Residency zones

| Zone | Regions | Typical driver |
| --- | --- | --- |
| Canada | Two Canadian regions | Federal and provincial public sector, Canadian financial services |
| United States | Two United States regions | United States regulated industries |
| European Union | Two European Union regions | GDPR, sectoral supervision |
| United Kingdom | Two United Kingdom regions | UK GDPR, Financial Conduct Authority expectations |
| Australia | Two Australian regions | Australian government and financial services |

A zone is served from two regions in an active-active configuration, and both
sit inside the zone. Continuity within a zone therefore never requires leaving
it. This is a constraint on the resilience design rather than a consequence of
it.

## What "pinned to a zone" covers

A tenant elects a zone at provisioning. The election covers message bodies,
attachments, message metadata, derived search indexes, key material including
key-encryption keys and their versions, event records, and backup images of all
of the above.

It does not cover three things, and saying so plainly is more useful to a
reviewer than a blanket claim that nothing ever leaves.

Aggregate operational telemetry — request rates, error rates, latency
distributions, capacity metrics — is collected centrally. It carries no message
content, no message metadata, no personal data of senders or recipients, and no
tenant-identifying content beyond an opaque tenant identifier.

Support correspondence lives in the support platform, which is located in the
European Union. A customer that raises a ticket and pastes content into it has
placed that content there; the support workflow asks customers not to, and
redacts what it detects, but the correspondence itself is not zone-pinned.

Billing and contract records are held centrally and contain no message content.

## Changing zones

A tenant's zone can be changed after provisioning through a coordinated
migration, not a configuration toggle. Migration copies data to the new zone,
verifies it, re-wraps key material under the new zone's hierarchy, cuts traffic
over, and then erases the source. The source erasure is verified and confirmed
in writing, because a migration that leaves a readable copy behind has
defeated its own purpose.

Migration is scheduled with the account team. Duration depends on volume; a
typical enterprise tenant completes inside a maintenance window.

## Support access from outside the zone

Engineering and support personnel are distributed. Where an issue requires a
member of staff outside the tenant's zone to access tenant data, the access
runs through a named-approval path: a request naming the tenant, the reason,
the data needed and the time bound; approval by a person other than the
requester; a session that expires automatically; and a record written to the
tenant's own event stream, not only to an internal log.

A tenant may require that its own administrator approve each such session
before it can begin. Tenants in the Canadian and European Union zones commonly
enable this, and public sector tenants generally require it. A tenant may also
restrict support access to personnel located inside its zone, at the cost of a
slower response outside that zone's business hours — the trade-off is real and
is stated during onboarding rather than discovered later.

## Transfer safeguards

Where personal data does cross a zone boundary — support correspondence, an
approved support session, or a cross-zone feature the tenant has explicitly
enabled — the transfer relies on the 2021 Standard Contractual Clauses for
transfers out of the European Economic Area, supplemented by a transfer impact
assessment available on request, and on the International Data Transfer
Addendum for transfers out of the United Kingdom. The GDPR position and the
sub-processor register are set out in the Data Processing Agreement.

The tenant's ability to hold its own root key material is relevant to a
transfer assessment. Where the tenant holds the root key, content that crosses
a boundary in sealed form remains unreadable without a call to the tenant's own
key, and that call is recorded in the tenant's own key service.

## Verifying the position

A reviewer can verify the residency position rather than take it on trust. The
tenant administration plane displays the elected zone and the regions serving
it. The event stream records every support session against the tenant,
including the location from which it was established. The sub-processor
register names the processing location of each sub-processor. And the annual
independent assessment covers the controls that enforce pinning, so the
assertion is tested by someone other than the party making it.
"""
}

# ---------------------------------------------------------------------------
# prior-rfps/ — completed questionnaire extracts. Attribution lives in the
# sidecar; the prose carries the answers.
# ---------------------------------------------------------------------------

PRIOR_RFP_BODIES: dict[str, str] = {
    "northwind_mutual_caiq_2026": """# Completed CAIQ v4.0.3 extract — Cryptography, Encryption and Key Management

Submitted 12 May 2026. Reviewed and approved for outbound use by the security
assurance team.

## EKM-01 — Are encryption keys maintained by the cloud consumer or a trusted key management provider?

Both arrangements are offered. Under the default, tenant key-encryption keys
are held by us in a managed key service. Enterprise tenants may instead supply
a customer-managed key held in their own account, or hold root key material in
a hardware security module they operate. Where the tenant holds the key, it can
revoke our grant unilaterally, which renders sealed content unreadable
immediately and reversibly, or schedule destruction, which is irreversible
after the mandatory waiting period.

## EKM-02 — Do you have a capability to allow creation of unique encryption keys per tenant?

Yes. Key-encryption keys are per tenant and, within a tenant, per data class —
message bodies and attachments, message metadata, derived indexes, and event
records each have their own. Data keys are per message.

## EKM-03 — Do you encrypt tenant data at rest?

Yes. Message bodies, attachments, metadata and derived indexes are sealed with
AES-256-GCM under per-message data keys, which are wrapped by the tenant
key-encryption key. Data keys exist in plaintext only in process memory for the
duration of a single operation. The stored envelope carries the ciphertext, the
nonce, the authentication tag and the wrapped data key.

## EKM-04 — What is your key rotation policy?

Key-encryption keys rotate on a 365-day cycle by default. Tenant administrators
may rotate on demand at any time from the administration plane or the API, with
effect within minutes. Rotation does not require rewriting existing envelopes,
because each envelope names the key version that wrapped it; a background
process migrates envelopes onto the current version over time.

## EKM-08 — Do you have documented ownership for each stage of the key lifecycle?

Yes. Generation, distribution, storage, use, rotation, archival and destruction
each have a named owner and a documented procedure. The procedures are covered
by the annual independent assessment and were tested without exception in the
most recent report period.

## Reviewer note

These responses were approved against the encryption and key management
documentation current at the date of submission. Confirm against the current
product documentation before reuse; the key management posture is the area most
likely to have gained options since.
""",
    "cascadia_trust_caiq_2026": """# Completed CAIQ v4.0.3 extract — Identity and Access Management

Submitted 2 April 2026. Reviewed and approved for outbound use by the security
assurance team.

## IAM-03 — Is a single sign-on solution supported for tenant users?

Yes. Workforce sign-in federates over SAML 2.0 or OpenID Connect against the
customer's identity provider. Signed assertions are mandatory and encrypted
assertions are optional. Configuration is self-service, and supplying identity
provider metadata by URL is recommended so that a signing certificate rollover
propagates without manual intervention.

## IAM-04 — Do you support multi-factor authentication for all tenant users?

Yes. WebAuthn and FIDO2, time-based one-time passwords, push approval with
number matching, and one-time codes by short message service are supported.
Tenant policy determines who must present a second factor and which factors are
acceptable. Roles that can read message content or change delivery policy
require a phishing-resistant factor, and that requirement cannot be relaxed by
tenant configuration.

## IAM-09 — Is automated provisioning and deprovisioning supported?

Yes, through a per-tenant SCIM 2.0 endpoint supporting create, update,
deactivate and reactivate for users and create, update, delete and membership
patch for groups. Deactivation is immediate: live sessions end, issued tokens
are revoked, and new sessions are refused until reactivation. A just-in-time
alternative exists for tenants that do not operate provisioning, and is
documented as materially weaker for exactly this reason.

## IAM-12 — How are privileged sessions controlled?

Privileged operations require a factor presented within the last five minutes
regardless of session age. This covers credential creation and revocation,
federation configuration changes, delivery policy changes for a partner domain,
key management settings, and event exports spanning more than thirty days. The
requirement applies to every role including the highest.

## IAM-14 — Are access rights reviewed periodically?

Yes, quarterly for standing access to production, with the review recorded and
the record retained. The review was tested without exception in the most recent
independent assessment.

## Reviewer note

Approved against the identity documentation current at the date of submission.
""",
    "meridian_health_caiq_2026": """# Completed CAIQ v4.0.3 extract — Data Security and Privacy Lifecycle

Submitted 18 February 2026. Reviewed and approved for outbound use by legal and
the security assurance team.

## DSP-01 — Do you provide a Data Processing Agreement?

Yes. The standard Data Processing Agreement is offered under Article 28 of the
GDPR and its United Kingdom equivalent, executed as an annex to the master
agreement. It incorporates the 2021 EU Standard Contractual Clauses in the
controller-to-processor module and the UK International Data Transfer Addendum.
It does not require negotiation for the great majority of customers.

## DSP-04 — Do you publish a list of sub-processors?

Yes. The register is published and versioned, and names each entity, the
processing it performs and the country in which that processing occurs.
Customers may subscribe to change notifications. A new sub-processor is
announced at least thirty days before it begins processing, and a customer that
objects on reasonable data protection grounds within the notice period may
terminate the affected service without penalty if the objection cannot be
resolved.

## DSP-10 — How do you assist with data subject rights requests?

Most requests are satisfied by the controller directly through the tenant
administration plane, which supports search and export scoped to a data subject
and supports deletion. Where a request cannot be satisfied that way, we assist
within the timescales the Data Processing Agreement sets.

## DSP-16 — Is customer data used for any secondary purpose?

No. Message content and metadata are processed solely to deliver the contracted
service on the customer's documented instruction. There is no use for
marketing, for product analytics, or for training machine learning models. This
is attested under ISO/IEC 27018:2019 as well as contracted.

## DSP-19 — What happens to data at the end of the relationship?

The customer may export through the documented path for thirty days after
termination. After that, or immediately on written instruction, content is
deleted and the deletion confirmed in writing. Content in backup images is
rendered unrecoverable as those images age out of a retention window not
exceeding thirty-five days, and the confirmation states this rather than
implying instantaneous erasure everywhere.

## Reviewer note

Approved against the Data Processing Agreement version current at the date of
submission. The sub-processor register changes; always cite the current
version.
""",
    "rideau_digital_caiq_2026": """# Completed CAIQ v4.0.3 extract — Datacentre Security and Data Location

Submitted 27 January 2026. Reviewed and approved for outbound use by the
security assurance team. Prepared for a public sector procurement with a
Canadian residency requirement.

## DCS-01 — In which countries is tenant data stored?

The tenant is pinned to a residency zone elected at provisioning. For this
engagement the zone is Canada, served from two Canadian regions in an
active-active configuration. Both regions are inside the zone, so continuity
within the zone never requires leaving Canada.

## DCS-04 — What data classes are covered by the residency commitment?

Message bodies, attachments, message metadata, derived search indexes, key
material including key-encryption keys and their versions, event records, and
backup images of all of the above. Not covered: aggregate operational telemetry
carrying no content, metadata or personal data; support correspondence, which
resides in a support platform in the European Union; and billing records. We
state the exclusions explicitly rather than making a blanket claim.

## DCS-06 — Can personnel outside the residency zone access tenant data?

Only through a named-approval path. A request naming the tenant, the reason,
the data required and a time bound is approved by a person other than the
requester; the session expires automatically; and a record is written to the
tenant's own event stream, not only to an internal log. This tenant has enabled
the option requiring its own administrator to approve each session before it
begins, and may additionally restrict support access to personnel located
inside Canada.

## DCS-09 — Is the residency control independently assessed?

Yes. The controls that enforce zone pinning are within the scope of the ISO/IEC
27001:2022 certified management system and are examined in the surveillance
programme, so the assertion is tested by a party other than us.

## Reviewer note

Approved against the residency matrix current at the date of submission.
""",
    "anvil_capital_caiq_2025": """# Completed CAIQ v4.0.3 extract — Business Continuity and Security Incident Management

Submitted 6 November 2025. Reviewed and approved for outbound use by the
security assurance team.

## BCR-01 — Do you have a business continuity plan, and is it exercised?

Yes. Each residency zone runs active-active across two regions, both carrying
live traffic. Continuity arrangements are exercised at least twice a year. One
exercise is a full regional evacuation with real production traffic collapsed
onto a single region for a bounded window; the other is scenario-based and
rotates through loss of the key management plane, corruption of a replicated
data set, loss of a critical supplier, and loss of the primary operational
communication channel.

## BCR-05 — What are your recovery objectives?

The encryption gateway and delivery path carry a recovery time objective of 2
hours and a recovery point objective of 5 minutes. The recipient portal carries
4 hours and 15 minutes. The key management plane carries 1 hour and a zero
recovery point objective, because its state replicates synchronously. These are
commitments, not observations.

## BCR-11 — How are backups protected and tested?

Full backups daily with continuous point-in-time recovery over a rolling
thirty-five day window. Copies are written to an account separate from
production under credentials production workloads do not hold, with object lock
preventing deletion inside the retention window. Restores are tested monthly
into an isolated environment with an integrity check; a failed restore test is
handled as a priority incident.

## SEF-02 — Describe your security incident response process.

Detection, triage, containment, eradication, recovery and review, with a named
incident commander for any event above the lowest severity and a 24x7
operations function performing triage. The procedure is exercised twice per
report period, once as a tabletop and once as a live scenario.

## SEF-05 — What are your customer notification timelines?

Where an event has been confirmed to have affected the confidentiality,
integrity or availability of a tenant's data, that tenant's registered security
contacts are notified within 72 hours of confirmation. The initial notification
states what is known, what is not yet known, the containment position and the
point of contact. A written post-incident review follows within thirty days of
closure. Events prevented by controls before impact, and events confined to
internal systems without effect on tenant isolation, are recorded and reviewed
internally but are not notifiable.

## SEF-08 — Are incident response obligations covered contractually?

Yes, the 72-hour notification commitment is in the master agreement and the
Data Processing Agreement, and the control is within the scope of the SOC 2
Type II report.

## Reviewer note

Approved against the continuity and incident documentation current at the date
of submission. Recovery objectives have since been restated per component;
confirm against the current documentation before reuse.
""",
    "helix_reinsurance_caiq_2025": """# Completed CAIQ v4.0.3 extract — Audit Assurance and Supply Chain

Submitted 22 September 2025. Reviewed and approved for outbound use by the
security assurance team.

## A&A-01 — What independent assurance reports do you hold?

A SOC 2 Type II report covering Security, Availability and Confidentiality, and
ISO/IEC 27001:2022 certification with a Statement of Applicability available
under non-disclosure. Conformance to ISO/IEC 27018:2019 for the processing of
personally identifiable information in public cloud is attested alongside the
ISO/IEC 27001 surveillance activity.

## A&A-02 — How often are these renewed?

The SOC 2 Type II report is produced annually against a twelve-month period.
ISO/IEC 27001 follows the standard three-year cycle with annual surveillance.
Internal audit covers the whole management system annually, independently of
the external cycle.

## A&A-05 — Do you commission independent penetration testing?

Yes, annually, by an independent testing firm, covering the internet-facing
gateway and its SMTP listeners, the recipient portal, the administration plane
and API, and the externally reachable interfaces of the key management plane.
An executive summary is available on request; the full technical report is
released under a mutual non-disclosure agreement.

## STA-05 — Do you produce a software bill of materials?

Yes. A signed CycloneDX 1.5 bill of materials is generated at build time for
every deployable artefact from the resolved dependency graph, and retained for
the supported life of the release plus twelve months. Customers under a mutual
non-disclosure agreement may obtain the bill of materials for any release they
are running, in CycloneDX or SPDX.

## STA-09 — How are sub-processors and suppliers assessed?

Before onboarding, against the criteria in the supplier management procedure,
and annually thereafter. Contractual security terms are no less protective than
those we owe our own customers. Sub-processors are disclosed in the published
register with thirty days' notice before any change takes effect.

## STA-14 — What are your vulnerability remediation targets?

Seven days for critical, thirty days for high, ninety days for medium, best
effort for low, measured from triage to verified fix. Targets expected to be
missed are escalated before the deadline rather than after.

## Reviewer note

Approved against the assurance evidence current at the date of submission. The
SOC 2 report period cited here has since been superseded — always cite the
current report.
""",
}

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_pdf(dest: Path, title: str, blocks: list[dict[str, Any]]) -> None:
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], spaceBefore=18, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], spaceBefore=12, spaceAfter=8)
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=10.5, leading=15, spaceAfter=8
    )

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
            rows = block["table"]
            ncols = len(rows[0])
            widths = {2: [2.2, 4.2], 3: [1.1, 3.9, 1.4], 4: [1.1, 2.6, 1.6, 1.4]}[ncols]
            t = Table(rows, repeatRows=1, colWidths=[w * inch for w in widths])
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


# Sidecar keys carried through from the compact source JSON. `document_id`,
# `source_type` and `source_id` are set explicitly; the rest are copied when
# present. Everything here is flat and scalar (or a list of strings) because
# that is all Bedrock KB filterable metadata accepts, and because
# retriever._sidecar_metadata() drops anything else.
_PASSTHROUGH_KEYS = (
    "updated_at",
    "approved_at",
    "approved_by",
    "answered_at",
    "expires_on",
    "question_text",
    "audience",
    "customer_name",
    "customer_id",
    "questionnaire_id",
    "questionnaire_name",
)


# Sidecar keys that shared.provenance.Provenance parses into `datetime`. They
# are written as timezone-aware ISO-8601, not as bare dates.
#
# A bare "2025-06-30" parses into a NAIVE datetime, and Provenance.is_expired
# then compares it against datetime.now(UTC) — an aware value — which raises
# TypeError rather than returning a boolean. The same applies to
# Provenance.age_days and to the divergence detector's sort, which uses an
# aware epoch as its floor. Appending the offset here keeps every consumer
# working. Lexicographic ordering is preserved, so the string comparisons that
# Bedrock KB metadata filters and retriever._kb_prior_matches rely on still
# behave: "2027-07-14T00:00:00+00:00" > "2026-08-27" exactly as the bare dates
# would compare.
_DATETIME_KEYS = ("answered_at", "approved_at", "expires_on")


def _as_utc_iso(value: str) -> str:
    """Widen a YYYY-MM-DD sidecar date to a timezone-aware ISO-8601 timestamp."""
    text = str(value)
    if len(text) == 10 and text.count("-") == 2:
        return f"{text}T00:00:00+00:00"
    return text


def _build_attrs(prefix: str, src_json: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "document_id": src_json["document_id"],
        "source_type": SOURCE_TYPE_BY_PREFIX[prefix],
        "source_id": SOURCE_ID_BY_PREFIX[prefix],
        "topic_ids": list(src_json.get("topic_ids", [])),
    }
    for key in _PASSTHROUGH_KEYS:
        if src_json.get(key):
            attrs[key] = (
                _as_utc_iso(src_json[key]) if key in _DATETIME_KEYS else src_json[key]
            )
    if src_json.get("corroborated_by"):
        attrs["evidence_refs"] = list(src_json["corroborated_by"])
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
    body = PRODUCT_DOCS_BODIES.get(doc_id) or (
        f"# {data.get('title', doc_id)}\n\n{data.get('excerpt', '')}\n"
    )
    dest = DST / "product-docs" / f"{doc_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return dest, data


def _generate_prior_rfp(src_path: Path) -> tuple[Path, dict[str, Any]]:
    data = json.loads(src_path.read_text())
    doc_id = data["document_id"]
    body = PRIOR_RFP_BODIES.get(doc_id) or (
        f"# {data.get('title', doc_id)}\n\n{data.get('excerpt', '')}\n"
    )
    dest = DST / "prior-rfps" / f"{doc_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return dest, data


def _generate_sme_approved(src_path: Path) -> tuple[Path, dict[str, Any]]:
    """Format SME-approved Q&A as markdown. The embedding model sees the full
    Q + A text, so semantic similarity against an inbound question is
    high-quality — this is what powers the real cosine-based H signal.

    Shape must stay in step with lambdas/review_api/handler.py:_sme_markdown(),
    which writes the same layout when an SME's edit is promoted back into the
    knowledge base. The second-run flywheel depends on the two matching.
    """
    data = json.loads(src_path.read_text())
    doc_id = data["document_id"]
    body = (
        f"# SME-Approved Q&A — {doc_id}\n\n"
        f"**Approved:** {data.get('approved_by', 'unknown')} on {data.get('approved_at', '?')}  \n"
        f"**Topics:** {', '.join(data.get('topic_ids', [])) or '—'}  \n"
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
    per_prefix: dict[str, int] = {}
    for prefix, gen in GENERATORS.items():
        src_dir = SRC / prefix
        if not src_dir.exists():
            print(f"  SKIP {prefix}/ — no source sidecars")
            continue
        dst_dir = DST / prefix
        # The corpus is a fixed set. Clear the target so a document dropped
        # from the source tree does not linger and get uploaded to S3.
        if dst_dir.exists():
            for stale in dst_dir.iterdir():
                if stale.is_file():
                    stale.unlink()
        for src in sorted(src_dir.glob("*.json")):
            dest, data = gen(src)
            attrs = _build_attrs(prefix, data)
            _write_metadata_sidecar(dest, attrs)
            rel = dest.relative_to(ROOT)
            print(f"  wrote {rel}  ({len(attrs['topic_ids'])} topic_ids)")
            emitted += 1
            per_prefix[prefix] = per_prefix.get(prefix, 0) + 1

    print(f"\nDone. {emitted} documents + sidecars under {DST.relative_to(ROOT)}/")
    for prefix in GENERATORS:
        print(f"  {prefix + '/':<16} {per_prefix.get(prefix, 0)}")


if __name__ == "__main__":
    main()
