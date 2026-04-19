# RFP Copilot — Live Demo Script

**Duration:** ~10 minutes. Acts 1–2 are the core demo (5 min). Acts 3–4 are the governance deep-dive for technical audiences. Backend verification commands are in `docs/test-guide.md` — this script covers what the audience sees and hears.

---

## Pre-flight checklist

Complete before the audience arrives:

1. `cd rfp-copilot/ui && python3 -m http.server 8080`
2. Open two browser tabs: `http://localhost:8080/upload.html` and `http://localhost:8080/review.html`
3. In `upload.html`: paste Upload API URL → "Upload API" field, paste Review API URL → "Review API" field, click **Save**
4. In `review.html`: paste Review API URL, click **Load** — confirm "No pending reviews" or a clean state
5. Confirm `data/sample_rfp.xlsx` is present
6. Optionally run `bash scripts/reset-demo.sh` to clear prior runs

**API URLs** are in `docs/test-guide.md` §1 resource table.

---

## Act 1: Upload & Pipeline (2 min)

### Moment 1 — Upload flow

**Action:** In `upload.html`, drag `data/sample_rfp.xlsx` into the drop zone.

Watch the progress bar advance through:
- "Requesting upload URL…" → "Uploading…" → "Upload complete. Starting pipeline…" → "Polling status…"

*"The rep drags in the same Excel they received. The upload hits S3 directly via a pre-signed PUT URL — the server never touches the file bytes. Then a single API call kicks off the Step Functions state machine."*

**Action:** Watch the status badge: `PROCESSING` (blue) → `WAITING_FOR_REVIEW` (amber) — takes ~90 seconds.

*"The pipeline fans out 10 questions in parallel using Step Functions Map. When every question is answered and the output Excel is written, the execution parks at a `waitForTaskToken` gate. It literally cannot proceed until a human approves. There's no polling loop, no timeout hack — Step Functions holds the token."*

**Action:** Click **"Open Review UI →"**. The browser navigates to `review.html?api=...&job=...`.

*"Notice the review UI loaded without me typing anything. The upload page deep-linked into the review UI with the job ID in the query string. The API URL is pre-populated from localStorage."*

---

## Act 2: SME Review with Citations (3 min)

The review UI shows only amber and red questions — green answers don't require human review.

### Moment 2 — Source citation provenance

**Action:** Find any question with sources (SEC-006 encryption if shown, otherwise any amber). Click **"▶ Sources (N)"** to expand the citations panel.

Point to the badge colours:
- Blue = Primary (compliance cert, security whitepaper) — authoritative
- Amber = Secondary (prior RFP) — phrasing reference only
- Gray = Tertiary (Seismic, Gong) — context only

*"Every answer traces back to its source. Prior RFPs get a yellow badge — they're phrasing references, not factual authority. The system can't use a three-year-old RFP answer to assert SOC2 compliance today. That distinction is architectural, not a prompt instruction."*

### Moment 3 — Confidence breakdown

**Action:** On the same question, point to the confidence component display next to the tier pill: `H₄₅%  R₂₅%  C₁₅%  F₁₀%  G₅%`

*"This isn't LLM self-reported confidence. It's a deterministic composite. H is the hallucination check — 45% of the score. If H is zero, the answer is capped at amber regardless of how strong the other signals are. The model cannot grade its own output. An independent classification call runs after generation."*

### Moment 4 — Hard rules: pricing forced RED

**Action:** Scroll to SEC-028 (per-user pricing). Show the RED badge and `pricing_detected` trigger tag.

*"Before this even reached the model, Bedrock Guardrails blocked the pricing content. After generation, the hard rules engine saw the pricing trigger and forced the tier to RED. This isn't the model deciding to be cautious — it's a deterministic rule. The commercial desk owns all pricing questions. The system enforces that boundary without LLM judgment."*

---

## Act 3: Governance Deep-Dive (3 min)

### Moment 5 — Freshness suppression

**Action:** Open SEC-002 (SOC2 compliance). Point to the badge **"🕐 2 stale prior(s) suppressed"**. Hover it to show the tooltip.

*"Two prior RFP answers for SOC2 were approved before the SOC2 cert was last updated. The retriever compared each prior's `approved_at` timestamp against the cert's `updated_at`. The priors lost — suppressed entirely, unable to influence this answer. The model only saw the current cert. Freshness beats prior approval history."*

### Moment 6 — No primary source escalation

**Action:** Open SEC-029 (FedRAMP roadmap). Show AMBER with **"⚠ No primary source corroboration"**.

*"There is no authoritative primary source for FedRAMP status in the corpus — we don't have that cert yet. Without a corroborating primary source, the answer is capped at amber and human review is mandatory. The system doesn't let the model assert compliance it can't prove."*

### Moment 7 — Customer reference gating

**Action:** Open SEC-004. Show AMBER with trigger tag `unapproved_reference`.

*"The model found a customer name — probably from training data. Hard rule 4 checks every name against a DynamoDB approval table. That customer is either under NDA, has an expired approval, or isn't on the list. Name redacted, tier forced to amber. The model can't leak NDA relationships."*

### Moment 8 — SME edit and approve

**Action:** On SEC-002 (SOC2), click into the answer textarea. Add "as of Q1 2026" to the sentence. Click **"Approve & Resume →"**.

Watch the banner: "✓ Approved — Step Functions execution resumed."

*"The SME edited one answer and approved the batch. The review API called `SendTaskSuccess` with the task token that's been sitting in DynamoDB since the pipeline paused. Step Functions resumed within seconds."*

---

## Act 4: Flywheel & Self-Cleaning (2 min)

### Moment 9 — Flywheel write verification

After approving, switch to the terminal. Run the LibraryFeedback scan from `docs/test-guide.md §6b`. Show the output table.

*"The flywheel captured the approved answer, the topic IDs, and the specific S3 URIs of the primary sources that corroborated it at approval time. When that SOC2 cert is renewed next quarter, the staleness daemon knows exactly which entries to flag — not by content similarity, but by source identity."*

### Moment 10 — Staleness sweep

**Action:** In the review UI header, click **"⟳ Staleness"**.

Show the inline result: `"Staleness sweep: checked N, flagged X, cleared Y"`

Run the stale-flag scan from `docs/test-guide.md §7`. Show the count.

*"The daemon re-checked every LibraryFeedback entry's corroborating sources against their current `updated_at`. Three entries were flagged stale — their sources were updated after approval. Those answers now fall out of the H signal until re-approved. The library self-cleans without manual curation."*

### Moment 11 — Circuit breaker

Run the mock-source loop from `docs/test-guide.md §5e`. Point to the ~1-in-20 ERROR response.

*"External sources like Seismic have a deliberate 5% error rate in this demo. The circuit breaker logs `source_degraded` and the answer proceeds without that source rather than failing the entire question. The degradation is observable in X-Ray and CloudWatch — not silent."*

### Moment 12 — Download output workbook

**Action:** Go back to `upload.html`. The Recent Jobs row shows **"Download ↓"**. Click it. The answered Excel opens.

Show: answer column filled, green/amber/red cell fills, Summary sheet.

*"The rep gets back the same Excel they uploaded. Green answers go straight to the doc. Amber and red are already reviewed and edited. No new tool to learn. 70% first-draft, 90% less effort, every answer traceable to its source."*

---

## Teardown / Reset

```bash
bash scripts/reset-demo.sh   # clean state for next run (~45 sec)
bash scripts/teardown.sh     # destroy all AWS resources
```

---

## Appendix: Talking Points for Q&A

**"Why not use Agents / AgentCore?"**
Deterministic orchestration for legal-adjacent workflows. Agent reasoning is non-deterministic and can't guarantee hard-rule enforcement — a pricing question could slip through. With Step Functions Standard every execution is auditable, every rule fires deterministically, and `waitForTaskToken` gives unlimited human-in-the-loop without polling. Agents are right for interactive, exploratory workflows. This is a compliance-governed batch pipeline.

**"How does this scale beyond 30 questions?"**
Three additive levers, none requiring architectural changes: (1) Distributed Map + S3 JsonItemReader for 500-question RFPs — CDK config only; (2) S3 keyword scan → OpenSearch or Kendra when source count exceeds ~10; (3) DynamoDB topic GSI replaces the scan when the library exceeds ~10K entries. All documented in `docs/architecture.md §11`.

**"What about Glean integration?"**
Glean's enterprise search API slots into the retriever's source authority hierarchy as Secondary or Tertiary, depending on the topic. The integration point is the `DISPATCH_TABLE` in `classifier/dispatch.py` — a two-line addition — plus a passage-fetching function in `retriever/handler.py`. The architecture was designed for this.

**"What's the cost?"**
~$3 per demo cycle on synthetic data. Production at 100 RFPs/month: $150–250/month on-demand (Bedrock tokens dominate). Adding Kendra adds ~$700/month fixed. The demo tier shows you can run the full workflow without Kendra or Neptune — production upgrades are documented additive steps, not rebuilds.

**"Why Step Functions over EventBridge Pipes?"**
Three reasons: (1) `waitForTaskToken` handles unlimited-duration human review without polling or timeouts; (2) full execution history — every input/output at every state in CloudWatch for audit; (3) the visual state machine graph in the console is a demo asset — you can show it running live during the interview.
