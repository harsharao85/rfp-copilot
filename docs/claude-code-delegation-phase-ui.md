# Claude Code Delegation — Phase UI + Demo Script

**Context:** RFP Copilot v0.5 is deployed and the pipeline runs end-to-end (parse → classify → retrieve → generate → score → rules → write → review gate). The review UI (`ui/review.html`) exists but is minimal. There is NO upload UI. The demo-test-guide.md covers CLI-only flows. This delegation adds the browser-based upload path, enhances the review UI with citation provenance, and produces a revised demo script that covers every demo moment.

**Karpathy rules apply.** Think before coding. Simplicity first. Surgical changes. Goal-driven execution. Remove orphans YOUR changes create.

---

## Task 1: Upload API Lambda + CDK Wiring

### What to build

A new Lambda (`lambdas/upload_api/handler.py`) behind an HTTP API Gateway that provides:

```
POST /upload/presign   → returns { uploadUrl, bucket, key, jobId }
GET  /upload/{jobId}/status  → returns { jobId, status, outputKey? }
GET  /upload/{jobId}/download → returns { downloadUrl } (pre-signed GET for output Excel)
```

### Behavior

1. **`POST /upload/presign`** — Generates a unique `jobId` (e.g., `job-{uuid4[:8]}`), creates a pre-signed PUT URL for `s3://{INCOMING_BUCKET}/incoming/{jobId}/{original_filename}`, writes an initial row to JobsTable `{jobId, status: "UPLOADING", created_at}`, returns `{uploadUrl, bucket, key, jobId}`. Pre-signed URL expires in 5 minutes.

2. **After upload completes** — The UI calls a second endpoint or the upload API itself kicks off the Step Functions execution. Simplest approach: add `POST /upload/{jobId}/start` that calls `sfn.start_execution()` with the correct input shape `{bucket, key, jobId}` and updates JobsTable status to `PROCESSING`.

3. **`GET /upload/{jobId}/status`** — Reads JobsTable. Returns `{jobId, status, outputKey, review_status}`. Status values: `UPLOADING → PROCESSING → WAITING_FOR_REVIEW → SUCCEEDED | FAILED`.

4. **`GET /upload/{jobId}/download`** — Generates a pre-signed GET URL for the output Excel at `s3://{OUTPUT_BUCKET}/answered/{jobId}/...`. Returns `{downloadUrl}`. Expires in 15 minutes.

### CDK changes (orchestration-stack.ts)

- New `PythonFunction` for upload_api using the same factory pattern as review_api
- New HTTP API Gateway (or add routes to the existing review API gateway — architect's choice, but separate gateway is cleaner for CORS isolation)
- Grant the Lambda: `s3:PutObject` on incoming bucket, `s3:GetObject` on output bucket, `dynamodb:PutItem + UpdateItem + GetItem` on JobsTable, `states:StartExecution` on the state machine
- Expose the upload API URL as a CfnOutput
- CORS: `Access-Control-Allow-Origin: *` (demo-tier; not production)

### IAM — least privilege

The upload Lambda should NOT have access to QuestionsTable, LibraryFeedbackTable, CustomerRefsTable, or any Bedrock permissions. It only touches: incoming bucket (write), output bucket (read), jobs table (read/write), state machine (start).

---

## Task 2: Upload UI (`ui/upload.html`)

### What to build

A single-page HTML file (same style as review.html — dark header bar, clean cards, no framework) that provides:

1. **API URL config bar** — Same pattern as review.html. Text input for upload API URL, "Save" button, persists to localStorage.

2. **Upload card** — Drag-and-drop zone (or file picker) accepting `.xlsx` files only. On drop/select:
   - Call `POST /upload/presign` with `{filename}` in body
   - Upload the file directly to the pre-signed S3 URL via `fetch(uploadUrl, {method: 'PUT', body: file})`
   - Call `POST /upload/{jobId}/start` to kick off the pipeline
   - Show a progress indicator

3. **Status polling** — After upload starts, poll `GET /upload/{jobId}/status` every 5 seconds. Display:
   - Current status with colored badge (PROCESSING=blue, WAITING_FOR_REVIEW=amber, SUCCEEDED=green, FAILED=red)
   - When status reaches `WAITING_FOR_REVIEW`: show a link "Open Review UI →" pointing to `review.html?api={reviewApiUrl}&job={jobId}`
   - When status reaches `SUCCEEDED`: show a "Download Answered RFP ↓" button that calls `/upload/{jobId}/download` and opens the pre-signed URL

4. **Recent jobs list** — Below the upload card, show a table of recent jobs (from localStorage or from a `/upload/jobs` list endpoint). Each row shows jobId, status, timestamp, and action buttons (Review / Download / Re-run).

### Style

Match `review.html` exactly: same fonts, colors, header bar with "RFP Copilot" title and a `<span class="badge">Upload</span>` pill. Same `.card`, `.btn`, `.status-msg` classes. Copy the CSS from review.html — do NOT create a separate stylesheet.

### Navigation

Add a nav link in the review.html header: `<a href="upload.html">Upload</a>` and vice versa in upload.html: `<a href="review.html">Review</a>`. Simple anchor tags, no router.

---

## Task 3: Enhance Review UI with Citations + Confidence Breakdown

### What to add to `ui/review.html`

The current review UI shows: question text, tier pill, raw confidence %, answer textarea, hard_rule_triggers. It's missing the provenance data that makes this project's governance story compelling. Add:

#### 3a. Source Citations

Below each answer textarea, add a collapsible "Sources" section showing the citations array from the question data. Each citation should display:
- Source system badge (e.g., `compliance`, `whitepaper`, `product-docs`, `prior-rfp`, `seismic`, `gong`) — color-coded by authority level:
  - Primary (compliance, whitepaper): solid blue badge
  - Secondary (prior-rfp): amber badge
  - Tertiary (seismic, gong, product-docs): gray badge
- Source title/URI (truncated, with tooltip for full path)
- Relevance snippet (first 150 chars of the passage text)

#### 3b. Confidence Breakdown

Replace the single `72%` confidence display with a mini breakdown bar showing the 5 components:
- H (hallucination check): 45% weight
- R (retrieval relevance): 25% weight
- C (citation coverage): 15% weight
- F (format compliance): 10% weight
- G (guardrail pass): 5% weight

Display as a horizontal stacked bar or as 5 small labeled values. The raw values come from `confidence_breakdown` on the question item.

#### 3c. Suppressed Priors Indicator

If a question had priors suppressed by freshness rule, show a small info badge: "🕐 N stale prior(s) suppressed" with a tooltip explaining: "Prior RFP answers were suppressed because the authoritative source was updated after they were approved."

This data needs to be surfaced from the retriever. The retriever already logs `suppressed_priors` — extend it to persist `suppressed_prior_count` on the question item in DynamoDB so the review API can return it.

#### 3d. Corroboration Status

For AMBER/RED questions, show which primary sources corroborated the answer. Display `corroborated_by` URIs (already persisted by hard_rules handler as `primary_passage_uris`). If empty, show "⚠ No primary source corroboration" in red text.

### Review API changes

Update `get_review()` in `lambdas/review_api/handler.py` to include these additional fields in the response:
```python
"citations": q.get("citations", []),
"confidence_breakdown": q.get("confidence_breakdown", {}),
"suppressed_prior_count": q.get("suppressed_prior_count", 0),
"primary_passage_uris": q.get("primary_passage_uris", []),
"topic_ids": q.get("topic_ids", []),
```

These fields are already being written to DynamoDB by the hard_rules handler and the retriever — the review API just needs to pass them through.

---

## Task 4: Retriever — Persist Suppressed Prior Count

### What to change in `lambdas/retriever/handler.py`

The retriever already computes suppressed priors and logs them. Add a DynamoDB update to persist the count on the question item:

```python
# After freshness suppression logic, before return
ddb.Table(os.environ["QUESTIONS_TABLE"]).update_item(
    Key={"jobId": job_id, "questionId": question_id},
    UpdateExpression="SET suppressed_prior_count = :spc",
    ExpressionAttributeValues={":spc": len(suppressed_priors)},
)
```

Grant the retriever Lambda `dynamodb:UpdateItem` on QuestionsTable if it doesn't already have it.

---

## Task 5: Revised Demo Script (`docs/demo-script.md`)

After Tasks 1-4 are complete and tested, write a comprehensive demo script covering every demo moment. Structure:

### Script structure

```
# RFP Copilot — Live Demo Script
## Pre-flight checklist
## Act 1: Upload & Pipeline (2 min)
## Act 2: SME Review with Citations (3 min)  
## Act 3: Governance Deep-Dive (3 min)
## Act 4: Flywheel & Self-Cleaning (2 min)
## Teardown / Reset
## Appendix: Talking Points for Q&A
```

### Demo moments to cover (ALL required)

1. **Upload flow** — Open upload.html. Drag sample RFP. Show pre-signed URL upload. Watch status transition from PROCESSING → WAITING_FOR_REVIEW. Click "Open Review UI →".

2. **Source citation provenance** — In review UI, open a GREEN question (SEC-006 encryption). Show the source citations: blue Primary badge for the encryption whitepaper, gray Tertiary for product-docs. Talk track: "Every answer traces back to its authoritative source. The model can't cite a prior RFP as factual authority."

3. **Confidence breakdown** — On the same question, show the H/R/C/F/G breakdown. Talk track: "This isn't LLM self-reported confidence. It's a deterministic composite — hallucination check is 45% of the score. If H is zero, the answer can never be green, regardless of other signals."

4. **Hard rules — pricing forced RED** — Open SEC-028. Show RED tier, `pricing_detected` trigger tag, deflected answer text. Talk track: "Guardrails blocked the actual price before it reached the answer. Pricing always goes to the commercial desk — this isn't a model decision, it's a hard rule."

5. **Freshness suppression** — Open SEC-002 (SOC2). Show "🕐 2 stale prior(s) suppressed" badge. Talk track: "Two prior RFP answers for SOC2 were approved before the SOC2 cert was last updated. The system suppressed them entirely — they can't pollute this answer with stale data."

6. **No-primary-source escalation** — Open SEC-029 (FedRAMP roadmap). Show AMBER tier with "⚠ No primary source corroboration" warning. Talk track: "There's no authoritative primary source for FedRAMP status, so the system caps this at amber and requires SME review. It won't let the model assert compliance without the cert."

7. **Customer reference gating** — Open SEC-004. Show AMBER tier. Talk track: "The model found Aurora Federal in training data, but they're under NDA and not on the approved reference list. Hard rule 4 caught this."

8. **SME edit + approve** — Edit one AMBER answer (SEC-002 SOC2). Change wording. Click "Approve & Resume →". Show execution completes.

9. **Flywheel write verification** — After approval, run the DynamoDB scan (from demo-test-guide.md §4d) to show LibraryFeedback entries with `sme_approved=true`, `corroborated_by` URIs, topic_ids. Talk track: "The flywheel captured not just the approved answer, but which primary sources corroborated it. When that SOC2 cert gets renewed, the staleness daemon will flag this entry."

10. **Staleness sweep** — Click ⟳ Staleness in the review UI. Show "checked N, flagged X, cleared Y". Run the DynamoDB scan for `corroboration_stale=true`. Talk track: "Three prior answers were flagged because their corroborating sources were updated after approval. The system won't serve stale answers — it forces re-review."

11. **Circuit breaker** — Run the curl command against mock sources API. Show the 5% error rate. Talk track: "External sources like Seismic have a 5% error rate in this demo. The circuit breaker logs `source_degraded` and the answer proceeds without that source rather than failing the entire question."

12. **Download output workbook** — Either from upload.html "Download" button or from S3 CLI. Open the Excel. Show green/amber/red cell fills, citations as comments, Summary sheet. Talk track: "The rep gets back the same Excel they uploaded, with answers filled in and color-coded. They don't need to learn a new tool."

### Talking points appendix

Include prepared answers for:
- "Why not use agents/AgentCore?" → Deterministic orchestration for legal-adjacent workflows; agent reasoning is non-deterministic and can't guarantee hard-rule enforcement
- "How does this scale?" → Plain Map → Distributed Map, S3 keyword scan → OpenSearch/Kendra, single-table DynamoDB redesign
- "What about Glean integration?" → Glean's API would be another source system in the retriever's source authority hierarchy, slotting in as Secondary or Tertiary
- "Cost?" → ~$3 per demo cycle; production at 100 RFPs/month estimated at $150-250/mo without Kendra/Neptune
- "Why Step Functions over EventBridge Pipes?" → Unlimited waitForTaskToken for human review; full execution history for audit; visual debugging in console

---

## Verification Checklist

Before marking this delegation complete, verify:

- [ ] `upload.html` loads, accepts drag-and-drop .xlsx, uploads via pre-signed URL
- [ ] Upload triggers pipeline; status polls correctly through all states
- [ ] Download button works when pipeline succeeds
- [ ] Review UI shows source citations with authority-level color coding
- [ ] Review UI shows confidence breakdown (H/R/C/F/G)
- [ ] Review UI shows suppressed prior count where applicable
- [ ] Review UI shows corroboration status (corroborated_by URIs or "no primary" warning)
- [ ] Approve flow still works (regression — don't break existing functionality)
- [ ] Staleness sweep still works
- [ ] `demo-script.md` covers all 12 demo moments listed above
- [ ] All new Lambdas follow existing patterns: shared/ import, logging_config, Pydantic models where applicable
- [ ] CDK synth passes
- [ ] All existing tests still pass
- [ ] No new dependencies that aren't already in the project

---

## Order of Operations

1. Task 4 first (retriever persist suppressed_prior_count) — small, surgical
2. Task 1 (upload API Lambda + CDK) — new code, no existing code changes
3. Task 2 (upload.html) — depends on Task 1 being deployed
4. Task 3 (enhance review.html) — depends on Task 4 being deployed
5. Deploy and test Tasks 1-4 together
6. Task 5 (demo script) — write AFTER everything works, not before
