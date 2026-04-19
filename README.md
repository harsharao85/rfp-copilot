# RFP Redlining Copilot

Sales-facing copilot that takes an incoming RFP/RFI Excel, auto-answers questions with citations, scores each answer's confidence, and returns the same Excel with color-coded highlights (green / amber / red) for rep and SME review.

See `docs/architecture-plan.md` for the full architecture plan and `docs/technical-faqs.md` for the technical FAQ. For AI-pair-programmer context, see `CLAUDE.md`.

## Repo layout

```
rfp-copilot/
├── infra/              CDK-TypeScript IaC
│   ├── bin/app.ts      CDK app entry
│   └── lib/*.ts        One stack per architectural layer
├── lambdas/            Python 3.12 Lambda sources
│   ├── shared/         Common utilities (logging, Bedrock, Kendra, models)
│   ├── excel_parser/   openpyxl parser + Haiku-assisted cell classifier
│   ├── question_classifier/  Compound-question decomposition
│   ├── retriever/      Kendra + Neptune retrieval orchestration
│   ├── generator/      Bedrock Sonnet synthesis
│   ├── confidence_scorer/    Composite scoring (H,R,C,F,G weights)
│   ├── hard_rules/     Pricing / compliance / reference-customer enforcement
│   ├── excel_writer/   Output workbook with fills, comments, summary sheet
│   └── tests/          pytest suite
├── data/               Synthetic data (generated — not real)
│   ├── incoming/       Sample incoming RFP workbook
│   ├── historical/     Prior SME-approved RFPs (reference corpus)
│   ├── seismic/        Mock Seismic content cards
│   ├── gong/           Mock Gong call transcripts
│   ├── whitepapers/    Security/compliance policy docs
│   ├── graph/          Neptune seed: customers, products, topics (JSON)
│   └── output/         Pipeline output (gitignored)
├── scripts/
│   ├── generate_synthetic_data.py   Regenerates all data/ artifacts
│   ├── smoke_test.py                Parser → scorer → writer end-to-end local test
│   ├── bootstrap.sh                 First-time AWS account setup
│   └── reset_demo.sh                Restore demo to known state
└── .github/workflows/ci.yml         Lint, typecheck, unit tests
```

## Prerequisites

- Node 20+ and npm (for CDK)
- Python 3.12+
- AWS CLI v2 configured with admin-equivalent credentials
- CDK v2 bootstrapped in the target account/region (`npx cdk bootstrap`)

## Quick start (local, no AWS required)

```bash
# 1. Install Python deps
cd lambdas
pip install -e . --break-system-packages

# 2. Generate synthetic data
cd ..
python scripts/generate_synthetic_data.py

# 3. Run the local smoke test: parse → score → write
python scripts/smoke_test.py

# Open data/output/sample_rfp_acmesec_answered.xlsx to see the result
```

## Deploying to AWS (once ready)

```bash
cd infra
npm install
npx cdk synth
npx cdk deploy --all
```

## Status

| Phase | Status |
|---|---|
| 0. Foundations (IaC scaffold, repo structure) | Done — stacks stubbed, deploy-ready skeleton |
| 1. Synthetic data generation | Done — generator runs end-to-end |
| 2. Ingestion + Excel parser | Done — parser + writer implemented |
| 3. Kendra index | TODO — S3 connector wiring |
| 4. Neptune graph | TODO — Gremlin load scripts |
| 5. Step Functions Distributed Map | TODO |
| 6. Confidence scoring + hard rules | Done (logic implemented; Bedrock Guardrails config pending) |
| 7. Excel writer | Done |
| 8. SME review UI | TODO |
| 9. Q Business surface | TODO |
| 10. Telemetry + QuickSight | TODO |
| 11. Security hardening | TODO |
| 12. Demo polish | TODO |

## Design non-negotiables (carried forward from v0.4 plan)

1. **Pricing answers are always red.** Commercial desk owns.
2. **Compliance claims are always amber minimum.** Compliance team owns.
3. **Reference customers gated by `public_reference: true` in the graph.**
4. **No LLM self-reported confidence.** Composite score only.
5. **Incoming RFPs never auto-ingest into the reference corpus.** Post-deal, with legal sign-off.
6. **Synthetic data is fully fictitious.** No real customer names.
