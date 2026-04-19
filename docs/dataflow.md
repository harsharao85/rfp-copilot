# Data Flow — what happens after an RFP lands in the Incoming bucket

**Companion to:** `docs/architecture.md`, `docs/scoringmechanism.md`
**Last updated:** 2026-04-17

This doc traces the full path from the moment an RFP workbook lands in the S3 Incoming bucket through to a downloadable colored output. Every Lambda invocation, every DynamoDB write, every Step Functions transition.

**One thing to flag up front:** the S3 upload itself does **not** auto-trigger the pipeline. There's no EventBridge rule watching the Incoming bucket. The pipeline starts when the browser makes an explicit `POST /upload/{jobId}/start` call *after* the PUT completes. This is a demo-tier choice — it keeps the trigger path explicit and debuggable.

---

## Step 0 — The context at the moment of upload

The browser has already done two things:
1. Called `POST /upload/presign` → got back a presigned S3 URL + a fresh `jobId` (UUID)
2. Called `PUT` on that presigned URL → the workbook bytes are now in `s3://incoming-bucket/uploads/{jobId}/source.xlsx`

At this exact moment:
- The file is in S3, encrypted at rest with the Incoming bucket's KMS CMK
- A Jobs table row already exists with `{jobId, status: "UPLOADED", filename, uploadedAt}` (written during presign)
- **Nothing else has happened.** No pipeline running. No Lambdas invoked beyond `upload_api`.

---

## Step 1 — Pipeline trigger (`/upload/{jobId}/start`)

Browser calls `POST /upload/{jobId}/start`. That hits API Gateway → `upload_api` Lambda.

`upload_api` does three things:
1. Reads the Jobs row to confirm the upload exists.
2. Updates the Jobs row: `status: "PROCESSING"`, `executionStartedAt`.
3. Calls `states:StartExecution` on the Step Functions state machine with input:
   ```json
   { "jobId": "job-b6e64744",
     "bucket": "incoming-bucket",
     "key": "uploads/job-b6e64744/source.xlsx" }
   ```

API Gateway returns `200 OK` to the browser immediately. From this point, Step Functions owns the workflow.

---

## Step 2 — ParseWorkbook

First state in the state machine. Invokes the `excel_parser` Lambda.

What it does:
- Downloads `source.xlsx` from S3 into `/tmp` (Lambda has 512 MB scratch).
- Uses `openpyxl` to iterate the sheet, skipping metadata rows, detecting the `ID, Question, Answer, Confidence` header row.
- Extracts a list of `(questionId, text, section)` tuples — filtering out blank rows and section headers.
- Writes one row per question into the **Questions** table: `{jobId, questionId, text, section, status: "pending"}`.
- Updates the Jobs row with `questionCount` and `status: "PROCESSING"`.
- Returns an array like:
  ```json
  [ {"jobId": "job-b6e64744", "questionId": "SEC-001", "text": "..."},
    {"jobId": "job-b6e64744", "questionId": "SEC-002", "text": "..."},
    ... ]
  ```

**Why this is a separate Lambda and not merged into the Map state:** parsing is sequential and fast (~2 seconds for 30 questions). Map state needs its input *already flattened* to fan out. Parsing is also the one stage that can fail in "obvious" ways — malformed workbook, wrong sheet structure, empty — and we want that failure isolated with its own retry policy.

---

## Step 3 — ProcessQuestions (Map state, concurrency 10)

Step Functions' `Map` state takes the array returned by the parser and fans it out: up to 10 questions processing concurrently, each running the same 5-stage inner pipeline.

For each question, the inner pipeline runs sequentially:

### 3a. Classify (`question_classifier` Lambda)

- Calls Bedrock with **Claude Haiku 4.5** (the cheap/fast classifier model).
- Prompt asks it to tag the question with topics from a fixed taxonomy: `soc2`, `iso27001`, `encryption_at_rest`, `sso`, `pricing`, `customer_reference`, `forward_looking`, etc.
- Looks up each topic in the local `DISPATCH_TABLE` (hardcoded Python dict in `dispatch.py`).
- Merges per-topic dispatch plans into one plan for this question: which sources are Primary, Secondary, Tertiary; whether `corroboration_required: true`; whether `force_tier: RED`.
- Writes classification + dispatch_plan to the Questions row.
- Returns the enriched question object.

### 3b. Retrieve (`retriever` Lambda)

- Reads the dispatch plan.
- For each Primary source in the plan, queries it:
  - `compliance_store` → S3 keyword match against `s3://reference-corpus/compliance/*.json`
  - `product_docs` → S3 keyword match against `s3://reference-corpus/product-docs/*.json`
  - `customer_refs_db` → DynamoDB filter scan on CustomerRefs
  - `mock_sources` (Seismic/Gong) → HTTP GET to the mock Lambda
- Scans **LibraryFeedback** for SME-approved priors matching this question's topics.
- Applies **Rule 1 (freshness suppression)**: for each prior, compares its `approved_at` to the `updated_at` of all Primary documents retrieved for the same topics. If any Primary is newer, the prior is suppressed — logged with `suppressed_priors: [...]`.
- Packages everything into a `RetrievalContext` object: `{passages: [...], prior_matches: [...], corroboration_metadata: {...}, reference_customers_matched: [...]}`.
- Writes retrieval metadata (passage IDs cited, suppressed_prior_count) to the Questions row.

### 3c. Generate (`generator` Lambda)

- Builds a prompt: system prompt (brand voice + hard-rule summary + answer pattern library, ~3K tokens, cached via `cache_control: ephemeral`) + user prompt (retrieved passages + the question + the approved-customers list).
- Calls Bedrock `invoke_model` with **Claude Sonnet 4.5**, temperature 0.2, max tokens 768.
- Parses the JSON response: `{answer_text, citations, invoked_customers, contains_pricing, contains_compliance_claim, contains_forward_looking}`.
- Calls `apply_guardrail(source="OUTPUT")` on the generated `answer_text`. If Bedrock Guardrails intervenes (detects blocked topic), the answer is replaced with the guardrail's blocked message.
- Returns `GeneratedAnswer` with hashes (`prompt_hash`, `response_hash`) for the audit trail.

### 3d. Score (`confidence_scorer` Lambda)

- Takes the retrieval passages, prior matches, and generated answer.
- Computes each signal:
  - **H**: similarity between the question and the best prior match → scaled 0..1
  - **R**: mean of the top-K retrieval relevance scores
  - **C**: number of distinct sources cited / expected
  - **F**: freshness decay based on `updated_at`/`approved_at` vs today
  - **G**: 1.0 if no guardrail intervention, 0 otherwise
- Weighted sum: `composite = 0.45*H + 0.25*R + 0.15*C + 0.10*F + 0.05*G`.
- If H == 0 and composite > 0.79, caps at 0.79 (amber).
- Maps composite to tier: ≥0.80 green, 0.55–0.80 amber, <0.55 red.
- Writes breakdown to Questions row.

### 3e. ApplyHardRules (`hard_rules` Lambda)

- Runs regex + lookup checks against the generated answer:
  - Pricing language → override to RED, trigger `pricing_language`
  - Compliance claim without citation → min AMBER, trigger `compliance_claim_uncited`
  - Named customer not in CustomerRefs with clean approval → min AMBER, trigger `unapproved_reference:name`
  - Forward-looking ("will deliver", "committed to") → min AMBER, trigger `forward_looking`
  - Competitor disparagement phrasing → override to RED, trigger `competitor_disparagement`
- If dispatch plan has `force_tier: RED`, overrides to RED regardless.
- Writes final tier + triggers list to Questions row.
- Returns the complete per-question result.

When every question has exited the Map state, Step Functions collects the array of results and passes it to the next state.

---

## Step 4 — Decision: all green, or any amber/red?

A Step Functions `Choice` state inspects the aggregated results:
- `all(q.tier == "green")` → jump to **WriteOutputWorkbook**
- `any(q.tier in ("amber", "red"))` → go to **WaitForReview**

This is the branch point that determines whether the job needs a human.

---

## Step 5a — WaitForReview (the human-in-the-loop path)

`review_gate` Lambda is invoked via Step Functions' **`.waitForTaskToken`** integration pattern.

What this means concretely:
- Step Functions passes a special `TaskToken` in the Lambda invocation event.
- `review_gate` *stores the token* in the Jobs row (`taskToken` field), updates Jobs `status: "WAITING_FOR_REVIEW"`, and returns.
- **Step Functions does NOT proceed.** The execution is now paused indefinitely — it will resume only when something external calls `SendTaskSuccess` or `SendTaskFailure` with that token.

The browser (which has been polling `/upload/{jobId}/status`) now sees `status: "WAITING_FOR_REVIEW"` and shows the "Open Review UI →" link.

### Human review

SME opens the review UI, which lists amber/red questions with their confidence breakdowns, citations, hard-rule triggers, and editable answer textareas. For each question the SME can edit the answer inline.

Two outcomes:
- **Approve** → `review_api` calls `SendTaskSuccess` with the edited answers → Step Functions wakes up → proceeds to WriteOutputWorkbook. Also writes each approved Q&A into LibraryFeedback with `corroborated_by` provenance + `approved_at` timestamp.
- **Reject** → `review_api` calls `SendTaskFailure` with the rejection reason → Step Functions execution ends in FAILED state → Jobs row status becomes FAILED, `review_status: "REJECTED"`. No output file is written.

---

## Step 5b — The all-green fast path

If every question came back green, the Choice state skips the review gate entirely and goes straight to WriteOutputWorkbook. No SME sees it. This is the 70%-first-draft promise in action — for well-covered topics with strong priors, the pipeline ships autonomously.

---

## Step 6 — WriteOutputWorkbook

`excel_writer` Lambda:
- Downloads the original `source.xlsx` from Incoming.
- Loads it with `openpyxl`.
- For each question row:
  - Writes the final `answer_text` into column C.
  - Colors the row's background by tier: green (#D4EDDA), amber (#FFF3CD), red (#F8D7DA).
  - Adds a comment with the confidence breakdown and cited document IDs.
- Saves the result to `s3://output-bucket/outputs/{jobId}/answered.xlsx`.
- Updates the Jobs row: `status: "SUCCEEDED"`, `outputKey`, `completedAt`.

Step Functions execution ends in SUCCEEDED state.

---

## Step 7 — The browser finds out

Meanwhile the browser has been polling `GET /upload/{jobId}/status` every 5 seconds the whole time. It now sees `status: "SUCCEEDED"` and an `outputKey` field. It calls `GET /upload/{jobId}/download` → `upload_api` generates a presigned GET URL on the Output bucket → browser opens that URL → file downloads.

If the browser is sitting on the Review UI (because the job went through the human-review path), after the SME clicks Approve the review page polls the upload API the same way and surfaces the download button inline.

---

## Summary timing (30-question RFP, well-covered topics)

| Stage | Typical duration |
|---|---|
| Parse workbook | ~2 sec |
| Map state (30 questions, concurrency 10) | ~90 sec (3 batches × ~30 sec each) |
| Decision (all-green fast path) | <1 sec |
| Write output workbook | ~3 sec |
| **Total (no human review)** | **~100 sec** |
| Wait for SME review | Minutes to hours |
| **Total (with human review)** | Dominated by the human |

---

## Where state lives at each moment

| Pipeline stage | What's written where |
|---|---|
| After `/upload/presign` | Jobs row created |
| After S3 PUT completes | Nothing new written |
| After `/upload/start` | Jobs status = PROCESSING |
| During ParseWorkbook | Questions rows created |
| During Map state | Questions rows enriched with classification, retrieval meta, generation hashes, scoring breakdown, hard-rule triggers |
| If review needed | Jobs row stores taskToken, status = WAITING_FOR_REVIEW |
| On SME approval | Reviews row appended, LibraryFeedback rows appended |
| After WriteOutputWorkbook | Jobs row has outputKey, status = SUCCEEDED |

---

## Why the audit trail matters

Every state transition is visible in Step Functions execution history (retained for 90 days on Standard workflows). If something breaks, you can replay the exact inputs and outputs of every Lambda that ran. Every Bedrock call records `prompt_hash` and `response_hash` for reproducibility. Every reviewer action lands in the Reviews table.

This is the audit trail that justifies using Step Functions Standard (instead of Express) in the first place. Express is cheaper but caps at 5-minute executions and doesn't retain history beyond CloudWatch metrics — neither is acceptable for a workflow that legal/compliance might need to reconstruct months later.

That's the whole flow, from S3 upload to downloadable output.
