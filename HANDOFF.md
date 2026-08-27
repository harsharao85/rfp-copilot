# HANDOFF — Echoworx security questionnaire POC

**From:** design + build session, 26 Aug 2026
**To:** Cowork / Claude Code session continuing this work
**Repo:** `rfp-copilot` (proposed rename: `questionnaire-copilot`)
**Baseline commit:** `5a90587`

Read this before writing code. It records decisions that were made
deliberately and for reasons that are not obvious from the code, so that they
do not get quietly reversed.

---

## 1. What this project now is

The repo was built as a sales-side RFP redlining copilot: ingest an Excel of
prospect questions, auto-answer with citations, score confidence, return a
colour-coded workbook for SME review.

It is being repurposed to answer **inbound security questionnaires** for
Echoworx — a cloud email encryption vendor who receives CAIQ / SIG / VSA-style
assessments from their own prospects and customers, and burns SME time
answering the same questions repeatedly.

The shape is the same. Echoworx is the vendor being assessed rather than the
vendor pitching, but the pipeline (parse → per-question retrieve/generate →
score → human gate → write) is unchanged.

**The existing IP that matters and should survive intact:** `confidence_scorer`
(composite H/R/C/F/G model) and `hard_rules` (deterministic tier enforcement).
These are the reason the system is defensible to a compliance buyer. Do not
refactor them casually.

---

## 2. Decisions locked — do not relitigate without asking

### 2.1 Step Functions stays the orchestrator. AgentCore goes inside it.

The instinct to replace Step Functions with an agent loop was considered and
rejected. The pipeline is deterministic, bounded and auditable, and the buyer
*is* a compliance function — an agent that picks its own path is much harder to
defend to the people who will audit its output.

AgentCore is used selectively, via the **native Step Functions ↔ AgentCore
harness integration** (GA June 2026), which makes this a supported pattern
rather than a workaround:

- **Gateway** — turns Echoworx's scattered sources into MCP tools behind one
  auth surface, and moves Guardrails enforcement to the gateway layer, outside
  agent code, where the model cannot reason around it. This is the strongest
  security-buyer talking point in the whole design.
- **Harness inside the Map state** — only for classifier-flagged multi-hop
  questions. Simple lookups stay on the existing single-shot path.
- **Evaluations** — ground-truth accuracy measurement. Highest POC value per
  day spent (see §5).
- **Identity** — phase 2, for on-behalf-of access to permissioned sources.

Explicitly **not** used: Memory, Code Interpreter, payments, Agent Registry,
Web Search. Browser tool is deferred but interesting if Echoworx receives
questionnaires through portals (OneTrust / Whistic / Panorays) rather than as
files — worth asking, not worth promising.

**Tiering must never be set by an LLM.** `confidence_scorer` and `hard_rules`
stay deterministic Lambdas. This is a founding non-negotiable of the original
design and it matters more here than it did originally.

### 2.2 Region: `us-east-1`. Residency is not a POC constraint.

Earlier analysis pushed for `ca-central-1` on data-sovereignty grounds. **The
human has explicitly set this aside for the POC.** Assume `us-east-1`, with
cross-region inference if invoked from Canada.

Do not re-raise residency unless asked. It is documented in
`docs/echoworx-poc-repurposing-plan.md` §2 for when it becomes relevant to a
production conversation.

### 2.3 Customer names are ingested in the clear, as metadata.

This reversed an earlier redaction-first design, for a good functional reason:
**a first-pass questionnaire answer must cite which customer a prior answer
was given to**, so an SME can spot anomalies retrospectively.

Two things follow, and both matter:

**Metadata, not body text.** Sidecar attributes are stored alongside the vector
and returned with the passage, but are *not* embedded. So the customer name is
citable and filterable without influencing similarity. If the name is in the
embedded chunk body, a query mentioning that customer ranks their answer higher
for the wrong reason. Keep attribution in the sidecar.

**Company name ≠ individual PII.** The `citation_safe` profile
(`shared/redaction.py`) lets customer *company* names through but still strips
emails, phone numbers, named individuals and credentials. Citing "what we told
Acme Financial in March" does not require a contact's mobile number, and a
corpus accumulating named individuals picks up a retention obligation nobody
agreed to. Widen or narrow via the profile constant if the human decides
otherwise.

### 2.4 Vector store: S3 Vectors, Titan Embed v2. Unchanged.

Explicitly retained by the human. Do not propose OpenSearch Serverless.

---

## 3. Constraints discovered the hard way

**Bedrock KB custom transformation is POST_CHUNKING only.**
`customTransformationConfiguration` supports exactly one `stepToApply`, and the
service writes parsed documents to the intermediate S3 bucket *before*
invoking the Lambda. There is no pre-chunking hook. Consequence: any
transformation that must happen before Bedrock sees raw text has to run
upstream of the KB entirely, in a separate gate. This is why
`ingestion/connectors.py` has a `gate` vs `native` ingest path distinction, and
why the native SharePoint/Confluence KB connectors cannot honour a pre-ingest
gate.

**S3 Vectors caps filterable metadata at 2048 bytes per vector.** The KB stack
already marks `AMAZON_BEDROCK_TEXT` and `AMAZON_BEDROCK_METADATA` as
non-filterable to keep them out of that budget. Any new sidecar key must stay
small and flat — nested structures are not filterable. There is a test
asserting the provenance sidecar stays under budget; keep it passing.

**Sidecar dates are ISO-8601 strings on purpose.** KB metadata filters compare
lexically, and ISO-8601 sorts correctly as a string. That is what makes
`answered_at > '2026-01-01'` work without numeric coercion. Do not "tidy" them
into epoch integers.

**Partial redaction is worse than no redaction.** A phone regex with a
trailing `(?![\w.])` lookahead backtracked on sentence-final periods and
emitted `[PHONE_2526] 0142`, leaking the last four digits while looking
handled. Fixed, with parametrised regression tests. The lesson generalises: the
`Redactor.verify()` second pass belongs in the pipeline permanently, not just
in tests — it is also the artefact you show the customer as evidence.

---

## 4. What is built and tested

**113 tests passing** (28 divergence, 31 redaction, 54 pre-existing untouched).

```
lambdas/shared/redaction.py       Deterministic pseudonymisation engine.
                                  HMAC tokens, legal-suffix normalisation,
                                  overlap resolution, fail-closed quarantine.
                                  Default profile for POC: citation_safe.

lambdas/shared/provenance.py      Provenance + Citation models. Sidecar
                                  round-trip. render_citation() produces the
                                  reviewer-facing source line.

lambdas/ingestion/connectors.py   Pluggable source framework. Golden dataset
                                  and S3-prefix connectors live. Confluence,
                                  SharePoint, trust portal are registered
                                  stubs that raise NotImplementedError listing
                                  the decisions each needs.

lambdas/divergence/detector.py    Retrospective anomaly detection. The reason
                                  customer attribution exists. See §4.1.

config/sources.yaml               The entire surface for adding a repository.
                                  Adding a source = a config block, never new
                                  infrastructure.

lambdas/tests/test_redaction.py
lambdas/tests/test_divergence.py
```

### 4.1 The divergence detector — read this before touching it

This is the sharpest thing in the package and the easiest to accidentally
flatten.

"Inconsistent prior answers" is not one problem. It is four, needing different
responses, and collapsing them into a single alert is how reviewers learn to
ignore alerts:

| Situation | Kind | Tier floor |
|---|---|---|
| Two customers, contradictory, same period | `CUSTOMER_DIVERGENCE` | **RED** — no benign explanation |
| Same customer, contradictory, any gap | `SELF_CONTRADICTION` | **RED** — they hold both documents |
| Two customers, contradictory, years apart | `UNRESOLVED_DIVERGENCE` | AMBER — probably the control improved |
| All agree, all expired | `STALE_CONSENSUS` | AMBER — reads clean on every other signal |

**The model is asked exactly one question — do these two statements
contradict? — and nothing else.** Severity comes from the deterministic
`classify()` matrix. Do not let the model assign kinds or tiers; that breaks
§2.1 and makes the behaviour unexplainable to an auditor.

Other details that are load-bearing:
- Grouping keys on `customer_id`, not display name, so an acquisition-renamed
  customer does not read as two parties.
- A comparator exception degrades to `UNCERTAIN` → AMBER, never to clean. A
  throttled Bedrock call must not read as agreement.
- Pairwise comparison is O(n²) in model calls. 40 priors is 780 Haiku calls for
  one cell. Capped at 60 by default, most-recent-first.

---

## 5. Next steps, in priority order

### Immediate wiring — DONE (27 Aug 2026, see git log)
1. ~~`retriever/handler.py` — hydrate `RetrievedPassage.metadata` into
   `Provenance.from_sidecar()`.~~ The whole sidecar now flows through, and
   `RetrievedPassage.provenance` carries the typed value. Hydration is
   fail-soft: a malformed sidecar loses the citation, never the passage.
2. ~~`hard_rules/rules.py` — consume `DivergenceReport.tier_floor` as a sixth
   rule.~~ `apply_rules(divergence_tier_floor=, divergence_kinds=)`.
   `RULES_VERSION` bumped to `2026-08-27` — **this is a policy change and
   needs General Counsel sign-off per CLAUDE.md §6.**
3. ~~`excel_writer/writer.py` — render citations and findings into the
   reviewer comment.~~ Divergence leads the comment, then confidence, then
   triggers, then rendered source lines.
4. ~~Implement the real comparator.~~ `divergence/comparator.py` —
   `BedrockComparator`, Haiku, temperature 0, four-word closed vocabulary,
   symmetric cache. Every failure path returns UNCERTAIN.

Carried by these four but **not yet done**:
- The divergence detector has no Step Functions state. `hard_rules/handler.py`
  reads `event["divergence"]` defensively and does nothing when it is absent,
  so the floor is enforced the moment something populates it. Wiring that state
  means deciding where the prior statements come from — most likely a KB query
  filtered on `customer_id` — and belongs with the Phase 1 Distributed Map work
  rather than ahead of it.
- `CLAUDE.md` still describes the sales-side RFP tool at v0.5 and says AgentCore
  is out of scope (§"What's explicitly out of scope"). That directly contradicts
  §1 and §2.1 of this document. Reconcile it before the next session, or the
  next session will believe it.

### Phase 0 — de-personalise (1–2 days, no design risk)
The repo is hardcoded to a personal demo account. All of this must go before
anything Echoworx-shaped is deployed:
- `infra/bin/app.ts` — region default and `rfp-copilot.meringue-app.com`
- `infra/lib/storage-stack.ts:58` — CORS origin
- `infra/cdk.context.json`, `infra/outputs.json` — contain account
  `658203403363` and live ARNs. **Delete and gitignore. Do not port.**
- `lambdas/shared/bedrock_client.py:23-29` — model profiles and region
- `scripts/ingest_corpus.py:25-26`, `recycle.sh`, `deploy-for-demo.sh`
- **Add Cognito.** The UI is currently public to anyone with the link. Fine for
  a personal demo, not for a bucket holding a security vendor's control
  documentation.

### Phase 1 — scale the spine (3–4 days)
Map state is capped at concurrency 10 and tops out around 30 questions. Real
questionnaires are hundreds (CAIQ) to low thousands (SIG). The Distributed Map
upgrade already flagged TODO in the README is now mandatory, not optional.
Also rethink the review gate: one `waitForTaskToken` per question across
hundreds of amber items is an unusable SME experience — batch by topic.

### Phase 2 — AgentCore integration (1–1.5 weeks)
Per §2.1. CDK note: `aws-bedrockagentcore` L2 constructs graduated to stable in
`aws-cdk-lib` (May 2026), so no `-alpha` package needed. Policy submodule is
still alpha.

### Phase 3 — evaluation harness (3–5 days, highest value per day)
The POC will be judged on "how accurate is it and how do you know?" Ingest
Echoworx's historically-answered questionnaires, hold back a split, wire
AgentCore Evaluations with ground truth, report accuracy by question category.

**Critical:** `config/sources.yaml` has `golden-questionnaire-holdout` set to
`enabled: false` deliberately. If the holdout gets embedded, the accuracy
number measures memorisation rather than retrieval. Split before first ingest.

### Also outstanding
- CDK ingestion stack (raw/sanitised buckets, quarantine prefix, entity-map
  table). Deferred because bucket naming and stage conventions should match
  VeUP patterns rather than invented ones — **ask the human**.
- Rewrite the v0.4 design non-negotiables for security questionnaires. Straw
  man is in `docs/echoworx-poc-repurposing-plan.md` §5. These need agreeing
  **with Randy at Echoworx**, not assumed.

---

## 6. Open questions for the human

1. **Whose AWS account?** VeUP's or an Echoworx-owned account? A real POC needs
   their actual control documentation, which is a legal conversation before it
   is a technical one. Connects to the dedicated-account / PrivateLink thread
   already in flight on the network egress workstream.
2. **Corpus scope.** How many historically-answered questionnaires can Echoworx
   supply? Under ~50 answered questions, the Phase 3 accuracy figure is not
   statistically meaningful, which removes the strongest thing the POC produces.
3. **VeUP bucket naming / stage conventions**, to unblock the CDK ingestion
   stack.
4. **File vs portal split** on inbound questionnaires — changes the phase 2
   roadmap materially, costs nothing to ask.

---

## 7. Running it

```bash
cd lambdas
pip install -e . --break-system-packages
python -m pytest tests/ -q          # expect 113 passed
```

New packages `lambdas/ingestion/` and `lambdas/divergence/` need `__init__.py`
(included in this package).

Dependencies used by the new modules: `pydantic` (already a repo dep), stdlib
only otherwise. `boto3` import in `connectors.py` is deferred inside the method
so local tests do not require AWS.

---

## 8. Reference

Full architecture reasoning, AgentCore component analysis, cost shape and AWS
source links: `docs/echoworx-poc-repurposing-plan.md`.

AWS facts in that document were verified 26 Aug 2026. AgentCore ships changes
most months — re-verify anything going in front of a customer.
