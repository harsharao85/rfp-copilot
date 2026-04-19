# RFP Redlining Copilot — Architecture Plan

**Owner:** H
**Status:** Draft v0.4 (RFP focus)
**Last updated:** 2026-04-14

> **Positioning:** A Sales-facing copilot that takes an incoming RFP/RFI Excel, auto-answers questions with citations, scores each answer's confidence, and returns the same Excel with color-coded highlights (green / amber / red) for rep and SME review. The flywheel: SME-approved edits feed back into the answer library, lifting green-answer % over time.

---

## 1. Problem statement

Enterprise sales teams spend 40–80 hours on a large RFP, most of it re-answering questions that have been answered many times before. Build an AWS-native workflow that:

1. Accepts an RFP/RFI Excel upload from an authenticated sales user.
2. Parses the workbook into a normalized question set regardless of format (merged cells, multi-sheet, embedded instructions).
3. For each question, retrieves evidence across Seismic, Gong transcripts, CMS, historical RFPs, and a curated content corpus.
4. Generates an on-brand answer with source citations.
5. Computes a composite confidence score and color-codes the answer cell (green / amber / red).
6. Applies hard legal rules: pricing → red, compliance claims → amber minimum, reference customers → approval-gated.
7. Writes answers + confidences + citations back into the original Excel and notifies the rep.
8. Routes amber / red answers through an SME review UI; approved edits flow back into the historical corpus.
9. Emits telemetry for A/B measurement of win-rate and time-per-RFP.

---

## 2. Strategic framing

| Dimension | Position |
|---|---|
| **What this is** | A first-draft accelerator for RFP responses, with confidence-graded SME review. |
| **What this is not** | An autonomous responder. Every RFP goes to a human before it goes to the prospect. |
| **Primary users** | Sales reps (upload + edit), Sales Ops / Deal Desk (oversight), Pre-Sales Engineers / SMEs (review amber/red), Product Marketing (owns answer library). |
| **Excluded scope (v1)** | Pricing, SLA commitments, commercial terms, custom contract language — forced to red; commercial desk owns. |
| **Pilot scope** | **Security questionnaires first.** Highest repetition, clearest ROI, lowest legal variance. Expand to technical/functional RFPs on evidence. |
| **Success narrative** | 50% reduction in hours per RFP; green-answer % at first draft rising 40% → 70% over 6 months (flywheel); measurable +3–5pp win-rate lift vs. control. |

---

## 3. Recommended architecture (one-paragraph summary)

A serverless, event-driven pipeline where **AWS Step Functions (Standard) with Distributed Map** iterates across questions in parallel. **Amazon Kendra** is the unified retrieval layer indexing Seismic, Gong, CMS, historical RFPs, and S3; **Amazon Q Business** reuses the same Kendra index as an interactive assistant for reps during live RFP triage (one index, two consumers). **Amazon Neptune Serverless** holds a relationship graph that answers queries Kendra cannot: similar-prior-answers, reference-customer match, SME provenance. **Amazon Bedrock** (Claude Sonnet 4.6 for synthesis, Claude Haiku 4.5 for classification and confidence math) generates answers under **Bedrock Guardrails** enforcing pricing / compliance / reference-customer hard rules. A **Lambda container with openpyxl** parses and rewrites Excel, preserving formatting and applying cell fill colors. **DynamoDB** holds job state, per-question results, and the closed-loop SME feedback. **IAM Identity Center + Verified Permissions** gate access. QuickSight surfaces ops and A/B metrics.

---

## 4. End-to-end flow

```
[Sales rep] → Amplify/CloudFront → API Gateway → S3 upload (KMS-CMK, Object Lock)
                                                      │
                                                      ▼
                                         EventBridge → Step Functions Standard
                                                      │
                                                      ▼
                       ┌────────────────────────────────────────────────────────┐
                       │  1. Parse & classify workbook (openpyxl + Haiku 4.5)  │
                       │     → normalized questions in DynamoDB                 │
                       └────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                       ┌────────────────────────────────────────────────────────┐
                       │  2. Distributed Map (concurrency ≤ 25)                 │
                       │     per question:                                      │
                       │     • Kendra retrieval across all sources              │
                       │     • Neptune graph queries (prior answers,            │
                       │       refs, SME provenance)                            │
                       │     • Bedrock Sonnet 4.6 synthesis + Guardrails        │
                       │     • Composite confidence scoring                     │
                       │     • Hard-rule override (pricing/compliance/refs)     │
                       └────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                       ┌────────────────────────────────────────────────────────┐
                       │  3. Excel writer Lambda (openpyxl)                     │
                       │     → answers + confidences + citations +              │
                       │       fill colors + summary sheet                      │
                       │     → output S3 bucket                                 │
                       └────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                       ┌────────────────────────────────────────────────────────┐
                       │  4. SES / Slack notification + signed S3 URL           │
                       │  5. SME review UI for amber/red (WebSocket + React)    │
                       │  6. Approved edits → historical RFP corpus via         │
                       │     Kendra re-index + Neptune edge update (flywheel)   │
                       └────────────────────────────────────────────────────────┘

Parallel: Q Business (same Kendra index) — rep-facing interactive chat
          for live RFP triage, reference lookups, ad-hoc questions.
```

---

## 5. Component breakdown

### 5.1 Front door
- Amplify Hosting + CloudFront; simple Excel upload UI and job status dashboard.
- API Gateway REST for upload + control; WebSocket for SME review UI.
- **IAM Identity Center** for SSO (mandatory for Q Business).
- **AWS Verified Permissions (Cedar)** for role-based access — rep uploads, deal desk oversight, SME reviews, product marketing curates library.

### 5.2 Ingestion
- Pre-signed S3 PUT URL; bucket has KMS-CMK encryption, Object Lock, Block Public Access, Macie scanning.
- S3 event → EventBridge rule → Step Functions execution.
- Incoming RFPs tagged `nda_status` and `customer_name`; **never auto-ingested** into the reference corpus.

### 5.3 Parse & classify (the least-glamorous, most-time-consuming stage)
- Lambda container (Python + openpyxl).
- Enumerates every sheet, every cell; Haiku 4.5 classifier labels cells as `question | section_header | instruction | answer_cell | metadata`.
- Handles merged cells, multi-sheet workbooks, question numbering in various columns, answer columns to the right or below.
- Output: normalized records in DynamoDB (`job_id`, `question_id`, `text`, `section`, `context`, `answer_cell_ref`, `confidence_cell_ref`).
- Three or four real-world RFP samples used as test fixtures — parser quality is a gating factor.

### 5.4 Retrieval layer: Kendra
- **Amazon Kendra** is the unified retrieval engine.
- Indexed sources:
  - **Seismic** via custom connector (REST API + OAuth2; delta sync on an hourly schedule).
  - **Gong** transcripts via custom connector (Calls API; filter to customer-facing calls only).
  - **CMS** via native Kendra connector or S3 export.
  - **Historical won/lost RFPs** via S3 connector, with metadata tags (`win_loss`, `industry`, `deal_size`, `sme_approved`).
  - **Product documentation** / knowledge base via Confluence or SharePoint connector.
- ACL inheritance from sources; Kendra filters results by the requesting user's Identity Center groups.
- Returns top-K passages with confidence scores, used as signal R in the composite confidence.

### 5.5 Graph layer: Neptune Serverless
Neptune earns its place only by answering queries Kendra cannot. The committed named queries for v1:

1. **Similar prior answer**: "Find top-3 SME-approved answers to questions semantically similar to this one." Nodes: Question, Answer, SME, Topic. Edges: `answers`, `approved_by`, `tagged_with`. Used as the highest-weighted confidence signal (H).
2. **Reference customer match**: "Find customer references matching industry + deployment pattern + contract size + has `public_reference: true` with unexpired approval." Nodes: Customer, Industry, ProductArea, CaseStudy. Edges: `deployed`, `in_industry`, `approved_until`.
3. **SME provenance**: "Who last approved an answer on this topic, and when? Is it stale?" Drives amber escalation if most-recent approval is older than 12 months.
4. **Competitive context**: "Which competitors appear most frequently in questions from this industry?" Drives tone and positioning in answer generation.

Graph loaded from DynamoDB snapshots + Kendra metadata; refreshed nightly via a Glue job. ACLs enforced at query time in the plugin Lambda using Identity Center group claims.

### 5.6 Generation: Bedrock
- **Claude Sonnet 4.6** for answer synthesis.
- **Claude Haiku 4.5** for cell classification, compound-question decomposition, distractor filtering, citation formatting.
- System prompt includes brand-voice guidelines and an "answer pattern library" (approved answer structures by question type).
- **Prompt caching** enabled on the stable portion (voice guidelines, hard rules, answer patterns) — 5–10x cost reduction on repeat questions.
- **Bedrock Guardrails**: denied topics (pricing specifics, unqualified compliance claims, competitor disparagement), PII filters, tone drift.

### 5.7 Confidence scoring (defensible methodology)

Composite score, not LLM self-report (which is unreliable):

| Signal | Weight | Source | Rationale |
|---|---|---|---|
| **H** — Prior-answer match | 0.45 | Cosine similarity to nearest SME-approved historical answer (Neptune query 1) | Highest signal: a prior-approved answer is the gold standard |
| **R** — Retrieval strength | 0.25 | Top-K Kendra passage score, normalized 0–1 | Quality of raw evidence |
| **C** — Source coverage | 0.15 | 1.0 if ≥2 independent sources agree; 0.5 if single source; 0.0 if no support | Hedges single-source errors |
| **F** — Freshness | 0.10 | Decay: <90d=1.0, <1yr=0.7, <2yr=0.4, else 0.2 | Penalizes stale content |
| **G** — Guardrail clean | 0.05 | 1.0 if no flags, 0.0 if flagged | Small but present penalty on risky content |

`confidence = 0.45·H + 0.25·R + 0.15·C + 0.10·F + 0.05·G`

**Thresholds**: ≥0.80 **green**, 0.55–0.80 **amber**, <0.55 **red**.

**Hard-rule overrides (applied after scoring)**:
- Answer contains pricing / SLA / commercial language → **forced red**, commercial desk owns.
- Answer contains compliance/certification claim (SOC2, ISO, FedRAMP, HIPAA, GDPR, PCI) → **minimum amber**, compliance team owns.
- Answer cites a customer reference → **minimum amber** unless Neptune confirms `public_reference: true` with unexpired approval.
- Answer confidence H=0 (no comparable prior answer exists) → **maximum amber** regardless of R/C/F.

Weights tuned offline against a labeled dataset of historical RFP responses (see §14 success metrics).

### 5.8 Excel writer
- Lambda container (openpyxl).
- Reads the original workbook, preserves all formatting.
- Injects: answer in the identified answer cell, confidence score in an adjacent column, source citations as cell comments.
- Applies fill color: green `#C6EFCE`, amber `#FFEB9C`, red `#FFC7CE`.
- Adds a **Summary sheet**: counts by tier, SLA clock, commercial/compliance/reference flags requiring attention, citation source mix, estimated rep review time.
- Output file to S3 with versioning; signed URL emailed via SES.

### 5.9 Interactive assistant: Q Business
- **Same Kendra index** as the batch pipeline (one index, two consumers).
- Used by reps and deal desk for live triage: "what reference customers in fintech for 10K+ employee companies?", "find our latest SOC2 bridge letter," "summarize the last Gong call with Acme."
- Q Apps published to Identity Center groups for persona-scoped experiences.
- Does **not** orchestrate the batch workflow — Step Functions owns that.

### 5.10 Human-in-the-loop: SME review
- After batch generation, workflow enters `waiting_for_review` state.
- SME review UI (React + WebSocket) lists amber + red questions with draft answer, citations, and "why amber/red" explanation.
- Actions: **approve**, **edit-and-approve**, **reject with feedback**.
- Approved edits tagged `sme_approved: true` and **fed back into the historical corpus** (Kendra re-index + Neptune edge update). This is the flywheel.
- SME review SLA: target median <15 minutes per amber, <30 minutes per red.

### 5.11 State, audit, observability
- **DynamoDB** tables: `jobs`, `questions`, `answers`, `reviews`, `library_feedback`.
- **CloudWatch Logs + X-Ray** on all Lambdas.
- **Bedrock model invocation logging** to dedicated S3 + **CloudTrail data events** — full audit trail of prompts, responses, citations, reviewer actions.
- **QuickSight** dashboards: ops (time-per-RFP, green-%, throughput), SME (review queue, edit rate per topic), leadership (win-rate A/B).

---

## 6. Data sources & connectors

| Source | Connector | Effort | Notes |
|---|---|---|---|
| S3 (historical RFPs, whitepapers) | **Native Kendra** | Low | Gold source; tag with `sme_approved`, `win_loss`, `industry`, `deal_size`, `expires_on` |
| Confluence / SharePoint | **Native Kendra** | Low | Product knowledge, enablement content |
| CMS | **Native Kendra** (if WordPress/Drupal) or S3 export | Low–Med | Depends on CMS |
| **Seismic** | **Custom Kendra connector** | 3–5 days | Content API + OAuth2; handles delta sync via `modifiedSince` |
| **Gong** | **Custom Kendra connector** | 3–5 days | Calls API; filter to customer-facing calls; transcript cleaning |
| Salesforce (CRM context) | **Native Kendra** | Low | Account/opportunity context for scoping retrieval by deal |

For the demo: mock Seismic and Gong via S3 snapshots and use Kendra's S3 connector. Call out the custom-connector gap explicitly in the demo narrative.

---

## 7. Governance & legal guardrails

**Content policy (enforced in Guardrails + post-generation rules)**

- **Pricing / commercial**: never auto-answered, always red. Commercial desk owns.
- **Compliance claims**: always amber minimum. Compliance team owns; claim must link to a current certification artifact.
- **Reference customers**: never named unless Neptune confirms `public_reference: true` with valid approval. Logo rights + case study approval statuses modeled explicitly.
- **Competitive mentions**: disparagement filter; answers must not name competitors negatively.
- **Forward-looking statements**: "will deliver by Q3" language flagged to legal review.

**Data policy**

- Incoming RFPs are NDA'd to the prospect and **not auto-ingested** into reference corpus. A deliberate approval step is required post-deal-close.
- Outgoing answers never include prospect's proprietary info from their RFP (prompt injection defense).
- Gong transcripts filtered by opt-in consent status per account.

**Ownership (RACI)**

| Activity | Responsible | Accountable | Consulted |
|---|---|---|---|
| Master answer library | Product Marketing | VP Marketing / CRO | Sales Ops, Compliance |
| Hard-rule policy (pricing/compliance/refs) | Legal + Compliance | General Counsel | Sales Ops |
| Connector and index health | Platform Eng | Head of Enterprise AI | Sales Ops |
| SME review pool | Pre-Sales / Solutions Eng | VP Solutions Eng | Sales |
| Win-rate measurement | RevOps | CRO | Finance |
| Security & data protection | InfoSec | CISO | Legal |

---

## 8. Security architecture

- VPC-isolated compute; PrivateLink endpoints to Bedrock, Kendra, S3, Secrets Manager, DynamoDB, Neptune, KMS.
- KMS CMKs per data domain (incoming RFPs, reference corpus, Gong transcripts, output).
- S3 Object Lock + Macie scanning on incoming RFP bucket.
- Bedrock Guardrails on every invocation; prompt-injection detection via input sanitization Lambda.
- IAM Identity Center for SSO; Verified Permissions (Cedar) for per-persona access policies.
- Secrets Manager rotates all source-system credentials (Seismic, Gong, Salesforce); short-lived STS roles for cross-account.
- GuardDuty, Security Hub, CloudTrail organization trail.
- Per-call audit record: actor, job ID, question ID, model ID, prompt hash, response hash, retrieval sources, confidence score, hard-rule triggers, reviewer actions.

---

## 9. Why this stack vs. alternatives

**Why Kendra + Bedrock instead of Q Business for the batch path?**
Q Business is a full chat application; calling its `ChatSync` API in a 100-question batch is fighting the product. Kendra is the retrieval primitive underneath Q; using Kendra + Bedrock directly gives deterministic, typed, auditable calls. Q Business stays in its lane as the interactive assistant sharing the same Kendra index.

**Why Kendra instead of Bedrock Knowledge Bases?**
Kendra has native connectors for the sources Sales actually uses (Salesforce, Confluence, SharePoint, ServiceNow) and inherits their ACLs automatically. Knowledge Bases is vector-only and ACL-naïve — acceptable for internal unstructured content, weaker for multi-source enterprise with strict access control. Kendra's license cost is higher; the connector + ACL savings typically outweigh it.

**Why Step Functions Distributed Map?**
Parallel-fans across a dynamic array of questions with back-pressure, retries, and observability; alternatives (EventBridge fanout, SQS + Lambda) give up the execution graph and audit trail. Distributed Map was designed for this pattern.

**Why Neptune Serverless over a SQL/DynamoDB relationship table?**
Multi-hop queries ("similar questions answered by SMEs who currently support this industry") are natural in Gremlin, painful in SQL. Serverless billing model matches bursty RFP workload. Three or four committed queries justify it — we don't add Neptune without them.

**Why a composite confidence score vs. LLM self-report?**
LLM self-reported confidence is empirically miscalibrated — high-confidence hallucinations are the dominant failure mode. A weighted composite grounded in retrieval strength, historical-match similarity, source coverage, and freshness is tunable, auditable, and defensible in front of a sales leader asking "why should I trust the green?"

---

## 10. Demo scope (what gets built for the presentation)

A tight vertical slice showing the full pipeline on a **security questionnaire** (narrowest, most repetitive, highest-ROI category). Portfolio-grade and SAP-C02 aligned.

1. Cognito sign-in; Amplify upload UI.
2. S3 → EventBridge → Step Functions.
3. Excel parser handling a real-world security questionnaire format (one sheet, ~30 questions, merged cells, mixed instructions).
4. **Kendra index** populated from:
   - 3 prior "won" security questionnaires (historical RFPs, tagged `sme_approved`).
   - ~30 synthetic whitepapers / policies / SOC2 bridge letters / data flow diagrams.
   - Mocked Seismic snapshot (~20 content cards).
   - Mocked Gong transcript snapshot (~10 transcripts).
5. **Neptune Serverless** with the four named queries wired in; loaded from a synthetic customer/product/topic graph.
6. Distributed Map processing ~30 questions with concurrency 10.
7. Composite confidence scoring with weights as defined in §5.7.
8. Hard-rule overrides demonstrated: one pricing question forced red, one compliance claim forced amber, one reference-customer claim gated by Neptune approval.
9. Excel writer producing the output with green/amber/red fills, citations as comments, summary sheet.
10. **SME review UI** (React + WebSocket) for amber/red; approved edits written back and a second run shows a previously-amber question flipping to green (flywheel demonstrated).
11. **Q Business app** sharing the same Kendra index — demonstrate interactive lookup as a complementary surface.
12. QuickSight dashboard with time-per-question, tier distribution, source mix.

**Explicitly out of scope for demo**: real Seismic/Gong connectors (mocked via S3), Salesforce integration, cross-tenant multi-tenancy, full A/B harness, rate-limit handling at production scale.

---

## 11. Synthetic data plan

Fully fictitious. No real customer names, no lightly-anonymized internal docs.

| Asset | Volume | Generator |
|---|---|---|
| Incoming RFP Excel (security questionnaire) | 1 workbook, ~30 questions | Claude generation, based on public security questionnaire templates |
| Historical won RFPs | 3 workbooks, ~40 Q&A each, tagged `sme_approved` | Claude-generated, manually spot-reviewed |
| Seismic mock content | ~20 cards (PDFs + short HTML) | Claude + simple PDF layout |
| Gong transcripts | ~10 transcripts, mixed discovery / demo / objection-handling | Claude-generated |
| Whitepapers / policies / SOC2 bridge letter | ~15 docs | Claude-generated |
| Customer reference graph | ~40 customers, 6 industries, approval-status mix | Python + Faker |
| Product/topic/competency graph | ~80 nodes, ~300 edges | Python script; loaded via Gremlin |

Generator scripted so the demo can be **reset to a known state in <5 minutes** — essential for repeat presentations.

---

## 12. Build sequence

| Phase | Duration | Deliverables |
|---|---|---|
| 0. Foundations | 0.5 day | AWS account hardening, IaC repo (CDK-TS), IAM Identity Center, CI/CD via GitHub Actions OIDC |
| 1. Synthetic data generation | 1.5 days | All assets in §11; reset script; test fixtures |
| 2. Ingestion + Excel parser | 1.5 days | Upload UI, S3, EventBridge, openpyxl Lambda container, Haiku classifier, DynamoDB schema |
| 3. Kendra index | 1 day | S3 connector (for real + mocked sources), ACL metadata, custom attributes |
| 4. Neptune graph | 1 day | Serverless cluster, Gremlin load script, the four named queries as Lambda plugins |
| 5. Step Functions Distributed Map | 1 day | Retrieval + generation + scoring per question, concurrency caps, retries |
| 6. Confidence scoring + hard rules | 1 day | Composite scorer, post-generation rule engine, Guardrail policy |
| 7. Excel writer | 1 day | openpyxl writer with fill colors, citations as comments, summary sheet |
| 8. SME review UI | 1.5 days | React app, WebSocket, approve/edit/reject, feedback loop into Kendra/Neptune |
| 9. Q Business surface | 0.5 day | Q Business app on same Kendra index; persona permissions |
| 10. Telemetry + QuickSight | 1 day | DynamoDB streams → metrics, three dashboards |
| 11. Security hardening | 1 day | KMS CMKs, VPC endpoints, Guardrails, Verified Permissions, prompt-injection sanitizer |
| 12. Demo polish | 1 day | Script, talk-track, architecture diagram, recorded walkthrough |

**Total: ~13 days.** Compressible to ~9 days if we cut the SME review UI to a stub and the QuickSight dashboard to a single page.

---

## 13. Cost envelope

**POC (idle-heavy, demo-use):**

| Service | Monthly est. |
|---|---|
| Kendra Developer Edition | ~$810 (largest line; Enterprise starts at $1,008) |
| Bedrock (Sonnet 4.6 + Haiku 4.5, ~200 questions/day demo) | $30–80 |
| Neptune Serverless (min 2.5 NCU, scales to 0) | $50–120 |
| Step Functions Standard | <$5 |
| Lambda, API Gateway, S3, DynamoDB, EventBridge | <$20 |
| Q Business (1 Pro seat) | $40 |
| QuickSight (1 author) | $24 |
| KMS, Secrets Manager, CloudWatch | ~$10 |
| **Total POC** | **~$1,050–1,150 / month** |

**Kendra is the dominant cost** and is why many teams default to Bedrock Knowledge Bases. Defense: Kendra's ACL inheritance and connector breadth pay for themselves the first time the audit team asks "prove Sales can't see HR content."

**Production levers**: Kendra query caching, prompt caching on stable prompt prefix, Haiku for sub-tasks, right-size Neptune NCUs, reserved QuickSight capacity, Bedrock provisioned throughput if volume is high and predictable.

---

## 14. Success metrics & A/B test design

| Metric | Target (Year 1) | Measurement |
|---|---|---|
| Hours per RFP (assisted vs. control) | −50% | Rep self-report + Step Functions execution time + SME review duration |
| Green-answer % at first draft | 40% month 1 → 70% month 6 | Confidence distribution per RFP; tracked in DynamoDB |
| SME edit rate on amber | Trending down | Diff volume between draft and approved version |
| Win rate on AI-assisted RFPs vs. control | +3 to +5 pp | **Matched A/B**: alternate eligible RFPs into assisted vs. control by deal size and segment |
| Library reuse rate | Rising | Count of times each SME-approved answer is cited |
| Mean time to draft for 100-question RFP | <30 min | Step Functions duration |
| Zero-confidence (red) rate | <15% | Distribution per RFP |
| Hard-rule override triggers per RFP | Tracked, not gated | Pricing/compliance/ref counts |

**A/B harness**: every qualifying incoming RFP is randomly assigned `assisted` or `control` (50/50) by RevOps automation based on deal size bucket and industry. Control RFPs use the current manual process. Outcomes (hours, win/loss, deal size) tracked in Salesforce with an `rfp_assist_flag` field. Significance target: 40 RFPs per arm over a quarter for a detectable win-rate lift.

---

## 15. Build vs. buy

| Dimension | Build (this plan) | Buy (Loopio, Responsive, Arphie, AutoRFP) |
|---|---|---|
| Annual cost (mid-market) | ~$15–30K infra | $100–300K license |
| Time to value | 2–3 weeks POC, 2–3 months prod | Weeks (connectors pre-built) |
| Connector breadth | Build for what you need | Pre-built for common sources |
| Proprietary data handling | Fully in your AWS account | Vendor-hosted (usually) |
| Customization (confidence scoring, hard rules, brand voice) | Unlimited | Constrained by vendor roadmap |
| Best for | Large enterprises with proprietary data, strict data residency, specialized connectors | Mid-market orgs, standard sources, speed to value |

**Recommendation**: build wins at the enterprise tier (proprietary data, data-residency, connector customization); buy wins at the mid-market. A consultant's pitch must include this paragraph — it demonstrates we evaluated the make-or-buy tradeoff on behalf of the client.

---

## 16. Open questions

- Which sources are real for the client (Seismic / Gong / Salesforce) and which are substitutes? Drives connector effort and timeline.
- Who owns the master answer library operationally — Product Marketing, Sales Enablement, or a dedicated content ops role?
- Data residency: any jurisdictional constraints (EU, UK, APAC)?
- Gong consent: is call transcription opt-in by account, and how is that status reconciled into the corpus filter?
- SME pool: Pre-Sales Engineers, Solutions Consultants, or dedicated reviewers? Drives review SLA realism.
- Which CRM field carries the A/B flag, and which RevOps analyst owns the measurement?

---

## 17. Next actions

1. Lock the demo scope (§10) with the presentation audience in mind.
2. Generate synthetic data per §11 (2 days — largest single block).
3. Bootstrap the IaC repo and Step Functions ASL skeleton.
4. Build the Excel parser first — it's the riskiest unknown.
5. Dry-run the demo against a real (sanitized) security questionnaire before the presentation.
6. Rehearse against the FAQ (`rfp-redlining-faqs.md`).

---

## Appendix A — Companion documents

- **`rfp-redlining-faqs.md`** — technical FAQ for demo defense.
- **`archive/ai-training-orchestration-plan-v0.3.md`** — prior direction (L&D copilot), archived.
