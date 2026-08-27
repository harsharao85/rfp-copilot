# Notion "PoC Factory" schema (contract for agents)

Source: poc-factory-init bundle, `assets/bundle/docs/notion-schema.md`. Vendored here 2026-08-27.

Root page: PoC Factory. Six databases. Every non-root record relates to exactly one Engagement.

## Engagements
Engagement (title) · Engagement ID (ENG-n) · Client · Vertical · Status · Current Bolt · Current Gate ·
Brief Summary · Success Criteria · Non-Goals · AWS Account ID · AWS Region · Budget Ceiling (USD) ·
Account TTL · Repo URL · Human Lead · Brief Source (files) · Created · Last Edited

## Units of Work
Unit of Work (title) · UoW ID (UOW-n) · Engagement (rel) · Phase (Inception/Construction/Operations) ·
Size (S/M/L) · Status (Backlog/Ready/In Progress/In Review/Done/Blocked) · Test Status (Not Run/Pass/Fail) ·
Acceptance Criteria · Architecture Notes · Implementation Notes · PR URL · Owning Agent · Bolt

## Decisions (ADRs)
Decision (title) · ADR ID (ADR-n) · Engagement (rel) · Category (Compute/Data/Auth/Integration/AI Model/Network/Other) ·
Status (Proposed/Accepted/Rejected/Superseded) · Context · Options Considered · Decision · Consequences ·
Cost Note · Security Note · Proposed By (agent) · Approved By (people) · Bolt

## Risks & Findings
Finding (title) · RISK ID · Engagement (rel) · Type (Security/Compliance/Cost/Quality/Assumption/Open Question) ·
Severity (Critical/High/Medium/Low) · Blocking (checkbox) · Status (Open/In Progress/Resolved/Accepted Risk) ·
Evidence · Proposed Fix · Resolution · Owning Agent · Bolt · Related UoW (rel) · Related ADR (rel)

## Gates
Gate row (title) · Engagement (rel) · Gate (G1..G5) · Verdict (Pending/Approved/Changes Requested/Rejected) ·
Reviewer (people) · Evidence Links · Reviewer Comment · Requested (created time) · Decided (date) · Bolt

## Artifacts
Artifact (title) · Engagement (rel) · Type (Diagram/Threat Model/Control Mapping/Cost Estimate/Test Report/
Runbook/Environment Manifest/Deck/Exec Summary/Demo Script) · URL · File · Version · Owning Agent · Bolt

Write-scope matrix is in each agent's "Owns in Notion" section. Anything else is read-only.

## Live IDs (VeUP workspace)

| Object | Data source ID |
|---|---|
| Engagements | fe8bd16a-9c06-490d-b76f-0427a94a4a5a |
| Units of Work | de21c35d-e93c-42d9-a49d-ba0ec5a0bba2 |
| Decisions | 1fffe988-b2bc-4b46-be44-a48d35177fd4 |
| Risks & Findings | 57a51433-a145-4a4e-83a4-48e66c33390b |
| Gates | a81313be-ea9c-4adc-906f-52edf72461ff |
| Artifacts | 794f7507-8e2d-4c75-ac3e-2fa5f6bf7b9a |

Root page: https://app.notion.com/p/3c92be538d7781249b8fd151ac5099fd

Note: the Gates row must carry the plain-text `Engagement ID` (e.g. `ENG-2`) in addition to the
relation, because `gate_guard.py` filters on it without resolving relations.
