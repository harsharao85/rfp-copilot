# Echoworx questionnaire copilot

Inbound security-questionnaire copilot. A CAIQ / SIG / VSA-style Excel workbook goes in;
every question comes back answered, cited, and colour-coded green / amber / red, with only
the uncertain items routed to a human. Approved answers feed the corpus, so the next
questionnaire starts from a better place than the last one.

Built as a VeUP proof of concept for Echoworx, who receive these assessments from their own
prospects and customers and burn senior SME time re-answering substantially the same controls.

**The engineering that matters is not the model call.** It is everything around it: answers
that cite real documents, expired evidence that cannot ship unreviewed, pricing that the
system is structurally incapable of inventing, and a tier that is decided by deterministic
code rather than by an LLM's opinion of its own confidence.

> The repo directory is still named `rfp-copilot` and stack names still carry the
> `rfp-copilot-` prefix. That is history, not intent — see `HANDOFF.md` §1.

## Start here

| You want to | Read |
|---|---|
| Deploy this to AWS and prove it works | **`docs/DEPLOY-RUNBOOK.md`** |
| Present it | **`docs/DEMO-SCRIPT.md`** |
| Understand the target architecture | `docs/architecture/echoworx-target-architecture.md` |
| Understand why things are the way they are | `HANDOFF.md` |
| Threat model, cost, CAIQ control mapping | `docs/architecture/` |

`docs/architecture.md` and `docs/technical-faqs.md` are the v0.4/v0.5 sales-side documents.
They are still the most detailed writing in the repo on scoring, guardrails and cost shape,
and worth reading for that — but they predate the Echoworx repurposing and both argue for a
Kendra-and-Neptune retrieval design that this build does not use. Treat
`docs/architecture/echoworx-target-architecture.md` as authoritative where they disagree.

**Nothing in this repository has ever been deployed to real AWS.** The pipeline is verified
end to end by two independent offline harnesses; Docker Lambda bundling in particular has
never executed. `docs/DEPLOY-RUNBOOK.md` §0 says exactly what that means for a first deploy.
Read it before you run `cdk deploy`.

## How it works

```
upload.html  ──▶  Upload API  ──▶  S3 (incoming)  ──▶  Step Functions
                                                            │
             ┌──────────────────────────────────────────────┤ per question
             ▼                                              ▼
  excel_parser ─▶ question_classifier ─▶ retriever ─▶ generator
                                            │              │
                              Bedrock KB ────┘              ▼
                            (S3 Vectors)          confidence_scorer
                                                            │
                                                       hard_rules
                                                            │
                                                            ▼
                                          excel_writer (draft) ─▶ review_gate
                                                                       │
                                                     review.html ◀─────┘  task token
                                                            │
                                            approve ─▶ excel_writer (final)
                                                            │
                                        approved answers ─▶ corpus ─▶ KB
```

**Retrieval is Bedrock Knowledge Bases over S3 Vectors**, embeddings by Amazon Titan Embed
Text v2 (1024 dimensions, cosine), fixed-size chunking at 300 tokens with 20% overlap. Sidecar
metadata — `document_id`, `source_type`, `updated_at` / `approved_at`, `topic_ids` — is
filterable and travels with the passage, so a citation can name the document and its currency
without the metadata influencing similarity.

There is no Kendra and no Neptune. Both appeared in the v0.4 design and were dropped
deliberately — Kendra's floor is around $810/month against a two-dozen-document corpus, and
Neptune is a standing hourly cost for multi-hop traversal that no query in this pipeline
performs. S3 Vectors runs around $5/month at this scale
(`infra/lib/knowledge-base-stack.ts`). OpenSearch Serverless is the documented scale-up when
sustained QPS or a corpus beyond ~100k documents justifies it.

**Tiering is never done by a model.** `confidence_scorer` computes a composite over five
signals — H (match against prior approved answers, 0.45), R (retrieval strength, 0.25),
C (coverage, 0.15), F (source freshness, 0.10), G (guardrail, 0.05) — and `hard_rules` applies
deterministic floors on top. Both are plain Python you can read and an auditor can follow.

## Repo layout

```
rfp-copilot/
├── infra/                      CDK TypeScript, one stack per layer
│   ├── bin/app.ts              App entry; stage/region/corsOrigins/siteUrl context
│   └── lib/
│       ├── storage-stack.ts            S3 buckets + KMS CMK
│       ├── data-stack.ts               DynamoDB: Jobs, Questions, Reviews, CustomerRefs
│       ├── knowledge-base-stack.ts     S3 Vectors + Bedrock Knowledge Base
│       ├── auth-stack.ts               Cognito user pool + Hosted UI
│       ├── orchestration-stack.ts      Lambdas, Step Functions, 2 HTTP APIs, Guardrail
│       ├── observability-stack.ts      Dashboards + alarms
│       └── static-site-stack.ts        S3 + CloudFront + generated config.js
├── lambdas/                    Python 3.12
│   ├── shared/                 Models, Bedrock client, provenance, redaction, session
│   ├── excel_parser/           openpyxl parse + question extraction
│   ├── question_classifier/    Topic + class dispatch; gated topics force a tier
│   ├── retriever/              Bedrock KB retrieval, per-source-type dispatch
│   ├── generator/              Sonnet synthesis with citations
│   ├── confidence_scorer/      Composite H/R/C/F/G scoring
│   ├── hard_rules/             Deterministic tier floors and triggers
│   ├── divergence/             Contradictory-prior detection (built, not yet wired)
│   ├── excel_writer/           Draft and final workbooks, fills, comments, summary
│   ├── review_gate/            Parks the execution on a task token
│   ├── review_api/             Review queue, approve, reject, corpus write-back
│   ├── upload_api/             Presign, start, status, download
│   ├── ingestion/              Pluggable source connectors (config/sources.yaml)
│   ├── ingestion_trigger/      S3-event / scheduled KB ingestion kick-off
│   ├── mock_sources/           Legacy: not referenced by any stack; nothing deploys it
│   └── tests/                  pytest suite
├── ui/                         Static pages; endpoints arrive as deploy-time config.js
├── data/
│   ├── incoming/               echoworx-caiq-v4-40q.xlsx + the 5-question follow-up
│   ├── corpus-real/            Generated evidence corpus with metadata sidecars
│   └── corpus/, graph/, …      Generator inputs and legacy synthetic data
├── scripts/                    Deploy, seed, reset, recycle, verify
└── docs/                       Runbook, demo script, architecture; archive/ for superseded
```

## Quick start (local, no AWS)

Verifies the whole answer pipeline against an offline corpus — parse, classify, retrieve,
generate, score, apply rules — and prints the tier spread, the hard-rule trigger coverage and
the expired-evidence handling.

```bash
cd lambdas && pip install -e . --break-system-packages && cd ..

python3 scripts/generate_synthetic_data.py
python3 scripts/generate_corpus_documents.py

PYTHONPATH=. python3 scripts/verify_fixture.py
```

Expect `RESULT: PASS`, and a tier spread of **12 green / 22 amber / 6 red** across the 40
CAIQ questions.

Then the unit suite:

```bash
python3 -m pytest lambdas/tests -q
```

And, for a walk through all seven workflow steps against the real Lambda entry points with
DynamoDB and S3 stubbed by moto:

```bash
python3 scripts/e2e_local.py
```

`e2e_local.py` and `verify_fixture.py` are deliberately independent: they build their retrieval
stubs differently (`verify_fixture.py` applies a 0.045 cosine cutoff, `e2e_local.py` applies
none) and reach the same tier spread. That agreement is the strongest offline evidence in the
repo. `e2e_local.py` is a QA harness and reports individual check failures by design — read
its summary rather than only its exit code.

## Deploying to AWS

Follow **`docs/DEPLOY-RUNBOOK.md`**. The short version, and it is short only if nothing goes
wrong:

```bash
aws sso login && export AWS_REGION=us-east-1
cd infra && npm ci
npx cdk bootstrap "aws://$(aws sts get-caller-identity --query Account --output text)/us-east-1"

# Pass 1 — everything. CloudFront domain does not exist until this finishes.
npx cdk deploy --all --require-approval never -c stage=poc -c region=us-east-1

# Pass 2 — auth stack only, now that the site origin is known.
npx cdk deploy rfp-copilot-poc-auth -c stage=poc -c siteUrl=https://<CloudFrontDomain>

cd .. && python3 scripts/seed_customer_refs.py && python3 scripts/seed_s3_corpus.py
```

The two-pass deploy is structural, not optional: Cognito needs the CloudFront domain in its
callback list, and CloudFront needs the Cognito identifiers in its generated `config.js`.
Skipping pass 2 produces `redirect_mismatch` at the Hosted UI. Docker must be running — Lambda
bundling shells out to `docker build`. Bedrock model access must be enabled in the account
before the pipeline will produce answers; the runbook says how to check and how to grant it.

Or one command, which does all of the above including both passes and the first reviewer:

```bash
bash scripts/deploy-for-demo.sh you@yourdomain.example
```

Reviewer accounts are created explicitly — self sign-up is disabled on purpose, because
`/approve` writes SME text into the corpus that seeds future answers. Cognito forces a
password change at first sign-in; do that once before you demo. Runbook §7.

Other scripts:

| Script | What it does |
|---|---|
| `scripts/deploy-for-demo.sh <reviewer-email>` | One-shot: bootstrap, both deploy passes, reviewer, seed. |
| `scripts/bootstrap-reviewer.sh <pool-id> <email>` | Create a reviewer in the Cognito pool. Idempotent. |
| `scripts/reset-demo.sh` | Restore to a known state between demos. **Required** before re-running the flywheel beat — it resets the `sme-approved/` corpus prefix, which nothing else does. |
| `scripts/recycle.sh {up\|down\|recycle\|status}` | Full infra lifecycle. `down` is destructive and asks for confirmation. |
| `scripts/teardown.sh` | Lighter-weight tear-down. |
| `scripts/seed_s3_corpus.py` | Upload the corpus and run KB ingestion to completion (1–3 min). |
| `scripts/verify_fixture.py` | Offline verification of the fixture's tier spread and triggers. |

## Status

| Area | State |
|---|---|
| Infrastructure (7 CDK stacks) | Built; never deployed to real AWS |
| Ingestion + Excel parser | Done |
| Bedrock KB retrieval over S3 Vectors | Done |
| Generation with citations | Done |
| Confidence scoring + hard rules | Done, deterministic |
| Excel writer (draft + post-review final pass) | Done |
| SME review UI | Done — `ui/review.html`, behind Cognito |
| Session lifecycle + audit trail | Done — append-only `Reviews` table |
| Step 7 flywheel (approved answers back into the KB) | Done; reaches amber, not green, by design |
| Cognito auth on every route | Done |
| Divergence detector | Built and tested; no Step Functions state wires it yet |
| Distributed Map for large questionnaires | Not started. Map concurrency is 10, tops out ~30 questions. Mandatory for real CAIQ/SIG volumes. |
| AgentCore integration | Not started. See `HANDOFF.md` §2.1. |
| Evaluation harness / accuracy measurement | Not started. Highest value per day of the remaining work. |

## Design non-negotiables

1. **Pricing answers are always red.** Gated at dispatch — retrieval is skipped entirely, so
   there is no path by which a price can be generated.
2. **Compliance claims are amber minimum.** They must cite the specific artefact and its
   validity period.
3. **Reference customers are gated on an approval table**, not on the model's judgment.
4. **No LLM self-reported confidence.** Composite score and deterministic rules only.
5. **Tiering is never set by a model.** `confidence_scorer` and `hard_rules` stay deterministic.
6. **Incoming questionnaires never auto-ingest into the corpus.** Only human-approved answers
   are written back, and each carries a sidecar recording who approved it and when.
7. **Synthetic data is fully fictitious.** No real customer names, no real Echoworx documents.

## Contributing notes

- `ui/config.js` is generated at deploy time by the static-site stack. It must never be
  committed; pages carry no endpoint literals.
- `infra/outputs.json` and `infra/cdk.context.json` hold live account identifiers and are
  gitignored. Do not commit or port them.
- Superseded documents are archived under `docs/archive/` with a header saying what replaced
  them and why, rather than deleted.
