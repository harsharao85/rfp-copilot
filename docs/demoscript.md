# Technical Demo Script — RFP Redlining Copilot

**Audience:** AI architects
**Length:** 15 minutes
**Format:** Screen recording + voiceover
**Tone:** Technical, confident, no filler — they can handle density

**Companion to:** `docs/architecture.md`, `docs/knowledgesources.md`, `docs/scoringmechanism.md`, `docs/dataflow.md`
**Last updated:** 2026-04-17

---

## [0:00 – 1:30] The problem nobody solves

**[VISUAL: Title card → a typical RFP Excel with 200 questions visible]**

> "Every company in this category — Loopio, Responsive, Arphie — solves the same problem the same way. Salespeople answer RFP questions, SMEs approve the answers, approved answers go into a library, next time a similar question comes up you auto-fill from the library.
>
> The failure mode is one nobody talks about in a sales demo: library decay. An answer approved in January 2023 is still being auto-filled in October 2025. The product has evolved. The SOC 2 report is a new one. The team that approved it has moved on. But the 'SME approved' checkmark next to the answer is still there, so nobody re-examines it. Companies ship wrong answers to prospects, confidently.
>
> This architecture is an attempt to fix that specific failure mode, AWS-native, with enterprise-legal constraints baked in from day one. Fifteen minutes, end-to-end.
>
> Call me if you want the longer version — today I'm going to focus on three things the architecture does that aren't obvious: source authority, composite confidence scoring, and the self-cleaning flywheel."

---

## [1:30 – 3:30] The architectural shape

**[VISUAL: Top-level architecture diagram from docs/architecture.md §3]**

> "Five CDK stacks in one AWS account, us-east-1. Storage, Data, Orchestration, Observability, Static Site. No VPC. No Kendra. No Neptune. No Cognito. No EventBridge triggers — the pipeline is started by an explicit API call, not S3 events.
>
> Those omissions are deliberate. Each is documented as a production scale-up trigger. Kendra at $810 a month makes no sense for a five-source demo. Neptune at $290 a month makes no sense when no query needs multi-hop traversal. VPC costs $100 a month in endpoints for a benefit the demo doesn't realize. Distributed Map matters at 500 questions, not 30.
>
> What we do use:
>
> - Step Functions Standard — specifically for the `waitForTaskToken` pattern and full execution history
> - Twelve Lambdas — Python 3.12, Pydantic-typed
> - Five DynamoDB tables — Jobs, Questions, Reviews, LibraryFeedback, CustomerRefs
> - Four S3 buckets — Incoming, Output, Reference Corpus, Audit
> - Bedrock — Sonnet 4.5 for generation, Haiku 4.5 for compound-question decomposition
> - Bedrock Guardrails — topic policies, applied output-only, not inline
>
> Bill at demo tier: roughly $120 a month running 24/7, near zero when idle. Bedrock is the only variable cost — about a dollar per 30-question RFP."

**[VISUAL: Zoom to per-question pipeline diagram, §4]**

> "The pipeline is deterministic by design. The LLM is called at fixed points inside a bounded envelope. Step Functions owns sequencing; Lambdas own stages. No iterative retrieval, no agentic reasoning. This is a legal-adjacent, audit-sensitive, latency-bounded workflow — I'll come back to why that choice matters."

---

## [3:30 – 6:00] The differentiator: source authority

**[VISUAL: Switch to §5.1 diagram — topic routing to Primary/Secondary/Tertiary]**

> "Here's the design decision that separates this from every commercial RFP tool. We treat prior approved answers as a phrasing reference, not a factual authority.
>
> Every topic — SOC 2, encryption-at-rest, pricing, customer-reference — has a dispatch plan. The plan names a Primary source of truth, a Secondary phrasing source, and Tertiary context sources. For SOC 2 questions, the Primary is the compliance corpus — the actual SOC 2 report PDF. Prior RFP answers are Secondary — useful for how to phrase the response, never authoritative for the facts.
>
> Four rules enforce this:"

**[VISUAL: Table of the four corroboration rules]**

> "1. **Freshness suppression.** If a prior's `approved_at` is older than any relevant Primary source's `updated_at`, the prior is suppressed entirely. It never reaches the generator.
>
> 2. **Primary required.** If the topic marks `corroboration_required: true` and no Primary source returned hits, the tier is capped at amber regardless of how confident the model sounded.
>
> 3. **Force-tier override.** Pricing topics have `force_tier: RED`. Unconditional, no composite math applies.
>
> 4. **Customer-name gating.** If the generated answer names a customer, we check CustomerRefs for an unexpired `public_reference: true` record. If not found, the tier is forced to amber minimum.
>
> Rule 1 is the flywheel mechanism. When your SOC 2 report updates, every prior approval that predates it falls out of retrieval until an SME re-approves against the new document. The library is younger than the Primary by design.
>
> Concretely:"

**[VISUAL: Small timeline diagram]**

> "A prior answer approved in October 2025 against SOC 2 v1. In April 2026 a new SOC 2 v2 is uploaded with a newer `updated_at`. Same question comes in — the retriever compares timestamps, suppresses the prior, logs `suppressed_priors: ['libfb-abc123']` in the audit trail. H signal drops to zero. Question that was green yesterday is amber today. An SME reviews, approves against the new document, new LibraryFeedback record with fresh `approved_at` and updated `corroborated_by`. Green again.
>
> The staleness daemon runs this check on a schedule too — daily — so the library self-cleans even for questions that don't get asked.
>
> No commercial tool in the category does this. That's the entire argument for building this rather than buying Loopio."

---

## [6:00 – 9:30] Live walkthrough

**[VISUAL: Navigate to rfp-copilot.meringue-app.com]**

> "Let me show it running. This is the upload UI — static HTML served from S3 through CloudFront, custom domain, ACM cert. No Cognito because it's a demo; production would add it.
>
> I'm going to upload `demo_rfp_techops.xlsx` — 12 questions biased toward topics we have strong priors on, so we should see mostly greens with a couple of ambers."

**[VISUAL: Drag the xlsx into the drop zone]**

> "The browser does three things. One, calls `/upload/presign` — gets back a SigV4 presigned URL. Two, PUTs the file directly to S3, Content-Type pinned to match the signed URL. Three, calls `/upload/{jobId}/start` which triggers Step Functions via `StartExecution`. The file never flows through Lambda — 6 MB payload limit, and we don't want it anyway."

**[VISUAL: Switch to Step Functions console showing the execution graph live]**

> "Here's the state machine running. ParseWorkbook first — reads the xlsx with openpyxl, writes one Questions row per question. Then the Map state — concurrency 10, fans out the 12 questions three or four at a time.
>
> Each question goes through five stages in sequence:
>
> - **Classify** — not an LLM call. Regex match against 19 topic patterns, plus a dispatch-plan lookup. Haiku runs only if the question is compound and needs splitting.
>
> - **Retrieve** — reads the dispatch plan and queries sources concurrently. Primary sources first, secondary, tertiary. Applies freshness suppression. Circuit breaker per source.
>
> - **Generate** — Sonnet 4.5 via Bedrock. 3K-token cached system prefix, per-question user prompt with retrieved passages. temperature 0.2, max_tokens 768. Forces JSON output.
>
> - **Score** — computes the composite: `0.45·H + 0.25·R + 0.15·C + 0.10·F + 0.05·G`. Pure math, no LLM.
>
> - **Hard rules** — regex checks for pricing language, compliance claims without citations, unapproved customer names, forward-looking language, competitor disparagement."

**[VISUAL: Zoom to the review-gate node, show taskToken]**

> "Here's the clever bit. The ReviewGate state uses `.waitForTaskToken`. The Lambda stashes the token in the Jobs row and returns. Step Functions *pauses the execution* until `SendTaskSuccess` arrives. Could be seconds, could be days. We pay nothing while it's waiting."

**[VISUAL: Switch to review UI at job URL]**

> "Two questions came back amber. One because the SOC 2 question mentioned a cert number we don't have in the compliance corpus — Rule 2 capped it at amber. The other because the answer mentioned Kestrel Logistics, whose reference approval expired last month — Rule 4.
>
> Both are recoverable. The SME edits the answers, clicks Approve, `review_api` calls `SendTaskSuccess`, the execution wakes up, excel_writer produces the colored output, upload UI polls the status endpoint, download link appears."

**[VISUAL: Click Approve, show polling, show Download link, open the resulting xlsx]**

> "Color-coded Excel: greens for the confidently-answered questions, ambers for the ones the SME just reviewed, comments in each cell with the confidence breakdown and cited document IDs."

---

## [9:30 – 12:00] Scoring and hard rules

**[VISUAL: Switch to a review UI screenshot showing the confidence breakdown badges]**

> "Let me unpack the scoring, because it's where the architecture pushes back against what most LLM systems do wrong.
>
> The composite is five signals:
>
> - **H — prior answer similarity, 0.45 weight.** Sourced from LibraryFeedback. Are there SME-approved priors that match this question?
> - **R — retrieval relevance, 0.25.** Strength of keyword or semantic match from the Primary sources.
> - **C — citation coverage, 0.15.** Count of distinct sources cited relative to expected.
> - **F — freshness decay, 0.10.** How recently were the retrieved docs updated?
> - **G — guardrail pass, 0.05.** Binary — did Bedrock Guardrails block the output?
>
> Thresholds: 0.80 and above is green, 0.55 to 0.80 amber, below 0.55 red.
>
> Three things about this that matter architecturally."

**[VISUAL: The math — "0.25 + 0.15 + 0.10 + 0.05 = 0.55"]**

> "One — the non-H signals cap at 0.55, right on the amber-red boundary. A fresh deployment with an empty LibraryFeedback table literally cannot produce a green answer. 'Green' here *means* 'a human expert already approved something substantially similar.' This is intentional, and it's the single most misunderstood behavior of the system. First-time demos with un-seeded data see all-red and panic.
>
> Two — we never ask the LLM how confident it is. Modern LLMs are overconfident on wrong answers. Composite over measurable signals is tunable against labeled data and explainable to a sales leader. 'Cosine similarity to approved prior was 0.82, three Primary sources corroborated' is a story. 'The model said 95% confidence' is not.
>
> Three — guardrails apply to output, not input."

**[VISUAL: Show the apply_guardrail call snippet]**

> "Bedrock Guardrails' inline `guardrailIdentifier` on `invoke_model` applies to both directions. RFP questions legitimately contain pricing and compliance language — that's the whole point of the workflow. If you wire the guardrail inline, every pricing question blocks at input with 'content that cannot be processed.' We use a separate `apply_guardrail` call with `source=OUTPUT` after generation. Topic policies gate what we say, not what we hear.
>
> The hard rules layer is enterprise-legal, not AI-safety. Pricing goes red because unsigned RFPs quoting prices can legally be construed as offers. Customer naming goes amber because logo rights expire. Forward-looking language goes amber because Product hasn't ratified the roadmap. General Counsel reviews this config; the test suite in `test_hard_rules.py` locks the behavior."

---

## [12:00 – 13:30] Trade-offs and scale-up

**[VISUAL: §12 trade-offs table scrolling]**

> "A few trade-offs worth flagging explicitly for this audience, because you'll ask.
>
> - **We use keyword retrieval over S3 JSON, not vector search.** At 10 documents it's fine. At 1000 you replace it with Bedrock Knowledge Bases — one Lambda swap, no pipeline change.
>
> - **Topic classification is regex, not embeddings.** Deterministic, auditable, free. For a 19-topic taxonomy with industry-standard vocabulary this is correct. Production adds an embedding fallback for unmatched questions, not a replacement for regex.
>
> - **Dispatch table is Python.** Production path is Postgres with two tables — one for topics including pattern and source arrays, one for audit. Classifier query becomes `WHERE text ~* ANY(patterns)`. Updatable by non-engineers, version-stamped, fully replayable.
>
> - **No VPC, no PrivateLink in the demo.** Production adds them for compliance posture. The architecture is designed so this is additive, not a refactor.
>
> Scale-up triggers worth naming:
>
> - **10+ source systems** → replace mock APIs with Amazon Kendra or Q Business
> - **Multi-hop provenance queries** ('SMEs two hops from topic X') → promote CustomerRefs and LibraryFeedback to Neptune
> - **500-question RFPs** → Map becomes Distributed Map
> - **Iterative retrieval** ('evidence is thin, let me query more') → wrap the generator in Bedrock Agents
> - **Interactive rep-facing chat** → Amazon Q Business on the same data
>
> Each is an additive change, not a rewrite. That's the payoff for picking managed services with clean boundaries.
>
> On 'Glean vs this' — Glean or Q Business replaces the retrieval substrate, roughly 20% of this system. You still need the scoring, hard rules, review workflow, output shaping. Worth buying only if the cost is amortized across enterprise-wide search, not just RFPs."

---

## [13:30 – 15:00] What makes this defensible

**[VISUAL: Back to top-level diagram, slowly zooming out]**

> "Three things I'd want you to take away.
>
> One — **source authority is the architectural lever**, not the LLM. Prior RFPs are a phrasing reference, not a factual authority. Freshness suppression is a two-timestamp comparison — no vectors, no model, deterministic. That single mechanism is what fixes library decay, and it's arguably more important than which foundation model we use.
>
> Two — **policy decisions live in deterministic code, not prompts**. The LLM produces content. Scoring, hard rules, and review gates *decide* what happens with that content. This separation is why the system is auditable six months later — every state transition is in Step Functions execution history, every generation carries `prompt_hash` and `response_hash`, every SME approval records what Primary source justified it. You can replay the decision that produced any answer in production.
>
> Three — **the self-cleaning flywheel is the production story**. Commercial RFP tools optimize for 'more approved answers in the library.' This architecture optimizes for 'fewer stale approved answers.' `corroborated_by` links approvals to the documents that justified them, the staleness daemon watches those documents for updates, approvals invalidate themselves automatically. The library is bounded in staleness by design, not by maintenance discipline.
>
> For this audience: none of the pieces are novel in isolation. Step Functions + Bedrock + DynamoDB + Guardrails is the standard AWS-native stack. The architectural value is in the *combination* — source-authority dispatch, composite confidence, corroboration provenance, enterprise-legal hard rules — assembled into a shape that addresses the actual failure mode of RFP tooling, not the surface problem.
>
> Full architecture docs are in the repo under `/docs` — architecture.md is the main one, knowledgesources.md, scoringmechanism.md, dataflow.md go deeper on the respective layers. Happy to go deeper on any of it.
>
> Thanks for fifteen minutes."

---

## Production notes for the recording

### Pacing

- 150–170 words per minute for technical content; the script is sized to that
- Don't rush the source-authority section (3:30–6:00); that's the money shot
- Pre-record the pipeline execution (it takes ~100 seconds live) and splice it at 7:30. Don't show dead air

### Visuals to prepare in advance

- Full architecture diagram (export from `docs/architecture.md` §3 in mermaid-live as PNG)
- Per-question pipeline diagram (§4)
- Source-authority diagram (§5.1)
- Four corroboration rules table (§5.2)
- Confidence weights math `0.25 + 0.15 + 0.10 + 0.05 = 0.55` as a slide
- Trade-offs table excerpt (§12)
- Pre-cropped screenshot of review UI with amber/red cards + confidence badges

### Screen recording setup

- 1920×1080 minimum, 60fps if you can
- Upload UI tab + Step Functions tab + Review UI tab pre-loaded
- Close notifications, Slack, mail
- Run `demo_rfp_techops.xlsx` once beforehand so a known-good job is ready to show

### Voice

- Start a touch slower than feels natural, pick up pace as you warm in
- Pause at the transitions (2:30, 6:00, 9:30, 12:00) — let the audience breathe
- On the "three things to take away" close, slow down. That's the line they'll remember

### What to cut if you're running long

- The API Gateway presigned-URL detail at 6:30 (cut to ~30 seconds)
- The trade-offs enumeration at 12:00–13:30 (keep 2 of 4)
- The scale-up list at 13:00 (keep Kendra + Neptune, cut the rest)

### What to expand if you're running short

- A second concrete freshness-suppression example at 5:30
- Show the actual CloudWatch log lines from a generator invocation
- Open the downloaded xlsx and read one green + one amber comment aloud

---

## Pre-flight checklist

Run through this 30 minutes before recording:

- [ ] Bedrock model access confirmed (`aws bedrock list-foundation-models | grep sonnet-4-5`)
- [ ] `python3.13 scripts/seed_reference_data.py` has been run against the target account
- [ ] `https://rfp-copilot.meringue-app.com` loads correctly in your recording browser
- [ ] One known-good job already in WAITING_FOR_REVIEW state, ready to demo the review UI
- [ ] Step Functions console open to the state machine, recent execution visible
- [ ] CloudWatch Logs Insights query saved for the generator Lambda (in case you want to show it)
- [ ] `demo_rfp_techops.xlsx` ready to drag onto the upload page
- [ ] All non-essential browser tabs closed
- [ ] System notifications muted (Slack, Mail, Calendar)
- [ ] External monitor at the resolution your screen recorder expects
- [ ] Headphones in (audio quality matters more than video)
- [ ] Glass of water within reach
