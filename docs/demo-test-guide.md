# RFP Copilot — Demo Test Guide

Live environment (us-east-1, account 658203403363):

| Resource | Value |
|---|---|
| Review UI | http://localhost:8080/review.html (run `python3 -m http.server 8080` in `ui/`) |
| Review API | https://rusa84gwa4.execute-api.us-east-1.amazonaws.com |
| Mock Sources API | https://xt2cs4vj4c.execute-api.us-east-1.amazonaws.com |
| State Machine | `RfpStateMachine46EAF9AE-S8D2Gt7befCZ` |
| Incoming bucket | `rfp-copilot-dev-storage-incomingbucket8258de13-7ooyiodjngme` |

---

## 1. Pre-flight

```bash
# Authenticate (new shell session)
aws login
eval "$(aws configure export-credentials --format env)"

# Confirm account
aws sts get-caller-identity --query "[Account,Arn]" --output text

# Start review UI server (if not already running)
cd rfp-copilot/ui && python3 -m http.server 8080
```

---

## 2. Full pipeline run

```bash
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:us-east-1:658203403363:stateMachine:RfpStateMachine46EAF9AE-S8D2Gt7befCZ" \
  --input '{
    "bucket": "rfp-copilot-dev-storage-incomingbucket8258de13-7ooyiodjngme",
    "key": "incoming/sample_rfp_acmesec.xlsx",
    "jobId": "demo-001"
  }'
```

Watch status (reaches WaitForReview in ~30 seconds):

```bash
EXEC_ARN="<executionArn from above>"
watch -n 5 "aws stepfunctions describe-execution --execution-arn $EXEC_ARN --query '[status,startDate,stopDate]' --output text"
```

**Expected:** Status transitions `RUNNING → RUNNING (WaitForReview)`. The execution parks at the review gate and waits indefinitely for SME input.

---

## 3. Review UI walkthrough

1. Open http://localhost:8080/review.html
2. Paste `https://rusa84gwa4.execute-api.us-east-1.amazonaws.com` → **Load**
3. Confirm `demo-001` appears with 30 answers
4. Click **Review →**

### What to verify per question tier

| Question | Expected tier | Why |
|---|---|---|
| SEC-028 (per-user pricing) | 🔴 RED | Hard rule: pricing forced RED, commercial desk owns |
| SEC-029 (FedRAMP roadmap) | 🟡 AMBER | Forward-looking commitment + compliance claim |
| SEC-002 (SOC 2 scope) | 🟡 AMBER | Compliance claim requires minimum AMBER |
| SEC-004 (reference customer) | 🟡 AMBER | Aurora Federal is NDA — not on approved list |
| SEC-006 (encryption at rest) | 🟢 GREEN | Strong primary sources, no hard-rule triggers |
| SEC-011 (SSO protocols) | 🟢 GREEN | Product docs corroborate fully |

---

## 4. Key demo moments

### 4a. Hard rules — pricing forced RED

In the review UI, open SEC-028. Confirm:
- Tier badge: **RED**
- Trigger tag: `pricing_detected` (or `dispatch_force_tier`)
- Answer text is a deflection ("pricing not available") — Guardrails blocked the actual price

### 4b. Freshness suppression visible in logs

```bash
# Find the retriever log group
LOG_GROUP=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/rfp-copilot-dev-orchestration-RetrieverFn" \
  --query "logGroups[0].logGroupName" --output text)

# Search for suppressed priors
aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --filter-pattern "suppressed_priors" \
  --query "events[-5:].message" --output text
```

**Expected:** JSON log lines showing `suppressed_priors: ["acme_financial_soc2_answer", ...]` for SOC2 and encryption questions — priors approved before their primary source last updated.

### 4c. Source degraded — circuit breaker

```bash
# Confirm mock sources are live
curl -H "Authorization: Bearer demo" \
  "https://xt2cs4vj4c.execute-api.us-east-1.amazonaws.com/seismic/content?query=encryption"
```

**Expected:** JSON response with Seismic cards. The 5% error rate means ~1 in 20 calls returns 503 — re-run a few times to see it fire. The retriever logs `source_degraded` on each 503 and surfaces it in `corroboration_metadata` in the Step Functions execution history.

To force it: temporarily set `MOCK_SOURCES_API_URL` to a dead URL in the Lambda env, re-run the pipeline, and check for `source_degraded` in the CloudWatch logs.

### 4d. Flywheel write — approve and inspect LibraryFeedback

In the review UI:
1. Edit one AMBER answer (e.g. SEC-002 SOC2) — change wording slightly
2. Click **Approve & Resume →**
3. Confirm the Step Functions execution completes (status → SUCCEEDED)

Verify the flywheel write:

```bash
eval "$(aws configure export-credentials --format env)"

LF_TABLE="rfp-copilot-dev-data-LibraryFeedbackTableB9B77862-1S857GFB1RZVB"

aws dynamodb scan \
  --table-name "$LF_TABLE" \
  --query "Items[*].{answerId:answerId.S,topic:topic_ids.L[0].S,approved_at:approved_at.S,corroborated_by:corroborated_by.L[0].S}" \
  --output table
```

**Expected:** Rows for each amber/red question showing `sme_approved=true`, `approved_at` timestamp, `topic_ids`, and `corroborated_by` S3 URIs of the primary sources.

### 4e. Staleness daemon — show it fire live

In the review UI (after approving at least one answer):
1. Click **⟳ Staleness** in the top bar
2. **Expected response:** `Staleness sweep: checked N, flagged X, cleared Y`

The three stale priors in the corpus (`acme_financial_soc2_answer`, `bluebird_encryption_answer`, `globex_fedramp_answer`) were approved before their corroborating primary source was last updated. Any LibraryFeedback entries written for those topics will be flagged `corroboration_stale=true`.

To verify the flag was written:

```bash
aws dynamodb scan \
  --table-name "$LF_TABLE" \
  --filter-expression "corroboration_stale = :t" \
  --expression-attribute-values '{":t":{"BOOL":true}}' \
  --query "Items[*].{answerId:answerId.S,approved_at:approved_at.S}" \
  --output table
```

---

## 5. Download the output workbook

The writer Lambda placed the answered Excel in the output bucket:

```bash
OUTPUT_BUCKET="rfp-copilot-dev-storage-outputbucket7114eb27-28f8zfqd3fvg"
aws s3 cp "s3://$OUTPUT_BUCKET/answered/demo-001/sample_rfp_acmesec_answered.xlsx" ~/Desktop/
open ~/Desktop/sample_rfp_acmesec_answered.xlsx
```

**Expected:** Original questions intact, Answer column filled, Confidence column showing GREEN/AMBER/RED cell fills.

---

## 6. Reset between demo runs

```bash
cd rfp-copilot
bash scripts/reset-demo.sh
# Then start a new run with jobId: demo-002, demo-003, etc.
```

Reset takes ~45 seconds. It clears all DynamoDB tables, re-seeds corpus and customer refs, and re-uploads the sample RFP.

---

## 7. Teardown

```bash
bash scripts/teardown.sh
# Type 'destroy' when prompted
```

Note: IncomingBucket and AuditBucket have Object Lock enabled — if CloudFormation hangs deleting them, empty them manually in the S3 console first.
