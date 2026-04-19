# RFP Copilot — Test Guide (v0.5)

Live environment (us-east-1, account 658203403363):

| Resource | Value |
|---|---|
| Review API | `https://rusa84gwa4.execute-api.us-east-1.amazonaws.com` |
| Upload API | fetch after redeploy — see §1 |
| Mock Sources API | `https://xt2cs4vj4c.execute-api.us-east-1.amazonaws.com` |
| State Machine | `RfpStateMachine46EAF9AE-S8D2Gt7befCZ` |
| Incoming bucket | `rfp-copilot-dev-storage-incomingbucket8258de13-7ooyiodjngme` |
| Output bucket | `rfp-copilot-dev-storage-outputbucket7114eb27-28f8zfqd3fvg` |

---

## 1. Deploy and fetch URLs

The upload_api Lambda and UploadApi HttpApi are new. Deploy before testing:

```bash
cd rfp-copilot/infra
npx cdk deploy rfp-copilot-dev-orchestration --require-approval never
```

Fetch the new Upload API URL and save it:

```bash
aws cloudformation describe-stacks \
  --stack-name rfp-copilot-dev-orchestration \
  --query 'Stacks[0].Outputs[?OutputKey==`UploadApiUrl`].OutputValue' \
  --output text
```

Paste this URL into `ui/upload.html`'s Upload API field and into the resource table above.

---

## 2. Pre-flight

```bash
# Authenticate (new shell session)
aws login
eval "$(aws configure export-credentials --format env)"

# Confirm account
aws sts get-caller-identity --query "[Account,Arn]" --output text

# Start UI server
cd rfp-copilot/ui && python3 -m http.server 8080
```

Open two tabs:
- `http://localhost:8080/upload.html`
- `http://localhost:8080/review.html`

**In upload.html:** paste Upload API URL into "Upload API" field, paste Review API URL into "Review API" field, click **Save**.

**In review.html:** paste Review API URL, click **Load**. Confirm the page shows "No pending reviews" or prior jobs.

---

## 3. Upload flow (browser)

This is the primary test path. It exercises the entire upload_api Lambda end-to-end.

1. In `upload.html`, drag `data/sample_rfp.xlsx` into the drop zone (or click to browse).
2. Confirm the progress bar appears: "Requesting upload URL…"
3. Confirm S3 upload completes: "Upload complete. Starting pipeline…"
4. Confirm polling begins: status badge shows `PROCESSING` (blue).
5. Wait ~90 seconds. Status transitions to `WAITING_FOR_REVIEW` (amber).
6. Confirm the message "Pipeline paused for review. **Open Review UI →**" appears.
7. Confirm the Recent Jobs row in the table below shows the same status.
8. Confirm the review UI auto-loads the job's questions without requiring you to click Load or re-enter the API URL. The `?api=...&job=...` query params should populate both fields and trigger the fetch automatically.

**Check:** Click **Open Review UI →**. The browser should navigate to `review.html?api=...&job=...` and automatically load the job's questions without you typing anything.

### Failure modes to check

| Symptom | Likely cause |
|---|---|
| "Enter the Upload API URL above and click Save." | Upload API URL not saved — click Save first |
| Progress bar stalls at "Requesting upload URL…" | Upload API not deployed, or URL wrong |
| S3 upload fails (status 403) | `s3:PutObject` IAM grant missing — check CDK deploy |
| Status stuck at PROCESSING after 3 min | State machine error — check CloudWatch / Step Functions console |

---

## 4. Review UI — new features

After clicking Open Review UI →, you're in the review UI with the job pre-loaded. Verify each new feature:

### 4a. Source citations

1. Find SEC-006 (AES-256 encryption). If it's in the reviewable list, expand it; if it's green and not shown, pick any amber/red question that has sources.
2. Click **"▶ Sources (N)"** to expand the citations panel.
3. Verify badges are color-coded:
   - Blue (`src-primary`) for `compliance` or `whitepaper` sources
   - Amber (`src-secondary`) for `prior-rfp` sources
   - Gray (`src-tertiary`) for `seismic`, `gong`, `product-docs`
4. Confirm each citation row shows a URI (monospace, truncated) and a passage snippet (≤150 chars).
5. Click the toggle again — panel collapses, arrow flips back to ▶.

**Expected:** Citations panel opens and closes correctly. Authority color-coding matches source system.

### 4b. Confidence breakdown

On any question card, look at the confidence display next to the tier pill.

1. If `confidence_breakdown` is populated, verify 5 labeled components appear: `H₄₅%`, `R₂₅%`, `C₁₅%`, `F₁₀%`, `G₅%`, each showing a percentage value.
2. Verify the composite % shown in the header matches `raw_confidence` on the item.
3. If no breakdown data (early runs may not have it), verify it gracefully falls back to a single `N%` display.

**Expected:** Breakdown components visible where data exists, no JS errors on questions without breakdown data.

### 4c. Suppressed priors badge

Find SEC-002 (SOC2 compliance):

1. Look for a badge reading **"🕐 N stale prior(s) suppressed"** below the answer textarea.
2. Hover the badge — tooltip should read: "Prior RFP answers were suppressed because the authoritative source was updated after they were approved."
3. If the badge is absent, the suppressed_prior_count is 0 for that question. Check the retriever logs for `suppressed_priors` events (see §5b below).

**Expected:** Badge appears for SEC-002 with count ≥ 1. Tooltip renders on hover.

### 4d. Corroboration status

On any **AMBER or RED** question:

1. Look for either:
   - Green text: "✓ Corroborated by: s3://..." (one or more URIs listed inline)
   - Red text: "⚠ No primary source corroboration"
2. SEC-029 (FedRAMP roadmap) should show **⚠ No primary source corroboration** — there's no FedRAMP cert in the corpus.
3. SEC-028 (pricing) should show **⚠ No primary source corroboration** — hard rule deflected before corroboration.
4. SEC-002 (SOC2) should show corroborated_by URIs if the compliance cert was retrieved.

**Expected:** Corroboration line present on all amber/red cards. No JS errors if `primary_passage_uris` is empty.

### 4e. Hard rules — pricing forced RED

1. Find SEC-028. Confirm:
   - Tier badge: **RED**
   - Trigger tag: `pricing_detected`
   - Answer text is a deflection ("pricing not available" / "contact sales")
2. Confirm corroboration shows "⚠ No primary source corroboration" (Guardrails blocked before any source was cited).

### 4f. Customer reference gating

1. Find SEC-004. Confirm AMBER with trigger tag `unapproved_reference`.

---

## 5. CLI verification (backend correctness)

These checks confirm the Lambda and DynamoDB writes are correct, not just that the UI renders.

Run this once at the start of §5 to set the table name variables used throughout:

```bash
JOBS_TABLE=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-dev-data \
  --query 'Stacks[0].Outputs[?OutputKey==`JobsTableName`].OutputValue' \
  --output text)

QUESTIONS_TABLE=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-dev-data \
  --query 'Stacks[0].Outputs[?OutputKey==`QuestionsTableName`].OutputValue' \
  --output text)
```

### 5a. Check presign → start chain wrote to JobsTable

```bash
# Replace <jobId> with the job ID from the upload UI
aws dynamodb get-item \
  --table-name "$JOBS_TABLE" \
  --key '{"jobId":{"S":"<jobId>"}}' \
  --query 'Item.{status:status.S,key:key.S,created_at:created_at.S}' \
  --output json
```

**Expected:** `status: "WAITING_FOR_REVIEW"` (or PROCESSING if still running), `key: "incoming/<jobId>/sample_rfp.xlsx"`.

### 5b. Freshness suppression in retriever logs

```bash
LOG_GROUP=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/rfp-copilot-dev-orchestration-RetrieverFn" \
  --query "logGroups[0].logGroupName" --output text)

aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --filter-pattern "suppressed_priors" \
  --query "events[-5:].message" --output text
```

**Expected:** JSON log lines showing `suppressed_priors` arrays for SOC2 and encryption questions.

### 5c. suppressed_prior_count written to QuestionsTable

```bash
aws dynamodb get-item \
  --table-name "$QUESTIONS_TABLE" \
  --key '{"jobId":{"S":"<jobId>"},"questionId":{"S":"SEC-002"}}' \
  --query 'Item.suppressed_prior_count' \
  --output text
```

**Expected:** A number ≥ 1 for SEC-002. If `None`, the retriever ran before Task 4 was deployed — redeploy and re-run.

### 5d. primary_passage_uris written by hard_rules

```bash
aws dynamodb get-item \
  --table-name "$QUESTIONS_TABLE" \
  --key '{"jobId":{"S":"<jobId>"},"questionId":{"S":"SEC-002"}}' \
  --query 'Item.primary_passage_uris' \
  --output json
```

**Expected:** A list of one or more S3 URIs from the compliance or whitepaper corpus.

### 5e. Circuit breaker — mock source error rate

```bash
for i in {1..20}; do
  curl -s "https://xt2cs4vj4c.execute-api.us-east-1.amazonaws.com/seismic/content?query=encryption" \
    | jq -r 'if .error then "ERROR: \(.error)" else "OK: \(.results | length) results" end'
done
```

**Expected:** ~19 OK responses and ~1 ERROR (5% rate). Check CloudWatch retriever logs for `source_degraded` when the error fires.

---

## 6. Approve and flywheel

1. In the review UI, edit SEC-002's answer — add "as of Q1 2026" to the SOC2 sentence.
2. Click **Approve & Resume →**.
3. Confirm the success banner: "✓ Approved — Step Functions execution resumed."
4. Both Approve and Reject buttons become disabled.

### 6a. Verify Step Functions completed

```bash
SM_ARN="arn:aws:states:us-east-1:658203403363:stateMachine:RfpStateMachine46EAF9AE-S8D2Gt7befCZ"
aws stepfunctions list-executions \
  --state-machine-arn "$SM_ARN" \
  --status-filter SUCCEEDED \
  --query 'executions[0].{name:name,status:status,stopDate:stopDate}' \
  --output json
```

**Expected:** Most recent execution shows SUCCEEDED within ~5 seconds of approval.

### 6b. Verify LibraryFeedback flywheel write

```bash
LF_TABLE=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-dev-data \
  --query 'Stacks[0].Outputs[?OutputKey==`LibraryFeedbackTableName`].OutputValue' \
  --output text)

aws dynamodb scan \
  --table-name "$LF_TABLE" \
  --filter-expression "sme_approved = :t" \
  --expression-attribute-values '{":t":{"BOOL":true}}' \
  --query 'Items[*].{id:answerId.S,topics:topic_ids.L[0].S,at:approved_at.S,corroborated:corroborated_by.L[0].S}' \
  --output table
```

**Expected:** Rows for each amber/red question. `corroborated_by` has at least one S3 URI for questions where `primary_passage_uris` was populated.

---

## 7. Staleness sweep

In the review UI header, click **⟳ Staleness**.

**Expected response:** `"Staleness sweep: checked N, flagged X, cleared Y"` displayed inline.

Verify the flag in DynamoDB:

```bash
aws dynamodb scan \
  --table-name "$LF_TABLE" \
  --filter-expression "corroboration_stale = :t" \
  --expression-attribute-values '{":t":{"BOOL":true}}' \
  --query 'Items[*].{id:answerId.S,approved_at:approved_at.S}' \
  --output table
```

**Expected:** Entries for SOC2/encryption/FedRAMP-adjacent questions flagged stale (their corroborating primary sources have `updated_at` newer than the prior RFPs' `approved_at`).

---

## 8. Download output workbook

### From upload.html

1. Go back to `upload.html`. The Recent Jobs table row for the completed job shows **"Download ↓"** button.
2. Click it — a pre-signed GET URL is fetched and the file opens in the browser.

### Fallback CLI

```bash
OUTPUT_BUCKET="rfp-copilot-dev-storage-outputbucket7114eb27-28f8zfqd3fvg"
JOB_ID="<jobId>"

OUTPUT_KEY=$(aws dynamodb get-item \
  --table-name "$JOBS_TABLE" \
  --key "{\"jobId\":{\"S\":\"$JOB_ID\"}}" \
  --query 'Item.output_key.S' --output text)

aws s3 presign "s3://$OUTPUT_BUCKET/$OUTPUT_KEY" --expires-in 900
```

Open the URL. Verify: answer column filled, cell fills green/amber/red, Summary sheet present.

---

## 9. Reset between runs

```bash
cd rfp-copilot
bash scripts/reset-demo.sh
```

~45 seconds. Clears all DynamoDB tables and re-seeds corpus, customer refs, and sample RFP. Use a new jobId (demo-002, demo-003, etc.) for the next run.

---

## 10. Teardown

```bash
bash scripts/teardown.sh
# Type 'destroy' when prompted
```

If CloudFormation hangs deleting buckets with Object Lock, empty them manually in the S3 console first.
