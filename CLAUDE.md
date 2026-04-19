# CLAUDE.md — Working context for the RFP Redlining Copilot

You are helping **H** (AWS SAA-C03 & AIF-C01 certified, prepping SAP-C02, targeting Solutions Consultant roles) build and deploy this project. The architectural design evolved across several Cowork sessions and has been consolidated to a demo-tier architecture (v0.5). This file is the handoff contract. **Read `docs/architecture.md` before making any non-trivial architectural change.** Prior designs are archived under `docs/archive/`.

## What this project is

A Sales-facing AWS workflow that takes an incoming RFP/RFI Excel, auto-answers questions with citations, scores each answer's confidence via a composite algorithm, applies enterprise-legal hard rules, and returns the same Excel with color-coded highlights (green / amber / red) for rep and SME review. A flywheel captures SME approvals with corroboration provenance so the library self-cleans as authoritative sources evolve.

The narrative for demos and interviews: **"One Excel in, one Excel out. 70% first-draft for 90% less effort. SMEs review only what the system flags. The library gets smarter every approval and self-cleans when authoritative sources update."**

## Operating principles (Karpathy guidelines)

Source: <https://github.com/forrestchang/andrej-karpathy-skills> (behavioral guidelines derived from Andrej Karpathy's observations on common LLM coding failure modes). These govern **how** you work on this project. They bias toward caution over speed — for trivial tasks, use judgment.

### 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused; don't touch pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan with verify steps:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**Self-check:** these are working if diffs have fewer unnecessary changes, fewer rewrites due to overcomplication, and clarifying questions land before implementation rather than after mistakes.

### How these interact with this project's rules

The Karpathy guidelines govern *how*; the non-negotiable design decisions and hard rules below govern *what*. When the two apparently conflict — for example, a "simpler" design that violates a locked decision — the project rules win. Raise the conflict with the user; don't silently relitigate.

## Non-negotiable design decisions (v0.5 — do not relitigate without asking)

1. **Demo-tier scope.** This build targets a live interview demo + GitHub repo + pre-recorded walkthrough. Production-scale architecture is documented in `docs/architecture.md` §11 as the additive scale-up path; do not implement it in the demo tier.
2. **Generation:** Amazon Bedrock with **Claude Sonnet 4.6** for synthesis, **Claude Haiku 4.5** for classification. Prompt caching on the stable system prefix is mandatory. Bedrock Guardrails applied on every `InvokeModel`.
3. **Orchestration:** Step Functions Standard (need `waitForTaskToken` for SME review + full audit trail). Plain `Map` state at demo scale; Distributed Map is deferred.
4. **Source authority — the key differentiator.** Every topic maps to a dispatch plan declaring Primary (authoritative) + Secondary (phrasing) + Tertiary (context) sources. **Prior RFPs are NEVER treated as factual authority** — they are a phrasing reference only. Freshness suppression rule: if a Primary source's `updated_at` is more recent than a prior RFP's `approved_at`, the prior is suppressed from retrieval.
4a. **Retrieval (single surface):** Bedrock Knowledge Base backed by **S3 Vectors** (1024-dim Titan Embed v2). One KB indexes all four S3-backed source prefixes (`compliance/`, `product-docs/`, `prior-rfps/`, `sme-approved/`); the retriever calls `bedrock-agent-runtime:Retrieve` with a `source_type` metadata filter to discriminate. Primary/secondary/tertiary passages come back as regular `RetrievedPassage` objects; the H signal is a separate KB query filtered to `sme_approved_answer` and mapped into `PriorAnswerMatch` objects with real cosine scores. **There is no DynamoDB LibraryFeedback** — approvals write directly to S3 + trigger a KB ingestion job. Index must declare `AMAZON_BEDROCK_TEXT` and `AMAZON_BEDROCK_METADATA` as `nonFilterableMetadataKeys`. OpenSearch Serverless is the documented scale-up trigger.
5. **Confidence composite:** `0.45·H + 0.25·R + 0.15·C + 0.10·F + 0.05·G`. Never LLM self-reported. Thresholds ≥0.80 green, 0.55–0.80 amber, <0.55 red. H=0 caps at amber.
6. **Hard rules (enterprise-legal, not AI-safety):**
   - Pricing / commercial → forced RED. Commercial desk owns.
   - Compliance / certification claims → minimum AMBER. Compliance team owns.
   - Reference customer names → AMBER unless Customer References DB confirms `public_reference: true` + unexpired approval.
   - Forward-looking ("will deliver by Q3") → minimum AMBER.
   - Competitor disparagement → forced RED.
   Changes require General Counsel review. Tests in `lambdas/tests/test_hard_rules.py` lock behavior — **do not relax assertions to make tests pass**.
7. **Flywheel with provenance (Phase F: query-time staleness).** Every SME-approved answer records the Primary sources that corroborated it at approval time (`corroborated_by` sidecar field). Freshness suppression runs at *retrieval time* — the retriever's `_apply_freshness_suppression` compares each approved answer's `approved_at` against the freshest primary's `updated_at` in the same query. Stale approvals drop out of the H signal for that query without requiring a separate daemon. The staleness-daemon Lambda was removed in Phase F.
8. **Data hygiene.** Synthetic data only — fully fictitious. No real customer names. Incoming RFPs are NDA-protected and never auto-ingest into the reference corpus.
9. **IaC:** CDK v2 in TypeScript. Lambda source in Python 3.12 with `openpyxl`, `pydantic`, `boto3`, `structlog`.

## What's explicitly out of scope for the demo tier (do not add)

- **Amazon Kendra.** Kendra-class managed search is out — demo uses Bedrock KB + S3 Vectors (see §4a above). Kendra is the scale-up when source count > 10 and native connectors (Slack, Confluence, SharePoint) are needed.
- **OpenSearch Serverless (AOSS).** Documented as the next-tier vector store above S3 Vectors; triggered by sustained QPS > ~5 or corpus > ~100k docs. Not for demo.
- **Amazon Neptune.** Demo uses DynamoDB for Customer References and the Prior RFPs library metadata. Documented as scale-up when multi-hop queries emerge.
- **VPC + PrivateLink.** Demo runs Lambdas outside VPC. Documented as production hardening.
- **Distributed Map.** Plain `Map` at 10x concurrency handles 30-question demos. Distributed Map is documented as the 500-question scale-up.
- **Q Business.** Documented as the "interactive rep-facing chat" scale-up.
- **Bedrock Agents / AgentCore.** Deliberate determinism — see `docs/architecture.md` §15 for the reasoning and when to add agents.
- **React SME review UI polish.** A minimal working UI is in scope; heavy polish is deferred.

## Current status (v0.5 deployed, 2026-04-19 — post Phase F)

v0.5 is live in `us-east-1`. Two sequenced refactors landed today:

- **Phase A-E (earlier session):** retrieval re-platformed from the placeholder S3-keyword scan to Bedrock KB + S3 Vectors. See `plans/image-7-still-no-snazzy-fairy.md`.
- **Phase F (this session):** single retrieval surface. LibraryFeedback DynamoDB table deleted; SME-approved Q&A moved to the KB under `sme-approved/` prefix. Staleness daemon deleted. H signal is now real cosine similarity against the KB's `sme_approved_answer` slice, not a topic-tag overlap with fake 0.82.

The non-negotiable logic (confidence scorer formulas, hard rules, Excel I/O, Guardrails) was not touched in either refactor.

| Component | State | Notes |
|---|---|---|
| `infra/lib/storage-stack.ts` | ✓ deployed | Four buckets + KMS CMKs; `referenceKey` now public-readonly so KB stack can grant decrypt |
| `infra/lib/data-stack.ts` | ✓ deployed | Four DynamoDB tables: Jobs / Questions / Reviews / CustomerRefs. LibraryFeedback removed in Phase F. |
| `infra/lib/knowledge-base-stack.ts` | ✓ deployed (2026-04-19) | S3 Vectors bucket + index (Titan v2, 1024d, cosine), Bedrock KB + DataSource, KB service role. `nonFilterableMetadataKeys` set on the index — do not remove |
| `infra/lib/orchestration-stack.ts` | ✓ deployed | Plain `Map` (concurrency 10); retriever has `KNOWLEDGE_BASE_ID` env + `bedrock:Retrieve` grant; retriever timeout 90s |
| `infra/lib/observability-stack.ts` | ✓ deployed | CloudWatch metrics + dashboard |
| `infra/lib/static-site-stack.ts` | ✓ deployed | CloudFront + ACM on `rfp-copilot.meringue-app.com` |
| `lambdas/retriever/handler.py` | ✓ refactored (Phases E+F) | `_knowledge_base_retrieve` replaces the old S3 keyword scan; `_kb_prior_matches` replaces the old DDB scan for the H signal; envelope preserved so freshness suppression still fires uniformly |
| `lambdas/review_api/handler.py` | ✓ refactored (Phase F) | On approve, writes Q&A markdown + sidecar to `sme-approved/` in the reference corpus bucket and triggers `bedrock-agent:StartIngestionJob` (fire-and-forget). Old DDB PutItem path gone. |
| `lambdas/mock_sources` (Seismic + Gong) | ✓ deployed | Single Lambda, 5% error rate + tail latency |
| `lambdas/staleness_daemon` | **removed (Phase F)** | Query-time freshness suppression in the retriever covers what the daemon used to do asynchronously. |
| `lambdas/classifier/dispatch.py` | ✓ deployed | `DISPATCH_TABLE` with `source_type`-aware routing |
| Composite confidence scorer | ✓ 18/18 tests | Do not modify logic |
| Hard rules engine | ✓ tests lock behavior | Changes require General Counsel |
| Excel parser + writer | ✓ done | Smoke test produces colored workbook |
| Bedrock Guardrails | ✓ attached | Output-only apply_guardrail in the generator |
| SME review UI | Minimal | React UI exists for approve/reject; polish deferred |
| Synthetic data | ✓ regenerated | Real multi-page PDFs (compliance) + long-form markdown (product-docs, prior-rfps) under `data/corpus-real/`. Three priors intentionally stale vs. their primaries |

## What the user will likely ask you next

v0.5 is deployed and the retrieval path is real (Bedrock KB + S3 Vectors). Plausible next directions:

- **Full-pipeline tier-distribution regression check.** Upload `data/incoming/demo_rfp_techops.xlsx`, run end-to-end via the UI, confirm tier counts are within ±1 of the pre-KB-swap baseline. This is the last uncovered verify step from the 2026-04-19 refactor plan.
- **Add more source coverage.** Expand corpus docs (more compliance PDFs, deeper product-docs), expand `DISPATCH_TABLE` coverage for niche topics, expand LibraryFeedback seed beyond the current 8 Q&A.
- **SME review UI polish.** The minimal React UI works for approve/edit/reject but is cosmetically rough.
- **Observability tightening.** Add CloudWatch dashboard tile for KB retrieval latency; add a metric for `suppressed_prior_count` rate over time.
- **Demo scripts.** `scripts/reset-demo.sh` to restore synthetic data to known state in <2 min between demo runs.
- **Scale-up exploration** (document-only, don't implement): OpenSearch Serverless, Distributed Map, Kendra swap for the mock sources.

## First-time deployment checklist

```bash
# Prereqs
node --version     # >= 20
python --version   # >= 3.12
aws --version      # v2
aws sts get-caller-identity  # confirm sandbox account
docker info        # Docker Desktop must be running for PythonFunction bundling

# Install
cd infra && npm ci
cd ../lambdas && pip install -e ".[dev]" --break-system-packages

# Validate locally
cd ../lambdas && PYTHONPATH=. python -m pytest tests/ -v
cd .. && python scripts/generate_synthetic_data.py \
      && python scripts/generate_corpus_documents.py \
      && python scripts/smoke_test.py

# Bootstrap + deploy
cd infra && npx cdk bootstrap
npx cdk synth
npx cdk deploy --all

# Seed reference data + trigger KB ingestion
cd .. && python scripts/seed_reference_data.py
python scripts/seed_s3_corpus.py   # uploads data/corpus-real/ + polls KB ingestion to COMPLETE
```

## Known issues and deferred TODOs (v0.5)

- **Bedrock model IDs** — `anthropic.claude-sonnet-4-6-v1:0` and `anthropic.claude-haiku-4-5-v1:0` in `lambdas/shared/bedrock_client.py`, plus `amazon.titan-embed-text-v2:0` referenced by the KB stack. Confirm access is granted in the Bedrock console for the target region.
- **DynamoDB RemovalPolicy is RETAIN by default** — when the LibraryFeedback table was dropped from the data stack in Phase F, CloudFormation left the table orphaned in AWS. Clean up with `aws dynamodb delete-table`. Same pattern applies to any other DDB table removal.
- **Phase F multi-stack deploy sequence** — dropping a cross-stack export requires `--exclusively` so CDK doesn't try to deploy all dependencies. Order: KB (to add new export) → orchestration (with `--exclusively`, drops stale import) → data (with `--exclusively`, drops the export).
- **SME-approval-to-searchable latency** — when an SME approves an answer in the review UI, the approval writes to S3 immediately but the KB takes ~1–3 min to ingest before it's retrievable. This is acceptable because approvals benefit future RFPs, not the current one.
- **KB index name & KB name in CDK** — `indexName` is omitted so CFN auto-generates (allows replacement). `name` on the `CfnKnowledgeBase` carries a manual version suffix (`-v2`); bump when an immutable property forces replacement.
- **Replace-the-KB dance** — if an immutable KB property changes (storage config, embedding model, chunking), CFN's export-in-use check blocks in-place replacement. The 3-deploy dance is documented in `docs/architecture.md` §17. Plan accordingly before making such changes.
- **Orphaned KB cleanup** — KB replacements can leave the old KB + DataSource behind in `DELETE_UNSUCCESSFUL` state (old DS can't delete its vector-store data after old index is gone). Fix: `aws bedrock-agent update-data-source --data-deletion-policy RETAIN`, then delete DS, then delete KB. Cheap but console-noisy.
- **Circuit-breaker visibility** — CloudWatch metric + dashboard so the demo can show the breaker firing live when a mock source is deliberately disabled.
- **Bedrock Provisioned Throughput** — not configured. On-demand is fine for demo volumes; documented as a scale-up lever.
- **Tier-shape regression check** — full Step Functions pipeline run against `demo_rfp_techops.xlsx` to confirm tier distribution ±1 of pre-KB-swap baseline. Not yet done.

## How the user likes to work

- Decisive recommendations with trade-offs called out — not a menu of options. (Compatible with Karpathy "state assumptions / ask when uncertain" — be decisive about the right thing; ask when genuinely ambiguous rather than guessing silently.)
- Terse, technical, prose-heavy. Minimal headers, no lists when a sentence works.
- Mentor-style engagement — they're learning while building (SAP-C02 prep).
- They iterate fast and pivot decisively. If they park an idea, it's parked — don't reintroduce it.
- Honest critique over cheerleading.
- They ask for build-vs-buy and A/B-test framing; surface those when architecting for production.
- They deploy via Claude Code (you) from the local terminal. Cowork sessions handle design; you handle implementation + live AWS feedback.

## File map

```
rfp-copilot/
├── CLAUDE.md                     This file — handoff contract
├── README.md                     Human-facing overview (update to match v0.5)
├── docs/
│   ├── architecture.md           v0.5 architecture, diagrams, trade-offs — the design contract
│   ├── technical-faqs.md         47 Q&A for demo defense
│   └── archive/                  v0.4 and earlier
├── infra/                        CDK-TS IaC (six stacks: storage, data, knowledge-base, orchestration, observability, static-site)
│   └── lib/knowledge-base-stack.ts  S3 Vectors + Bedrock KB + DataSource + KB service role
├── lambdas/                      Python 3.12 Lambda sources
│   ├── shared/                   models, bedrock_client, logging
│   ├── excel_parser/             (do not modify logic)
│   ├── question_classifier/      handler.py + dispatch.py (DISPATCH_TABLE)
│   ├── retriever/                _knowledge_base_retrieve + freshness suppression (do not modify envelope)
│   ├── generator/                (do not modify logic)
│   ├── confidence_scorer/        (do not modify logic)
│   ├── hard_rules/               (do not modify logic — policy-locked)
│   ├── excel_writer/             (do not modify logic)
│   ├── mock_sources/             Single Lambda for Seismic + Gong mocks
│   └── tests/                    40 tests passing (4 suites + KB-adapter suite)
├── data/
│   ├── corpus/                   Compact JSON sidecars (source of truth for all 4 prefixes)
│   │   ├── compliance/           SOC 2 / ISO 27001 / FedRAMP primary source metadata
│   │   ├── product-docs/         Encryption / SSO-MFA / DR product-doc metadata
│   │   ├── prior-rfps/           Prior RFPs (phrasing reference, freshness-suppressed)
│   │   └── sme-approved/         SME-approved Q&A (H-signal source)
│   └── corpus-real/              Generated PDFs + markdown + Bedrock sidecar metadata (fed to KB)
├── scripts/
│   ├── generate_synthetic_data.py
│   ├── generate_corpus_documents.py   → data/corpus-real/
│   ├── seed_s3_corpus.py              uploads + triggers KB ingestion
│   ├── seed_reference_data.py         DynamoDB seeds
│   ├── smoke_test.py / reset-demo.sh / teardown.sh / deploy-for-demo.sh
├── plans/                        Session plan files (project-scoped, referenceable by path)
└── .github/workflows/ci.yml      Lint + typecheck + pytest
```

## When in doubt

Consult `docs/architecture.md` for design rationale, `docs/technical-faqs.md` for defensible answers to tough technical questions, and `docs/archive/` for prior designs that were deliberately simplified. **Do not re-introduce archived complexity into the demo tier** — it's archived for a reason.
