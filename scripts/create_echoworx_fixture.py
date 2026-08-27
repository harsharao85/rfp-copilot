#!/usr/bin/env python3
"""Build the Echoworx CAIQ v4 demo fixture: two workbooks and one manifest.

Outputs:
  data/incoming/echoworx-caiq-v4-40q.xlsx           40 questions, CAIQ v4 shape
  data/incoming/echoworx-caiq-followup-5q.xlsx      5 questions, second run
  data/incoming/echoworx-caiq-v4-40q.expected.json  what QA asserts against

Workbook layout matches scripts/create_demo_rfps.py so that
lambdas/excel_parser/parser.py finds the same question column, answer column
and section headers it finds in the existing demo files. Section headers are
written as "<Letter>. <Title>" because that is one of the forms
parser.SECTION_HEADER_RE recognises.

Why the questions are worded the way they are
---------------------------------------------
Two pieces of machinery read the question text, and both are keyword-driven, so
the wording is load-bearing rather than decorative.

question_classifier/handler.py:_TOPIC_PATTERNS decides the topic, and
question_classifier/dispatch.py:TOPIC_CLASS turns the topic into one of four
classes. A question that matches no pattern falls to `unclassified`, which is a
real outcome for genuine long-tail questions and a bug for anything else.

hard_rules/rules.py matches on the *answer*, not the question — but a generator
restates the question's framing in its answer, so a question that asks "will
you support X by Q2 2027" produces an answer carrying the forward-looking
phrasing that rule 4 demotes on. That is how questions 31-37 fire their rules,
and it is why the GREEN questions are worded to avoid the rule vocabulary
entirely: no "audit", no framework names, no pricing words, no RTO/RPO.

The expected tiers below are the *design intent*. scripts/verify_fixture.py
runs the real classifier, scorer and rule engine over this fixture and reports
every question where the intent and the code disagree. Where they disagreed
during authoring, the note field records why.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "incoming"

FIXTURE_NAME = "echoworx-caiq-v4-40q.xlsx"
FOLLOWUP_NAME = "echoworx-caiq-followup-5q.xlsx"
MANIFEST_NAME = "echoworx-caiq-v4-40q.expected.json"

GREEN, AMBER, RED = "green", "amber", "red"


# Each entry: (id, section, text, expected_tier, expected_triggers, note)
# `expected_triggers` lists hard-rule trigger names that MUST appear. A run may
# legitimately produce more (a compliance question that also trips the SLA
# pattern, for instance); verify_fixture.py treats the list as a subset check.
QUESTIONS: list[dict] = [
    # -- A. Cryptography and Key Management ---------------------------------
    {
        "id": "CAIQ-001",
        "section": "A. Cryptography and Key Management",
        "text": (
            "How is customer message content encrypted at rest? State the algorithm, "
            "the mode of operation, and the key length."
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "Happy path. encryption_at_rest -> auth_product, product_doc primary retrieves.",
    },
    {
        "id": "CAIQ-002",
        "section": "A. Cryptography and Key Management",
        "text": (
            "Is AES-256 used for storage encryption, and how are per-message data keys "
            "generated and wrapped?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "Happy path, second phrasing of the same topic.",
    },
    {
        "id": "CAIQ-003",
        "section": "A. Cryptography and Key Management",
        "text": (
            "How is customer data protected in transit? State the minimum TLS version "
            "accepted and the cipher suites you allow."
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "encryption_in_transit -> auth_product.",
    },
    {
        "id": "CAIQ-004",
        "section": "A. Cryptography and Key Management",
        "text": (
            "Do you refuse TLS 1.0 and TLS 1.1 at every external listener, and do you "
            "assert HSTS on public endpoints?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "encryption_in_transit, narrow factual phrasing.",
    },
    {
        "id": "CAIQ-005",
        "section": "A. Cryptography and Key Management",
        "text": (
            "Do you support BYOK, where the tenant supplies a key held in its own KMS "
            "account?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "key_management. Multi-passage: product doc + Northwind Mutual prior -> C=1.0.",
    },
    {
        "id": "CAIQ-006",
        "section": "A. Cryptography and Key Management",
        "text": (
            "What is the default key rotation period, and can a tenant administrator "
            "trigger rotation on demand?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "key_management. Re-asked verbatim in the follow-up workbook as a control.",
    },
    {
        "id": "CAIQ-007",
        "section": "A. Cryptography and Key Management",
        "text": (
            "Can customer-managed key material be held in a dedicated HSM partition "
            "outside your environment?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "key_management via the customer-managed-key pattern.",
    },
    # -- B. Identity and Access Management -----------------------------------
    {
        "id": "CAIQ-008",
        "section": "B. Identity and Access Management",
        "text": (
            "Which SSO protocols do you support for workforce sign-in - SAML 2.0, OIDC, "
            "or both?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "sso carries a seismic tertiary that is unreachable offline; stays green without it.",
    },
    {
        "id": "CAIQ-009",
        "section": "B. Identity and Access Management",
        "text": (
            "Which MFA factors are available, and can a second factor be required by "
            "tenant policy?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "mfa, same seismic-tertiary property as sso.",
    },
    {
        "id": "CAIQ-010",
        "section": "B. Identity and Access Management",
        "text": "Do you support SCIM 2.0 for automated user provisioning and deprovisioning?",
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "scim.",
    },
    # -- C. Audit and Assurance ----------------------------------------------
    {
        "id": "CAIQ-011",
        "section": "C. Audit and Assurance",
        "text": (
            "Do you hold a current SOC 2 Type II report? State the trust services "
            "criteria in scope."
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "Green from the compliance primary, demoted to amber by hard rule 2.",
    },
    {
        "id": "CAIQ-012",
        "section": "C. Audit and Assurance",
        "text": (
            "Which subservice organisations are carved out of your SOC 2 report, and "
            "what complementary controls do you expect of us?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "Same path; exercises the carve-out content in the SOC 2 summary.",
    },
    {
        "id": "CAIQ-013",
        "section": "C. Audit and Assurance",
        "text": "Are you certified to ISO 27001? Provide the certificate number and validity dates.",
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "iso27001 -> auth_compliance.",
    },
    {
        "id": "CAIQ-014",
        "section": "C. Audit and Assurance",
        "text": (
            "Which Annex A controls are declared not applicable in your ISO 27001 "
            "Statement of Applicability, and why?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "Needs the SoA extract specifically, not the certificate.",
    },
    {
        "id": "CAIQ-015",
        "section": "C. Audit and Assurance",
        "text": (
            "The SOC 2 Type II report we hold on file is dated 2024. Is that report "
            "still current evidence of your control environment?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": (
            "Freshness. Both SOC 2 documents match; soc2_type2_2024_expired carries an "
            "expires_on in the past so it is marked expired and must not be the primary "
            "citation. See expected_suppressed_documents in this manifest."
        ),
    },
    {
        "id": "CAIQ-016",
        "section": "C. Audit and Assurance",
        "text": (
            "State the report period and issue date of your current SOC 2 Type II "
            "attestation, and confirm which earlier reports it supersedes."
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "Second freshness probe, phrased so the correct answer names both reports.",
    },
    # -- D. Business Continuity and Resilience -------------------------------
    {
        "id": "CAIQ-017",
        "section": "D. Business Continuity and Resilience",
        "text": "State your RTO and RPO for the encryption gateway and for the recipient portal.",
        "expected_tier": AMBER,
        "expected_triggers": ["sla_claim"],
        "note": "dr_bcp -> auth_product with a compliance extra-primary. SLA rule demotes.",
    },
    {
        "id": "CAIQ-018",
        "section": "D. Business Continuity and Resilience",
        "text": "Do you commit to 99.95% uptime monthly, and how is that figure measured?",
        "expected_tier": AMBER,
        "expected_triggers": ["sla_claim"],
        "note": "Uptime percentage is what the SLA pattern keys on; a bare 'uptime' is not enough.",
    },
    {
        "id": "CAIQ-019",
        "section": "D. Business Continuity and Resilience",
        "text": (
            "Describe your disaster recovery exercise programme and the RPO you achieve "
            "for message metadata."
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["sla_claim"],
        "note": "Exercise cadence is in both the product doc and the Anvil Capital prior.",
    },
    # -- E. Security Incident Management -------------------------------------
    {
        "id": "CAIQ-020",
        "section": "E. Security Incident Management",
        "text": (
            "Describe your security incident response process, and confirm the process "
            "is within the scope of your SOC 2 report."
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "incident_response + soc2, both auth_compliance.",
    },
    {
        "id": "CAIQ-021",
        "section": "E. Security Incident Management",
        "text": (
            "Do you commit to notifying us within 72 hours of confirming a personal "
            "data breach, as GDPR Article 33 requires?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "The 72-hour commitment. gdpr pulls the compliance rule.",
    },
    {
        "id": "CAIQ-022",
        "section": "E. Security Incident Management",
        "text": (
            "What information is in your initial incident notification, and is the "
            "notification control tested in your most recent audit?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "'audit' alone is enough for hard rule 2 - deliberately, it is a claim word.",
    },
    # -- F. Application and Supply Chain Security ----------------------------
    {
        "id": "CAIQ-023",
        "section": "F. Application and Supply Chain Security",
        "text": (
            "How often do you commission independent penetration testing, and can you "
            "share the attestation letter from the testing firm?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "pentest -> auth_compliance.",
    },
    {
        "id": "CAIQ-024",
        "section": "F. Application and Supply Chain Security",
        "text": (
            "Do you produce a software bill of materials for each release, and how is it "
            "verified during your annual audit?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "sbom -> auth_product. Pairs with 023 to give the 'mixed class' row.",
    },
    # -- G. Data Residency and Privacy ---------------------------------------
    {
        "id": "CAIQ-025",
        "section": "G. Data Residency and Privacy",
        "text": (
            "In which regions is customer data stored, can data residency be pinned per "
            "tenant, and how does that map to your GDPR transfer position?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "Dual primary: data_residency adds compliance_store to product_docs.",
    },
    {
        "id": "CAIQ-026",
        "section": "G. Data Residency and Privacy",
        "text": (
            "Which data classes are covered by your data residency commitment, which are "
            "excluded, and is the pinning control within your ISO 27001 certified scope?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "The exclusions are the interesting half; the residency matrix states them.",
    },
    {
        "id": "CAIQ-027",
        "section": "G. Data Residency and Privacy",
        "text": (
            "Do you offer a DPA under GDPR Article 28, and does it incorporate the EU "
            "Standard Contractual Clauses?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "dpa -> auth_compliance.",
    },
    {
        "id": "CAIQ-028",
        "section": "G. Data Residency and Privacy",
        "text": (
            "Provide your current sub-processor register with the processing purpose and "
            "location for each entry, and confirm each sub-processor is audited annually."
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["compliance_claim"],
        "note": "Sub-processor pattern also routes to dpa.",
    },
    # -- H. Secure Development -----------------------------------------------
    {
        "id": "CAIQ-029",
        "section": "H. Secure Development",
        "text": (
            "Describe your secure development lifecycle, including what static and "
            "dependency analysis gates a change into your main branch."
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "Second green cluster. Wording avoids 'audit' on purpose.",
    },
    {
        "id": "CAIQ-030",
        "section": "H. Secure Development",
        "text": (
            "How is threat modelling and peer code review handled before a change "
            "reaches production?"
        ),
        "expected_tier": GREEN,
        "expected_triggers": [],
        "note": "ssdlc via the code-review pattern.",
    },
    # -- I. Commercial --------------------------------------------------------
    {
        "id": "CAIQ-031",
        "section": "I. Commercial",
        "text": "What is your per-seat cost for the Enterprise tier at our headcount?",
        "expected_tier": RED,
        "expected_triggers": ["pricing_detected", "pricing_never_autogenerated"],
        "note": (
            "gated/pricing forces RED at dispatch, so retrieval is skipped entirely. "
            "The deflection text still carries the word 'pricing', so rule 1 fires too."
        ),
    },
    {
        "id": "CAIQ-032",
        "section": "I. Commercial",
        "text": "What volume discount applies at 5,000 seats on a three-year term?",
        "expected_tier": RED,
        "expected_triggers": ["pricing_detected", "pricing_never_autogenerated"],
        "note": "Discount phrasing, same forced-red path.",
    },
    # -- J. Customer References ----------------------------------------------
    {
        "id": "CAIQ-033",
        "section": "J. Customer References",
        "text": (
            "Northwind Mutual appears in your published materials. Can Northwind Mutual "
            "act as a named reference customer for this evaluation?"
        ),
        "expected_tier": RED,
        "expected_triggers": [],
        "note": (
            "APPROVED reference: Northwind Mutual is public_reference=true with an "
            "unexpired approval, so hard rule 3 does NOT fire. Tier is red rather than "
            "amber for a structural reason - see the divergences section of this "
            "manifest. The observable difference against 034 is the trigger list."
        ),
    },
    {
        "id": "CAIQ-034",
        "section": "J. Customer References",
        "text": (
            "Can Helix Reinsurance act as a client reference for a deployment of "
            "comparable scale?"
        ),
        "expected_tier": RED,
        "expected_triggers": ["unapproved_reference:Helix Reinsurance"],
        "note": "Helix Reinsurance's approval has lapsed, so hard rule 3 fires here and only here.",
    },
    # -- K. Roadmap and Forward Commitments ----------------------------------
    {
        "id": "CAIQ-035",
        "section": "K. Roadmap and Forward Commitments",
        "text": "Will you support FIDO2 passkeys for recipient authentication by Q2 2027?",
        "expected_tier": AMBER,
        "expected_triggers": ["forward_looking_statement"],
        "note": "mfa topic, so green from the primary, then rule 4 demotes.",
    },
    {
        "id": "CAIQ-036",
        "section": "K. Roadmap and Forward Commitments",
        "text": (
            "Will you provide tenant-controlled key rotation scheduling in the EU zone "
            "by Q4 2027?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": ["forward_looking_statement"],
        "note": "key_management topic; the roadmap framing is what demotes it.",
    },
    # -- L. Competitive Positioning ------------------------------------------
    {
        "id": "CAIQ-037",
        "section": "L. Competitive Positioning",
        "text": (
            "Unlike other gateway vendors, does your service avoid retaining plaintext "
            "message bodies at any stage of delivery?"
        ),
        "expected_tier": RED,
        "expected_triggers": ["competitor_disparagement"],
        "note": "Matches no topic pattern, so unclassified; rule 5 forces red regardless.",
    },
    # -- M. Interoperability and Operations ----------------------------------
    {
        "id": "CAIQ-038",
        "section": "M. Interoperability and Operations",
        "text": (
            "How does the service integrate with unified endpoint management tooling for "
            "corporate-managed mobile devices?"
        ),
        "expected_tier": RED,
        "expected_triggers": [],
        "note": (
            "THE FLYWHEEL QUESTION. Unclassified, no authoritative primary, and "
            "deliberately no SME-approved answer, so H is zero and the composite lands "
            "below the amber threshold. This is the cell the SME writes on camera. The "
            "follow-up workbook re-asks it verbatim."
        ),
    },
    {
        "id": "CAIQ-039",
        "section": "M. Interoperability and Operations",
        "text": (
            "Can recipients open encrypted messages through a third-party secure email "
            "gateway from another vendor, without enrolling in your portal?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": [],
        "note": (
            "Unclassified with no primary, but sme-echoworx-0001 covers it, so the H "
            "signal carries the composite into amber. Amber is also the ceiling: the "
            "scorer caps unclassified questions there however strong the composite."
        ),
    },
    {
        "id": "CAIQ-040",
        "section": "M. Interoperability and Operations",
        "text": (
            "Which administrative and delivery event records can we export to our own "
            "SIEM, and in what format?"
        ),
        "expected_tier": AMBER,
        "expected_triggers": [],
        "note": "Unclassified, carried into amber by sme-echoworx-0002.",
    },
]

# The second-run workbook. One question is CAIQ-038 verbatim; the other four are
# verbatim repeats of one question per tier, so the second run has a control at
# each tier and any movement in them is a regression rather than the flywheel.
FOLLOWUP_IDS = ["CAIQ-038", "CAIQ-006", "CAIQ-011", "CAIQ-031", "CAIQ-040"]

FOLLOWUP_NOTES = {
    "CAIQ-038": (
        "The point of the second run. Once the SME's answer has been ingested under "
        "sme-approved/, H rises from 0 to the similarity of an almost-exact match and "
        "the composite crosses the amber threshold, citing the human's own answer. It "
        "stops at amber, not green - see divergences."
    ),
    "CAIQ-006": "Green control. Must not move.",
    "CAIQ-011": "Amber control. Must not move.",
    "CAIQ-031": "Red control. Must not move.",
    "CAIQ-040": "Amber control that already had an SME answer in run one. Must not move.",
}

# Where the shipped code does not do what the unit brief assumed. Recorded in
# the manifest so QA asserts against the code's actual behaviour rather than
# against the brief, and so the divergence is visible rather than folded away.
DIVERGENCES: list[dict] = [
    {
        "topic": "customer_reference tier",
        "questions": ["CAIQ-033", "CAIQ-034"],
        "expected_by_brief": "amber",
        "actual": "red",
        "why": (
            "For topic_class 'gated', confidence_scorer.scorer.score() returns the "
            "composite-derived tier. The customer_reference dispatch plan has "
            "customer_refs_db as its only primary, and the retriever returns no "
            "passages for it, so H is zero (the H query is skipped for every class "
            "except unclassified) and the composite cannot reach the 0.55 amber "
            "threshold: 0.45*0 + 0.25*R + 0.15*C + 0.10*F + 0.05*G peaks at 0.55 only "
            "when R, C and F are all exactly 1.0. Hard rules can only demote, so the "
            "tier stays red. The demo point survives intact - the trigger list differs "
            "between the approved and unapproved reference - but the tier does not."
        ),
    },
    {
        "topic": "step-7 flywheel reaching green",
        "questions": ["CAIQ-038"],
        "expected_by_brief": "green on the second run",
        "actual": "amber on the second run",
        "why": (
            "confidence_scorer.scorer.score() caps every unclassified question at amber "
            "regardless of composite, and 'sme_approved' is not in "
            "_PRIMARY_SOURCE_SYSTEMS, so an ingested SME answer cannot make a question "
            "green by itself. review_api.handler._emit_sme_approved_to_kb() writes the "
            "SME's edit back under source_type=sme_approved_answer, which feeds H and "
            "the citation but not the primary-source test. The observable second-run "
            "change is real and demonstrable - H moves from 0 to near 1, the composite "
            "roughly triples, the tier moves red to amber, and the citation becomes the "
            "SME's own answer - but it stops at amber."
        ),
    },
    {
        "topic": "query-time freshness suppression",
        "questions": ["CAIQ-015", "CAIQ-016"],
        "expected_by_brief": "a freshness-suppression rule drops the expired report",
        "actual": "no query-time suppression exists; three other mechanisms cover it",
        "why": (
            "retriever/handler.py states plainly that freshness suppression no longer "
            "runs at query time - source currency is enforced upstream by event-driven "
            "re-ingestion. What still discriminates on age: scorer.score_f() floors any "
            "passage older than 730 days at 0.2 while the current report scores 1.0; "
            "Provenance.is_expired flips on the past expires_on and render_citation() "
            "suffixes the source line with (EXPIRED); and the divergence detector's "
            "STALE_CONSENSUS finding applies an amber floor when every prior backing an "
            "answer has expired. The fixture is dated to drive all three."
        ),
    },
]


def _make_workbook(title: str, prospect: str, questions: list[dict]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CAIQ v4"

    header_font = Font(bold=True)
    section_fill = PatternFill("solid", fgColor="D9E1F2")
    header_fill = PatternFill("solid", fgColor="D9D9D9")

    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Prospect: {prospect}"])
    ws.append([f"Sent: {datetime.now(timezone.utc).date().isoformat()}"])
    ws.append(["Please respond to each question in the Answer column."])
    ws.append([])
    ws.append(["ID", "Question", "Answer", "Confidence"])
    for cell in ws[6]:
        cell.font = header_font
        cell.fill = header_fill

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 78
    ws.column_dimensions["C"].width = 78
    ws.column_dimensions["D"].width = 12

    current_section: str | None = None
    for q in questions:
        if q["section"] != current_section:
            current_section = q["section"]
            ws.append([current_section])
            for cell in ws[ws.max_row]:
                cell.fill = section_fill
                cell.font = header_font
        ws.append([q["id"], q["text"], None, None])
        ws.cell(row=ws.max_row, column=2).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[ws.max_row].height = 42

    return wb


def _row_for(questions: list[dict]) -> dict[str, int]:
    """Reproduce the row each question lands on, so the manifest can name it."""
    rows: dict[str, int] = {}
    row = 6
    current_section = None
    for q in questions:
        if q["section"] != current_section:
            current_section = q["section"]
            row += 1
        row += 1
        rows[q["id"]] = row
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    wb = _make_workbook(
        "Echoworx - Cloud Security Alliance CAIQ v4.0.3",
        "Cascadia Trust Bank - Third Party Risk (fictional)",
        QUESTIONS,
    )
    wb.save(OUT / FIXTURE_NAME)
    print(f"Created {OUT / FIXTURE_NAME} ({len(QUESTIONS)} questions)")

    by_id = {q["id"]: q for q in QUESTIONS}
    followup = [
        {**by_id[qid], "section": "A. Follow-up", "note": FOLLOWUP_NOTES[qid]}
        for qid in FOLLOWUP_IDS
    ]
    wb2 = _make_workbook(
        "Echoworx - CAIQ v4.0.3 Follow-up Round",
        "Cascadia Trust Bank - Third Party Risk (fictional)",
        followup,
    )
    wb2.save(OUT / FOLLOWUP_NAME)
    print(f"Created {OUT / FOLLOWUP_NAME} ({len(followup)} questions)")

    rows = _row_for(QUESTIONS)
    tier_counts: dict[str, int] = {GREEN: 0, AMBER: 0, RED: 0}
    for q in QUESTIONS:
        tier_counts[q["expected_tier"]] += 1

    all_triggers = sorted({t for q in QUESTIONS for t in q["expected_triggers"]})

    manifest = {
        "fixture": f"data/incoming/{FIXTURE_NAME}",
        "followup_fixture": f"data/incoming/{FOLLOWUP_NAME}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/create_echoworx_fixture.py",
        "verifier": "scripts/verify_fixture.py",
        "questionnaire_standard": "CSA CAIQ v4.0.3",
        "expected_tier_counts": tier_counts,
        "tier_spread_floor": {"green": 12, "amber": 20, "red": 3},
        "expected_triggers_union": all_triggers,
        "required_trigger_coverage": [
            "pricing_detected",
            "compliance_claim",
            "sla_claim",
            "unapproved_reference:*",
            "forward_looking_statement",
            "competitor_disparagement",
        ],
        "approved_reference_customers": ["Northwind Mutual", "Cascadia Trust Bank"],
        "unapproved_reference_customers": [
            "Meridian Health Group",
            "Province of Rideau - Digital Services",
            "Anvil Capital Partners",
            "Helix Reinsurance",
        ],
        "expected_suppressed_documents": [
            {
                "document_id": "soc2_type2_2024_expired",
                "superseded_by": "soc2_type2_2026",
                "reason": "expires_on 2025-06-30 is in the past; updated_at is ~26 months old",
                "on_questions": ["CAIQ-015", "CAIQ-016"],
                "assert": [
                    "the superseded report is reachable in the corpus",
                    "Provenance.is_expired is true and render_citation() ends with (EXPIRED)",
                    "score_f floors the superseded report at 0.2 and the current one at 1.0",
                    "soc2_type2_2026 is retrieved on both questions",
                    "both questions are amber, so a human reviews before anything ships",
                ],
                "not_asserted": (
                    "retrieval rank. No query-time freshness suppression exists in "
                    "retriever/handler.py, so the superseded report can and does outrank "
                    "the current one on CAIQ-015, which asks about it by date. The "
                    "(EXPIRED) citation suffix and the amber tier are what carry the "
                    "safety here, not rank."
                ),
            }
        ],
        "questions": [
            {
                "id": q["id"],
                "row": rows[q["id"]],
                "section": q["section"],
                "text": q["text"],
                "expected_tier": q["expected_tier"],
                "expected_triggers": q["expected_triggers"],
                "note": q["note"],
            }
            for q in QUESTIONS
        ],
        "followup": {
            "fixture": f"data/incoming/{FOLLOWUP_NAME}",
            "premise": (
                "Run two, after the SME's answer to CAIQ-038 has been approved in the "
                "review UI and written back to the knowledge base under sme-approved/."
            ),
            "questions": [
                {
                    "id": qid,
                    "text": by_id[qid]["text"],
                    "tier_run_one": by_id[qid]["expected_tier"],
                    "tier_run_two": (AMBER if qid == "CAIQ-038" else by_id[qid]["expected_tier"]),
                    "note": FOLLOWUP_NOTES[qid],
                }
                for qid in FOLLOWUP_IDS
            ],
        },
        "divergences_from_brief": DIVERGENCES,
    }

    dest = OUT / MANIFEST_NAME
    dest.write_text(json.dumps(manifest, indent=2))
    print(f"Created {dest}")
    print(
        f"  expected spread: green={tier_counts[GREEN]} "
        f"amber={tier_counts[AMBER]} red={tier_counts[RED]}"
    )


if __name__ == "__main__":
    main()
