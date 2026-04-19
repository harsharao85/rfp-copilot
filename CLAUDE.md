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
5. **Confidence composite:** `0.45·H + 0.25·R + 0.15·C + 0.10·F + 0.05·G`. Never LLM self-reported. Thresholds ≥0.80 green, 0.55–0.80 amber, <0.55 red. H=0 caps at amber.
6. **Hard rules (enterprise-legal, not AI-safety):**
   - Pricing / commercial → forced RED. Commercial desk owns.
   - Compliance / certification claims → minimum AMBER. Compliance team owns.
   - Reference customer names → AMBER unless Customer References DB confirms `public_reference: true` + unexpired approval.
   - Forward-looking ("will deliver by Q3") → minimum AMBER.
   - Competitor disparagement → forced RED.
   Changes require General Counsel review. Tests in `lambdas/tests/test_hard_rules.py` lock behavior — **do not relax assertions to make tests pass**.
7. **Flywheel with provenance.** Every SME-approved answer records the Primary sources that corroborated it at approval time. A staleness daemon (scheduled + on-demand) re-checks corroborating source `updated_at` and marks approved answers `corroboration_stale` when their Primary updates. Stale answers fall out of the H signal until re-approved.
8. **Data hygiene.** Synthetic data only — fully fictitious. No real customer names. Incoming RFPs are NDA-protected and never auto-ingest into the reference corpus.
9. **IaC:** CDK v2 in TypeScript. Lambda source in Python 3.12 with `openpyxl`, `pydantic`, `boto3`, `structlog`.

## What's explicitly out of scope for the demo tier (do not add)

- **Amazon Kendra.** Demo uses simple S3 + keyword search. Documented as scale-up when source count > 10.
- **Amazon Neptune.** Demo uses DynamoDB for Customer References and the Prior RFPs library metadata. Documented as scale-up when multi-hop queries emerge.
- **VPC + PrivateLink.** Demo runs Lambdas outside VPC. Documented as production hardening.
- **Distributed Map.** Plain `Map` at 10x concurrency handles 30-question demos. Distributed Map is documented as the 500-question scale-up.
- **Q Business.** Documented as the "interactive rep-facing chat" scale-up.
- **Bedrock Agents / AgentCore.** Deliberate determinism — see `docs/architecture.md` §15 for the reasoning and when to add agents.
- **React SME review UI polish.** A minimal working UI is in scope; heavy polish is deferred.

## Current status (v0.5 target state)

The codebase currently reflects v0.4 scope (Kendra, Neptune, Distributed Map, VPC). The v0.5 architecture in `docs/architecture.md` is the target — Claude Code's first task is to **refactor the codebase toward the v0.5 demo-tier scope**, stack-stripping cleanly without touching the non-negotiable logic (confidence scorer, hard rules, Excel I/O, Guardrails).

| Component | v0.4 state (now) | v0.5 target | Action needed |
|---|---|---|---|
| `infra/lib/network-stack.ts` (VPC) | Exists | Remove | Delete stack; remove from `infra/bin/app.ts` |
| `infra/lib/search-stack.ts` (Kendra) | Exists | Remove | Delete stack; remove from wiring |
| `infra/lib/graph-stack.ts` (Neptune) | Exists | Remove | Delete stack; remove from wiring |
| `infra/lib/storage-stack.ts` | Exists, VPC-tied | Keep, simplify | Remove KMS CMKs complexity if preferred; preserve buckets |
| `infra/lib/data-stack.ts` (DynamoDB) | Exists | Keep + extend | Add `CustomerReferences` and `LibraryFeedback` tables |
| `infra/lib/orchestration-stack.ts` | Uses VPC + Distributed Map + 10 Lambdas | Refactor | Remove VPC config; `Map` instead of `DistributedMap`; consolidate mock sources into one Lambda |
| `infra/lib/observability-stack.ts` | Exists | Keep | Adjust metrics to match new state machine shape |
| Mock Seismic + Gong | Not yet wired | Add | Single `mock_sources` Lambda behind API Gateway with path routing |
| Source authority / dispatch | Not present | Add | `lambdas/classifier/dispatch.py` with hardcoded `DISPATCH_TABLE` |
| Freshness suppression | Not present | Add | In `lambdas/retriever/handler.py` — compare Primary source `updated_at` vs. prior `approved_at` |
| Staleness daemon | Not present | Add | `lambdas/staleness_daemon/handler.py` + EventBridge schedule + API Gateway on-demand trigger |
| Composite confidence scorer | Done | Keep as-is | 18/18 tests passing — do not modify logic |
| Hard rules engine | Done | Keep as-is | Tests lock enterprise-legal constraints |
| Excel parser + writer | Done | Keep as-is | Smoke test produces colored output workbook |
| Bedrock Guardrails | Done | Keep as-is | Attached to generator via env var |
| SME review UI | Not started | Add minimal | React app + WebSocket API for amber/red approve/edit/reject |
| Synthetic data | Done for v0.4 | Re-seed for v0.5 | Add realistic `updated_at` timestamps; include intentionally stale priors to demo suppression |

## What the user will likely ask you next (v0.5 build plan)

**Phase A — Strip to demo-tier shape**

1. Read `docs/architecture.md` end-to-end to orient.
2. Remove VPC / Kendra / Neptune stacks and all references.
3. Refactor `orchestration-stack.ts` to plain `Map` state; consolidate Lambdas where §6 of the architecture doc suggests.
4. Verify `npx cdk synth` runs clean.

**Phase B — Add source authority**

5. Create `lambdas/classifier/dispatch.py` with the `DISPATCH_TABLE` from `docs/architecture.md` §5.3.
6. Modify `lambdas/classifier/handler.py` to return `topics + dispatch_plan`.
7. Modify `lambdas/retriever/handler.py` to consume `dispatch_plan` and apply the four corroboration rules.
8. Add tests for freshness suppression (prior with older `approved_at` than Primary's `updated_at` → suppressed).

**Phase C — Mock sources + staleness daemon**

9. Build the single `mock_sources` Lambda with `/seismic/content` + `/gong/calls` paths, simulated latencies and 5% error rate.
10. Build `staleness_daemon` Lambda with EventBridge daily schedule + API Gateway on-demand trigger.
11. Seed synthetic data with realistic timestamps — include at least two prior RFPs that are intentionally stale vs. their corroborating Primary source.

**Phase D — SME review UI**

12. Minimal React app — list of amber/red questions, approve/edit/reject buttons.
13. WebSocket API Gateway + Lambda that calls `SendTaskSuccess` with reviewer input.
14. On approval, write to `LibraryFeedback` table with `corroborated_by` + `corroborated_at`.

**Phase E — Deploy + demo polish**

15. `scripts/deploy-for-demo.sh` + `scripts/teardown.sh` for fast cycle.
16. Reset-demo script that restores synthetic data to known state in under 2 minutes.
17. Dry-run the full demo flow end-to-end. Verify: upload → parse → process → download → stale-prior suppression visible in audit log → circuit breaker fires when mock source is disabled.

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
cd .. && python scripts/generate_synthetic_data.py && python scripts/smoke_test.py

# Bootstrap + deploy
cd infra && npx cdk bootstrap
npx cdk synth
npx cdk deploy --all
```

## Known issues and deferred TODOs (v0.5)

- **Bedrock model IDs** — `anthropic.claude-sonnet-4-6-v1:0` and `anthropic.claude-haiku-4-5-v1:0` in `lambdas/shared/bedrock_client.py`. Confirm these match the target AWS region's Bedrock catalog via `aws bedrock list-foundation-models`. Also confirm model access is granted in the Bedrock console.
- **DynamoDB-backed Customer References** — v0.4 had this in Neptune; v0.5 moves it to DynamoDB. Seed from `data/graph/customers.json` with a one-off loader script `scripts/seed_customer_refs.py`.
- **Prior RFPs corpus format** — should land in S3 as JSON-per-answer with `topic_ids`, `approved_at`, `expires_on`, `corroborated_by` fields for the freshness-suppression rule to work.
- **Staleness daemon on-demand endpoint** — API Gateway trigger lets the demo manually fire the staleness check mid-session to visibly demonstrate the mechanism.
- **Circuit-breaker visibility** — CloudWatch metric + dashboard so the demo can show the breaker firing live when a mock source is deliberately disabled.
- **Bedrock Provisioned Throughput** — not configured. On-demand is fine for demo volumes; documented as a scale-up lever.

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
├── infra/                        CDK-TS IaC (refactor per Phase A above)
├── lambdas/                      Python 3.12 Lambda sources
│   ├── shared/                   models, bedrock_client, logging
│   ├── excel_parser/             (done — do not modify logic)
│   ├── classifier/               add dispatch.py + modify handler.py
│   ├── retriever/                refactor: replace Kendra+Neptune calls with direct source calls + corroboration rules
│   ├── generator/                (done — keep as-is)
│   ├── confidence_scorer/        (done — do not modify logic)
│   ├── hard_rules/               (done — do not modify logic)
│   ├── excel_writer/             (done — do not modify logic)
│   ├── mock_sources/             NEW — single Lambda for Seismic + Gong mocks
│   ├── staleness_daemon/         NEW
│   └── tests/                    18 tests passing, extend for source-authority
├── data/                         Synthetic fixtures (re-seed per Phase C)
├── scripts/                      generate_synthetic_data.py, smoke_test.py, + new deploy/teardown/reset
└── .github/workflows/ci.yml      Lint + typecheck + pytest
```

## When in doubt

Consult `docs/architecture.md` for design rationale, `docs/technical-faqs.md` for defensible answers to tough technical questions, and `docs/archive/` for prior designs that were deliberately simplified. **Do not re-introduce archived complexity into the demo tier** — it's archived for a reason.
