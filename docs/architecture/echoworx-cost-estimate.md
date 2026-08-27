# ENG-2 Echoworx PoC — Cost Estimate (Bolt 2)

**Agent:** finops-analyst · **Bolt:** 2 · **Date:** 27 Aug 2026
**Ceiling:** USD 1,500 · **Account TTL:** 2026-10-11 · **Region:** us-east-1 · VeUP ephemeral PoC-OU account
**Status:** pre-deploy estimate. The SC6 *measured* figure comes from `veup:engagement` tag-scoped Cost Explorer data post-deploy. This document exists to tell us now whether that measurement can land inside the ceiling.

---

## 0. Headline — read this paragraph if you read nothing else

**SC1 fits, and it fits comfortably — but only because of one line of code, and the documentation that describes that line is wrong in a direction that blows the ceiling.**

One full 250-question run costs **USD 19.99, or 8.0 cents per question**, and the whole engagement projects to **USD 474 against a USD 1,500 ceiling — 68% headroom**. That result depends entirely on `DivergenceDetector.max_pairs = 60`, which in `lambdas/divergence/detector.py:166` caps **pairs**, not priors. HANDOFF §4.1 prose, and the Bolt 2 brief derived from it, both describe the cap as *"capped at 60 priors"*. 60 priors is 1,770 pairs, not 60. Build it the way it is written down and the same run costs **USD 342.67 (USD 1.37 per question)**, the ceiling supports **4.4 full runs**, and SC1 + SC3 (which requires two identical runs) + a demo + the eval harness overrun USD 1,500 before anything goes wrong. The gap between the code and its own documentation is a factor of **29.5× on the dominant cost line**. Resolve which one is intended before UOW-5 wires the detector into the Distributed Map — after that it is an invoice, not a decision.

Second headline, smaller but real: **prompt caching does not engage anywhere in this pipeline as currently coded.** Bedrock's minimum cache-checkpoint size is 1,024 tokens for Claude Sonnet 4.5 and **4,096 tokens for Claude Haiku 4.5**. The comparator's cached system prefix is ~358 tokens and its entire prompt is ~1,500 tokens; the generator's is ~248 tokens; the classifier's is ~149. The design claims caching as mandatory and cost-shaping. It is currently inert, and this estimate is priced with it inert (a USD 3.14/run, ~16% understatement if it were made to work).

---

## 1. Named assumptions

Everything in §3 onward is computed from these. Change a number here and the arithmetic moves predictably; nothing below is buried in a formula.

### 1.1 Volume

| ID | Assumption | Value | Basis / confidence |
|---|---|---|---|
| V1 | Questions in the SC1 run | **250** | SC1 says ">=250" (CAIQ v4). Modelled at the floor. Sensitivity in §7. |
| V2 | Compound-question rate | **20%**, avg 2.5 sub-questions | `question_classifier/handler.py` docstring: "80% of RFP questions are atomic". |
| V3 | Answer units per run (V1 x V2) | **325** | 250 x (0.8x1 + 0.2x2.5). Retrieval + generation bill per answer unit, not per questionnaire row. |
| V4 | Questions with >=2 prior answers (divergence-eligible cells) | **60% = 150** | Estimate. Echoworx corpus size is an open question (HANDOFF §6.2). Drives the dominant cost line — see §7. |
| V5 | Pairwise comparisons per divergence cell | **60** (`max_pairs`, as coded) | `detector.py:166,295`. **This caps pairs.** See §4. |
| V6 | Questions routed to the AgentCore harness (multi-hop) | **25% = 63** | HANDOFF §2.1: harness only for classifier-flagged multi-hop. Estimate. |
| V7 | Extra model turns per harness question | **5** | An agent loop replacing one single-shot generate. Estimate; the harness is the least-bounded line in the model. |
| V8 | KB retrieval queries per answer unit | **4** | `dispatch.py` unions primary/secondary/tertiary sources + the prior-answer query. |

### 1.2 Tokens per call

Measured from the repo where the prompt exists; estimated where the Echoworx version is not yet written.

| ID | Assumption | Value | Basis |
|---|---|---|---|
| T1 | Generator system prefix | **248 tok** | Measured: `generator/handler.py` SYSTEM_PROMPT = 941 chars. Will grow with the Echoworx pattern library. |
| T2 | Generator variable input | **1,900 tok** | 5 passages x 300-token chunks (`knowledge-base-stack.ts` maxTokens: 300) + prior answer + question. |
| T3 | Generator output | **350 tok** | `max_tokens=768`; a questionnaire answer is shorter than the cap. |
| T4 | Comparator system prefix | **358 tok** | Measured: `comparator.py` SYSTEM_PROMPT = 1,362 chars. |
| T5 | Comparator variable input | **860 tok** | Question ~53 tok + 2 answers truncated at `MAX_ANSWER_CHARS = 1500` (~395 tok each) + template. |
| T6 | Comparator output | **8 tok** | `max_tokens=8`, four-word closed vocabulary. |
| T7 | Classifier | **209 tok in / 60 out** | Measured DECOMPOSE_SYSTEM = 565 chars + question. |
| T8 | Guardrail text units per answer | **2** | ~1,400 chars of generated answer; 1 text unit = 1,000 chars. Output-only (`check_output`). |
| T9 | **Prompt caching engages** | **NO** | Bedrock minimum checkpoint: Sonnet 4.5 = 1,024 tok, Haiku 4.5 = 4,096 tok. T1/T4/T7 are all below. See §5. |

### 1.3 Corpus and storage

| ID | Assumption | Value | Basis |
|---|---|---|---|
| C1 | Ingested corpus | **5M tokens** (~3,500 pages) | Estimate — Echoworx corpus scope is an open question. Cost is insensitive to this (§3.2). |
| C2 | Chunking overhead | **1.2x** | FIXED_SIZE 300 tokens / 20% overlap. |
| C3 | Vectors produced | **20,000** | C1 x C2 / 300. |
| C4 | Bytes per vector | **~5 KB** | 1024 dims float32 = 4 KB + sidecar metadata. |
| C5 | Re-ingests during engagement | **3** | Chunking/redaction changes force a re-embed. |

### 1.4 Run frequency to 11 Oct TTL

| ID | Assumption | Value | Rationale |
|---|---|---|---|
| F1 | Full 250-question runs | **8** | SC1 rehearsals, SC3 determinism (needs 2 identical), SC5 corpus-wide redaction evidence, demo, 2 spare. |
| F2 | Reduced-scale dev/integration runs (40 q) | **40** | Build-loop iteration across UOW-5..8. |
| F3 | Eval harness runs (50-question holdout) | **12** | SC2 tuning iterations. |
| F4 | Idle window | **45 days** (27 Aug -> 11 Oct) = 1.5 months | Account TTL. |

### 1.5 Prices — all fetched 27 Aug 2026, us-east-1

| Component | Price | Source (and its own date stamp) |
|---|---|---|
| Claude Sonnet 4.5 (Bedrock) | $3.30/M in, $16.50/M out, $0.330/M cache read, $4.125/M cache write | pricepertoken.com model page, updated **26 Aug 2026** |
| Claude Haiku 4.5 (Bedrock) | $1.00/M in, $5.00/M out, $0.100/M cache read, $1.250/M cache write | pricepertoken.com model page, updated **26 Aug 2026**; $1/$5 corroborated by CloudZero Bedrock pricing guide, updated **20 Aug 2026** |
| Titan Text Embeddings V2 | $0.0200/M input tokens, output free | futureagi.com Bedrock calculator, fetched **27 Aug 2026** |
| Bedrock prompt-cache minimums | Sonnet 4.5 **1,024 tok**; Haiku 4.5 **4,096 tok**; TTL 5 min / 1 h | AWS docs, `bedrock/latest/userguide/prompt-caching.html`, fetched **27 Aug 2026** |
| Bedrock Guardrails | $0.15 per 1,000 text units (content filters / denied topics) | cloudchipr.com, article dated **18 Dec 2025** — **weakest citation in this table**, see §8 |
| S3 Vectors | $0.06/GB-mo storage; $0.20/GB PUT (128 KB min per PUT); $2.50/M queries + $0.004/TB processed (<100k vectors) | aws.amazon.com/s3/pricing, fetched **27 Aug 2026** |
| S3 Standard | $0.0265/GB-mo; $0.005/1k PUT; $0.0004/1k GET | same |
| Step Functions Standard | **$0.000025 per state transition**; 4,000 free/month. Distributed Map: **"one state transition per iteration"** | aws.amazon.com/step-functions/pricing, fetched **27 Aug 2026** |
| Lambda (x86) | $0.0000166667/GB-s; $0.20/M requests; free tier 1M req + 400k GB-s/mo | aws.amazon.com/lambda/pricing, fetched **27 Aug 2026** |
| DynamoDB on-demand | $0.625/M WRU; $0.125/M RRU; $0.25/GB-mo storage; PITR $0.20/GB-mo; Streams $0.02/100k | aws.amazon.com/dynamodb/pricing/on-demand, fetched **27 Aug 2026** |
| CloudWatch | Logs $0.50/GB ingest (Standard), $0.25/GB (IA), $0.03/GB-mo storage; dashboards **$3.00/mo**; X-Ray $0.000005/trace | aws.amazon.com/cloudwatch/pricing, fetched **27 Aug 2026** |
| KMS | **$1.00/CMK/month**; $0.03/10k requests; first + second rotation add $1/mo each | aws.amazon.com/kms/pricing, fetched **27 Aug 2026** |
| CloudFront | Free tier 100 GB egress + 1M requests/month | aws.amazon.com/cloudfront/pricing, fetched **27 Aug 2026** |
| Cognito | Lite/Essentials free to **10,000 MAU**; then $0.0055 / $0.015 per MAU | aws.amazon.com/cognito/pricing, fetched **27 Aug 2026** |
| AgentCore Gateway | **$0.005/1,000** API invocations (ListTools/InvokeTool/Ping); Search $0.025/1,000; tool indexing $0.02/100 tools/mo | aws.amazon.com/bedrock/agentcore/pricing, fetched **27 Aug 2026** |
| AgentCore Runtime (harness compute) | **$0.0895/vCPU-hour**, **$0.00945/GB-hour**; memory billed on peak footprint for session lifetime | same |
| AgentCore Evaluations | Built-in **$0.0024/1k input tok, $0.012/1k output tok**; custom evaluators $1.50/1,000; batch $0.0018/$0.009 | same |
| OpenSearch Serverless (counterfactual only) | **$0.24/OCU-hour**, classic collection minimum **2 OCU**; managed storage $0.02/GB-mo; NextGen collections scale to zero | aws.amazon.com/opensearch-service/pricing, fetched **27 Aug 2026** |

---

## 2. THE SC6 NUMBER — one full 250-question run

Computed from §1. Divergence at the coded 60-pair cap, prompt caching inert (T9).

| Line | Volume | Cost | % of run |
|---|---|---|---|
| **Haiku — divergence comparator** | 9,000 calls (150 cells x 60 pairs) | **$11.32** | **56.6%** |
| Sonnet — answer generation | 325 answer units | $4.18 | 20.9% |
| Sonnet — AgentCore harness extra turns | 63 q x 5 turns | $4.05 | 20.3% |
| Haiku — question classifier | 250 calls | $0.127 | 0.6% |
| Bedrock Guardrails (output only) | 650 text units | $0.097 | 0.5% |
| Lambda (13 functions, 1024 MB) | ~4,000 GB-s, 1,500 invokes | $0.067 | 0.3% |
| AgentCore Runtime (harness microVM) | 63 sessions | $0.061 | 0.3% |
| Step Functions Standard | ~1,750 transitions | $0.044 | 0.2% |
| CloudWatch Logs + X-Ray | ~10 MB, ~1,250 traces | $0.030 | 0.2% |
| S3 Vectors queries | 1,300 queries | $0.003 | <0.1% |
| Titan Embed v2 (query side) | 52,000 tokens | $0.001 | <0.1% |
| DynamoDB, API Gateway, S3 | — | $0.002 | <0.1% |
| AgentCore Gateway | 1,000 invocations | $0.005 | <0.1% |
| **TOTAL — one full run** | | **$19.99** | 100% |
| **Per-question unit cost** | 250 questions | **$0.0800** | |
| *(per answer unit, incl. decomposition)* | *325 units* | *$0.0615* | |

**Bedrock model tokens are 98.4% of the run.** Every non-model line in this table combined is 32 cents. This is the correct shape for a PoC of this design and it means cost control lives entirely in call volume and prompt size, not in infrastructure choices.

At $19.99/run the USD 1,500 ceiling buys **75 full 250-question runs**.

---

## 3. The other breakouts

### 3.1 One-off corpus ingest and embedding

| Line | Basis | Cost |
|---|---|---|
| Titan Embed v2 | 5M tok x 1.2 chunking overhead = 6M tokens @ $0.02/M | $0.12 |
| S3 Vectors PUT | 20,000 vectors; **worst case** 128 KB minimum per PUT = 2.44 GB billable @ $0.20/GB | $0.49 |
| S3 Standard storage (source corpus) | ~2 GB | $0.05/mo |
| S3 Vectors storage | 20,000 x 5 KB = 0.1 GB @ $0.06/GB-mo | $0.006/mo |
| Redaction + ingestion Lambdas | ~2,000 GB-s | $0.03 |
| **Total per ingest** | | **~$0.70** |
| **x3 re-ingests (C5)** | | **~$2.10** |

The 128 KB-minimum-per-PUT rule on S3 Vectors is the only non-obvious line here: a 5 KB vector written individually bills as 128 KB, a 25x inflation. Bedrock KB batches its writes, so the real figure is likely well under $0.49 — but the worst case is carried in the model because it costs nothing to carry. **Ingest cost is not a budget concern at any plausible corpus size**: even a 50M-token corpus (10x C1) lands at ~$6.

### 3.2 UOW-8 evaluation harness against the >=50-question holdout

| Line | Basis | Cost |
|---|---|---|
| Pipeline run over 50 holdout questions | 50/250 x $19.99 | $4.00 |
| AgentCore Evaluations — built-in, 4 evaluators | 50 items x 4 x (1,500 in @ $0.0024/1k + 200 out @ $0.012/1k) | $1.20 |
| **Per eval run** | | **$5.20** |
| **x12 tuning iterations (F3)** | | **$62.40** |

Note the Evaluations rate: **$2.40 per million input tokens** is 2.4x Sonnet 4.5's own input price and 72% below its output price. At a 50-item holdout this is $1.20 and irrelevant. Running Evaluations across a full 250-question output would be ~$6 per pass — still small, but the batch tier ($0.0018/$0.009) is 25% cheaper if the harness is ever run unattended.

### 3.3 Idle / at-rest cost between demo runs, to 11 Oct TTL

The single best cost property of this architecture: **there is no always-on compute.** No NAT gateway, no OpenSearch cluster, no provisioned DynamoDB, no Runtime instance held warm. Everything at rest is storage and per-key charges.

| Line | Monthly | To TTL (1.5 mo) |
|---|---|---|
| KMS customer-managed keys — 4 CMKs (`storage-stack.ts`: incoming, reference, output, audit) | $4.00 | $6.00 |
| CloudWatch dashboard (`rfp-copilot-ops`) | $3.00 | $4.50 |
| S3 Standard + S3 Vectors + DynamoDB storage + PITR | ~$0.40 | $0.60 |
| CloudFront, Cognito, API Gateway at idle | free tier | $0.00 |
| **Total idle** | **~$7.40** | **~$11.10** |
| *if the deferred CDK ingestion stack adds 2 more CMKs* | ~$9.40 | ~$14.10 |

For contrast, the counterfactual that NG7 forbids: **OpenSearch Serverless at the classic-collection 2-OCU minimum is $350.40/month — $526 to TTL, 35% of the whole ceiling spent on an idle index.** The repo's "roughly 70x S3 Vectors at demo scale" claim (`echoworx-poc-repurposing-plan.md` §2) checks out exactly: $350.40 vs ~$5. One caveat for accuracy: AOSS **NextGen collections** have no OCU minimum and scale to zero after 10 minutes idle, so the 70x figure is specific to classic collections. It remains the right comparison for an always-queryable demo endpoint, and the S3 Vectors decision (HANDOFF §2.4) is worth roughly the entire remaining budget. Do not reopen it.

### 3.4 Total-engagement projection against the USD 1,500 ceiling

| Bucket | Basis | Cost |
|---|---|---|
| 8 full 250-question runs (F1) | 8 x $19.99 | $159.93 |
| 40 reduced-scale dev runs, 40 q (F2) | 40 x $3.20 | $127.94 |
| 12 eval harness runs (F3) | 12 x $5.20 | $62.40 |
| 3 corpus ingests (C5) | 3 x $0.70 | $2.10 |
| Idle to 11 Oct (F4) | §3.3 | $11.10 |
| **Subtotal** | | **$363.47** |
| Contingency **+30%** — throttle-retry storms, failed runs re-run from scratch, prompt-tuning iterations, one accidental uncapped divergence sweep | | $109.04 |
| **PROJECTED ENGAGEMENT TOTAL** | | **$472.51** |
| **Ceiling** | | $1,500.00 |
| **HEADROOM** | | **$1,027.49 (68.5%)** |

**It fits, with room to double the run count twice over.** The 30% contingency is deliberately fat because the harness turn count (V7) and the divergence-eligible fraction (V4) are the two assumptions with the least evidence behind them, and both scale the largest lines.

---

## 4. The divergence detector: modelling the O(n^2), and what the cap actually buys

This is the whole ballgame. At 56.6% of a run it is the dominant variable cost, and its cost is quadratic in a quantity — the number of prior answers Echoworx has for a given question — that nobody has measured yet and that **grows monotonically as the corpus grows**.

### 4.1 Call volume

For a cell with `n` prior statements, `detector._pairs()` yields `min(C(n,2), max_pairs)` comparisons, most-recent-first. Each is one Haiku call at ~1,218 input + 8 output tokens = **$0.00126 per comparison**. Two calls are free: identical text short-circuits to AGREE, and the pair is sorted before the LRU cache so (A,B) and (B,A) are one entry — but within a single cell every pair is distinct, so the cache saves nothing intra-cell. It only helps across cells that share statements.

| priors `n` | uncapped pairs C(n,2) | calls/cell at cap 60 | cost/cell uncapped | cost/cell at cap |
|---|---|---|---|---|
| 5 | 10 | 10 | $0.013 | $0.013 |
| 11 | 55 | 55 | $0.069 | $0.069 |
| 12 | 66 | 60 | $0.083 | $0.076 |
| 20 | 190 | 60 | $0.239 | $0.076 |
| **40** | **780** | **60** | **$0.982** | **$0.076** |
| **60** | **1,770** | **60** | **$2.227** | **$0.076** |
| 100 | 4,950 | 60 | $6.228 | $0.076 |

**The cap binds from n=12 upward.** Below 12 priors it is inert; above it, cost is flat at $0.076/cell regardless of how deep the history goes.

### 4.2 What the cap buys, at run scale

150 divergence-eligible cells (V4):

| Configuration | Haiku calls/run | Divergence cost | **Run total** | **Per question** | Full runs affordable in $1,500 |
|---|---|---|---|---|---|
| **`max_pairs=60` — as coded** | 9,000 | $11.32 | **$19.99** | **$0.0800** | **75** |
| Uncapped, avg 40 priors (HANDOFF's own worked example) | 117,000 | $147.19 | $155.86 | $0.6234 | 9.6 |
| **`max_pairs` read as 60 *priors* = 1,770 pairs** | 265,500 | $334.00 | **$342.67** | **$1.3707** | **4.4** |
| Uncapped, avg 100 priors (year-3 corpus) | 742,500 | $934.20 | $942.87 | $3.7715 | 1.6 |

**The 60-pair cap buys a 29.5x reduction against the documented-prose reading and a 13x reduction against HANDOFF's own 40-prior worked example. It is worth USD 322 per run.** It is also the difference between an engagement that fits with 68% headroom and one that overruns the ceiling on full runs alone.

### 4.3 The code/documentation divergence (filed as a blocking finding)

- **Code** — `detector.py:166` `max_pairs: int = 60`; `detector.py:293-298` increments `count` per **pair** yielded from `combinations(ordered, 2)`.
- **Docstring, same file, lines 167-171** — "40 statements is 780 calls for one questionnaire cell. Cap it and compare the most recent" — consistent with pairs.
- **HANDOFF §4.1** — "Pairwise comparison is O(n^2) in model calls. 40 priors is 780 Haiku calls for one cell. **Capped at 60 by default**, most-recent-first." Ambiguous; reads naturally as 60 priors given the preceding sentence counts priors.
- **The Bolt 2 brief** — "capped at 60 priors by default". Unambiguous, and wrong relative to the code.

A builder implementing UOW-5 from the written brief will raise the cap to 60 priors, believing they are matching spec. That is a 29.5x cost increase delivered as a bug fix. **Decide and write it down before the detector gets its Step Functions state.**

### 4.4 Second-order note (cost-shaped, not a correctness verdict)

`combinations()` walks lexicographically over the date-sorted list, so a 60-pair budget spends 59 of its 60 comparisons on pairs involving the single most-recent statement plus one on (s1,s2). It is a *star* comparison against the newest answer, not a most-recent-first *sweep*. That is arguably the right thing for a cheap detector — the newest answer is the one about to be reused — but it is not what "compare the most recent" implies, and it means raising the cap buys much less marginal coverage than the linear cost increase suggests. **If more coverage is wanted, a top-k-most-recent windowing (`ordered[:k]`) is far cheaper per unit of coverage than raising `max_pairs`.** k=11 gives a complete sweep of the 11 newest statements for the same 55 calls.

### 4.5 The cheapest real lever, if divergence cost ever needs cutting

`MAX_ANSWER_CHARS = 1500` sets ~790 of the ~1,218 input tokens per comparison. Dropping it to 800 chars cuts input to ~790 tokens — a **34% reduction on the single largest cost line** ($11.32 -> $7.52), or $3.80/run, for the price of comparing first-paragraph claims only. The comparator's own docstring already argues that "a security-questionnaire answer states its control in the first paragraph; the tail is boilerplate". 1500 chars is more than a first paragraph.

---

## 5. Prompt caching does not engage (and this estimate assumes it does not)

`bedrock_client.invoke()` sets `cache_control: {"type": "ephemeral"}` on the system block by default, and three modules' docstrings treat the resulting saving as real:

- `bedrock_client.py:82-83` — "Savings kick in from the 2nd call that uses the same system prompt (~10% of input-token cost)"
- `comparator.py:45-47` — "Bedrock prompt caching (enabled by default...) pays for itself from the second comparison onward"
- `generator/handler.py:3` — "stable prefix is cached by Bedrock prompt caching"

Against AWS's published minimums (fetched 27 Aug 2026):

| Call site | Cached prefix | Whole prompt | Model minimum | Engages? |
|---|---|---|---|---|
| Divergence comparator | 358 tok | ~1,226 tok | Haiku 4.5: **4,096 tok** | **No — on any reading.** The entire prompt is 3.3x under the floor. |
| Answer generator | 248 tok | ~2,148 tok | Sonnet 4.5: **1,024 tok** | **No** on the prefix reading; the whole-prompt reading is arguable. Must be verified empirically. |
| Question classifier | 149 tok | ~209 tok | Haiku 4.5: 4,096 tok | **No.** |

The comparator case is the one that matters and it is not close. **This estimate is priced with caching inert**, which is the honest and conservative choice. If the generator prefix is grown past 1,024 tokens by the Echoworx pattern library and caching does engage there, the run drops from $19.99 to **$16.85** (-16%).

Two consequences worth acting on:
1. **Verify, don't assume.** `invoke()` already returns `payload["usage"]`. Assert on `cache_read_input_tokens > 0` in one integration test per call site. A caching claim that is false in the cost model is also false in the customer conversation.
2. **Do not pad prompts to reach the cache floor.** Padding the comparator's 358-token prefix to 4,096 tokens to unlock a $0.10/M cache-read rate costs more in cache writes and first-call input than it ever returns at these volumes. The lever that works on the comparator is §4.5, not caching.

---

## 6. Demo scale vs production scale — do not quote the wrong one

**$0.0800 per question is a demo-scale figure and must never be quoted for a production question.** Four things move against it at production scale, all in the same direction:

| Driver | Demo (this estimate) | Production | Effect |
|---|---|---|---|
| Questionnaire size | CAIQ v4, 250 questions | SIG, 1,000–2,000 questions | Linear on everything |
| Prior-answer depth `n` per cell | assumed modest; cap binds at n>=12 | grows every quarter; n=40-100 within 2 years | **The cap stops being a cost control and becomes an accuracy ceiling** — at n=100 it inspects 60 of 4,950 pairs (1.2%) |
| Corpus size | ~20,000 vectors | >100k docs | S3 Vectors query data-processing tier changes; the repo's own doc names >100k docs as the AOSS scale-up point (+$350/mo minimum) |
| Concurrency / throughput | 10-way inline Map, minutes of wall clock | throughput-provisioned or batch inference becomes rational | Batch inference and provisioned throughput change the per-token rate entirely |

A defensible production framing: **at the capped configuration, per-question cost is roughly flat at ~$0.08 and a 1,500-question SIG runs about $120 in model spend** — but the divergence coverage that number assumes degrades from ~100% of pairs at n<12 to ~1% at n=100. Production either accepts that degradation explicitly or pays for a smarter comparator (embedding-space pre-filter to shortlist candidate contradictions, then Haiku only on the shortlist — turns O(n^2) model calls into O(n^2) cosine distances plus O(k) model calls). That is a production design conversation, not a PoC one, and it is a better answer to "does this scale?" than a bigger number.

---

## 7. Sensitivity — which assumptions actually matter

Base run $19.99. Each row varies one assumption from §1, holding the rest.

| Assumption varied | Change | Run cost | Delta |
|---|---|---|---|
| V5 `max_pairs` 60 -> 1,770 (60 priors) | **29.5x on divergence** | **$342.67** | **+1,614%** |
| V5 `max_pairs` 60 -> uncapped @ n=40 | 13x on divergence | $155.86 | +680% |
| V4 divergence-eligible cells 60% -> 90% | +50% on divergence | $25.65 | +28% |
| V4 divergence-eligible cells 60% -> 30% | -50% on divergence | $14.33 | -28% |
| V1 questions 250 -> 500 | 2x everything | $39.98 | +100% |
| V6/V7 harness turns 5 -> 10 on 25% of questions | 2x harness line | $24.04 | +20% |
| T5 `MAX_ANSWER_CHARS` 1500 -> 800 | -34% on divergence | $16.19 | -19% |
| T9 caching made to engage | cache-read rates | $16.85 | -16% |
| V6 harness routing 25% -> 50% | 2x harness questions | $24.04 | +20% |
| Everything non-Bedrock doubled | infra x2 | $20.31 | +1.6% |

**Only one assumption in this model can breach the ceiling on its own, and it is V5.** Nothing else — not doubling the questionnaire, not doubling the harness, not doubling all infrastructure — gets within sight of $1,500 across the whole engagement.

---

## 8. Prices I could not establish

Stated plainly rather than guessed:

1. **The authoritative AWS Bedrock pricing page could not be read.** `aws.amazon.com/bedrock/pricing/` renders its model tables client-side; the fetched content returned only Claude 3.5-era rows. The AWS Price List API (`pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/...`) returned **HTTP 403** through this environment's egress proxy. All Bedrock model prices here come from third-party trackers dated 26 Aug 2026 (pricepertoken) and 20 Aug 2026 (CloudZero). **Re-verify in the account console before any figure leaves VeUP.**
2. **Sonnet 4.5's Bedrock premium is unexplained.** The source gives Sonnet 4.5 at $3.30/$16.50 on Bedrock — a 10% premium over Anthropic list — while giving Haiku 4.5 at $1.00/$5.00 with no premium. One of those is probably wrong. If Sonnet is actually $3.00/$15.00, the run drops to $19.24 (-3.8%); the conclusions do not move. Flagged rather than smoothed over.
3. **Bedrock Guardrails USD pricing is weakly sourced.** $0.15/1,000 text units for content filters and denied topics comes from a third-party article dated **18 Dec 2025**. The AWS EU pricing page gave EUR figures (EUR 0.2587 / EUR 0.1724 per 1,000 TU) in the same 1.5:1 ratio, which is consistent but not a US price. Contextual-grounding USD pricing not established. Immaterial at $0.10/run.
4. **AgentCore managed-harness billing beyond Runtime compute is not established.** The AgentCore pricing page lists Runtime microVM vCPU-hour and GB-hour but no line for the harness itself or for the Step Functions <-> harness integration. The repurposing plan asserts "the harness itself free". Modelled as Runtime compute only. **If a per-harness-invocation charge exists it is not published where I could find it** — worth a five-minute console check during UOW-7.
5. **AWS Budgets pricing not verified** in this pass. The §9 recommendation assumes the long-standing "first two budgets free per account" and should be confirmed before creating more than two.
6. **Bedrock Knowledge Bases `Retrieve` carries no separate published per-query charge** that I could establish — retrieval cost appears to be S3 Vectors queries plus the query embedding, which is how it is modelled. One third-party source lists re-ranking at ~$1.00/1,000 queries and SQL generation at ~$2.00/1,000; **this design uses neither**, and enabling re-ranking would add ~$1.30/run (a 6.5% increase) — cheap, but not free, and worth knowing before someone turns it on to improve SC2.

---

## 9. Guardrail recommendations (Deliverable 4)

### 9.1 Tagging — SC6 is unmeasurable today

`infra/bin/app.ts:59-61` applies exactly three tags, app-wide:

```
cdk.Tags.of(app).add('project', 'rfp-copilot');
cdk.Tags.of(app).add('stage', stage);
cdk.Tags.of(app).add('managed-by', 'cdk');
```

**None of `veup:engagement`, `veup:ttl` or `veup:owner` exists.** SC6 requires the run cost to be "reported from `veup:engagement` tag-scoped cost data". As deployed today that query returns nothing, and SC6 cannot be met regardless of what the run actually costs. Filed as a blocking finding.

Required change — three lines in the same place, so every construct in all six stacks inherits them:

```ts
cdk.Tags.of(app).add('veup:engagement', 'ENG-2');
cdk.Tags.of(app).add('veup:ttl', '2026-10-11');
cdk.Tags.of(app).add('veup:owner', '<VeUP owner>');   // set from context, not hardcoded
```

Four things that do not follow from `Tags.of(app)` and need explicit handling:

- **Activate the three keys as cost allocation tags** in Billing -> Cost allocation tags. An applied tag that is not activated does not appear in Cost Explorer or CUR. This is a console/CLI step, not a CDK one, and it is the step most often missed. Activation is **not retroactive** — do it before the first billable run or the SC1 evidence has a hole in it.
- **Bedrock model invocation charges do not carry resource tags.** Token spend is billed against the account, not against a tagged resource, so `veup:engagement`-scoped Cost Explorer data will show ~1.6% of the run and miss the 98.4% that is model tokens. **This is the practical obstacle to SC6 and it needs an answer now**: either the PoC account is single-tenant to ENG-2 (in which case account-scoped cost *is* engagement-scoped cost, and SC6's evidence is the account bill filtered by service + the tag report for the rest), or Bedrock spend is attributed via **model invocation logging** to the audit bucket with per-job token counts, which `bedrock_client.invoke()` already returns in `usage` but does not currently persist. Persisting `usage` per question into the questions table is a small change that makes the per-question unit cost directly measurable from the system's own data rather than inferred from a bill — and it is a better SC6 artefact than a Cost Explorer screenshot.
- **Enable Cost Explorer hourly granularity + resource-level data** in the PoC account on day one. Daily granularity cannot isolate a 25-minute run from the day's other activity.
- **Tag the CloudFront distribution and ACM certificate too** — the static-site stack deploys into a separate stack env; `Tags.of(app)` covers it, but confirm in the synthesized template.

### 9.2 AWS Budgets alarm shape for a USD 1,500 ceiling

Two budgets (within the commonly free allowance), both filtered to `veup:engagement = ENG-2` where filtering is possible and account-scoped otherwise:

**Budget 1 — "ENG-2 engagement ceiling"** — cost budget, **fixed period, not monthly.** A monthly budget is the wrong instrument here: the engagement spans 27 Aug to 11 Oct and a monthly reset hides cumulative burn across the boundary. Use a fixed period ending 2026-10-11 with a total of USD 1,500.

| Threshold | % of $1,500 | Amount | Type | Action |
|---|---|---|---|---|
| 25% | $375 | ACTUAL | notify | Email owner. Roughly the whole projected engagement — hitting this early means an assumption in §1 is wrong. |
| 50% | $750 | ACTUAL | notify | Email owner + Conductor. Investigate before the next full run. |
| 80% | $1,200 | ACTUAL | notify | Escalate to human lead. |
| 100% | $1,500 | FORECASTED | notify | Forecast breach — the earliest useful signal, fires before the money is spent. |
| 100% | $1,500 | ACTUAL | **budget action** | Apply a deny SCP/IAM policy on `bedrock:InvokeModel*` to the execution roles. Fail closed. |

Set the forecast alert **first** — on a 45-day engagement with bursty runs, an actual-cost alarm at 100% arrives after the damage.

**Budget 2 — "ENG-2 daily anomaly"** — a daily cost budget at **USD 60/day** (3x the cost of one full run) with an ACTUAL notification at 100%. This is the runaway-loop detector: an unbounded divergence sweep, a retry storm, or an uncapped harness session shows up here within hours, whereas the engagement budget would absorb it silently for days. At $19.99/run a legitimate heavy day is 2-3 full runs; $60 is a threshold a normal day cannot reach by accident.

Also enable **AWS Cost Anomaly Detection** with a monitor on the Bedrock service dimension and a $25 impact threshold. It is free, it catches the shape of failure this workload actually has (a sudden 30x on one service), and it needs no maintenance.

### 9.3 TTL hygiene — resources that outlive the engagement

- **`veup:ttl = 2026-10-11` on every resource** is what makes end-of-engagement sweep possible. Without it, teardown is manual archaeology.
- **`storage-stack.ts:104-110`: the audit bucket sets S3 Object Lock in COMPLIANCE mode with a 7-year default retention.** COMPLIANCE mode cannot be shortened or overridden by anyone, including the account root. Once anything is written there, that bucket cannot be emptied or deleted until 2033, and the account cannot be cleanly closed at TTL by deleting its contents. The storage cost is trivial (cents); the problem is that an ephemeral PoC-OU account acquires an undeletable object with a 7-year obligation attached. For a PoC this should be **GOVERNANCE mode** (which a privileged principal can override) or a retention period inside the account TTL. Filed as a finding.
- The four KMS CMKs have `enableKeyRotation: true`. Rotation is annual, so no rotation surcharge lands inside a 45-day engagement — but if the account is extended past 27 Aug 2027 each key's first rotation adds $1/mo. Non-issue at this TTL; noted so it is not a surprise if the TTL moves.
- `removalPolicy` is DESTROY only on the static-site bucket. The four storage buckets and four DynamoDB tables default to RETAIN on stack deletion, so `cdk destroy` will leave them (and their CMKs) behind, still billing ~$4.40/month. Set explicit destroy policies on everything except the audit bucket before the TTL sweep.

### 9.4 Small, cheap, do-them-anyway

- **Persist `usage` per question.** `invoke()` already returns input/output token counts. Writing them to the questions table makes the SC6 per-question unit cost a queryable fact from the system's own audit trail, costs ~$0.001/run in DynamoDB writes, and turns SC6 from a billing exercise into a product feature.
- **`logLevel: ALL` + `tracingEnabled` on the state machine and `Tracing.ACTIVE` on all 13 Lambdas** costs ~$0.03/run at 250 questions — keep it, the audit trail is a deliverable (SC3, SC4). But at production scale with `includeExecutionData` on a Distributed Map over 1,500 items this becomes a real CloudWatch line; note it in the production framing, don't change it here.
- **Distributed Map (UOW-5) does not change the cost picture.** Step Functions bills "one state transition per iteration" for Distributed Map; the current inline Map bills each of the ~6 inner states as a transition. Either way the run's Step Functions cost is 4-5 cents. The reason to move to Distributed Map is the 256 KB payload limit and the concurrency ceiling, not cost — and it is worth saying so, because it means the migration can be judged on correctness alone. One caveat: if child workflows are configured as **EXPRESS**, billing switches to per-request plus GB-second duration, which for a workload that spends most of its wall clock waiting on Bedrock is a *worse* deal than Standard's per-transition pricing. **Keep the child workflow type STANDARD.**

---

## 10. What would change this estimate most

In descending order of impact per unit of effort to resolve:

1. **A ruling on `max_pairs` — 60 pairs or 60 priors.** 29.5x on the dominant line. One sentence from the human closes it. (Blocking finding filed.)
2. **The actual distribution of prior-answer counts in Echoworx's corpus** (HANDOFF §6.2 / RISK-3). V4 and the effective `n` per cell are estimates; they set 57% of the run cost. The moment a real corpus lands, re-run §4 with the measured histogram — that is a 20-minute job and it converts the largest number in this document from estimate to fact.
3. **An empirical `cache_read_input_tokens` check** on one generator call. Settles §5 for $0.
4. **Harness turn count (V7)** measured on 5 real multi-hop questions. Sets 20% of the run.
5. **A console check of Bedrock's actual posted rates** in the PoC account, replacing the third-party citations in §1.5.
