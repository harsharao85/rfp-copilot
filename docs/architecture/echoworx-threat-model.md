# Threat Model — Echoworx Inbound Security-Questionnaire Copilot (ENG-2, Bolt 2)

**Author:** security-architect · **Bolt:** 2 · **Date:** 2026-08-27
**Target of evaluation:** `harsharao85/rfp-copilot` @ branch `echoworx-wiring` (HEAD `739133b`), as it would deploy today, plus the AgentCore additions locked in `HANDOFF.md` §2.1.
**Region / account:** `us-east-1`, VeUP ephemeral PoC-OU account, TTL 2026-10-11.
**Method:** asset + data-flow decomposition, trust boundaries enumerated, STRIDE per boundary, each threat carried to *what an attacker gets* and *what actually stops them in the code as written*. Every claim is anchored to a file and line in the tree read on 27 Aug 2026. Where a control is asserted in a docstring but absent from the executing path, that is stated.

---

## 0. Executive position

The pipeline's defensible design ideas are real: a deterministic Step Functions spine, a composite confidence model, a hard-rule engine that can only demote, and a divergence detector with a closed model vocabulary. Those are the reasons this is worth building.

What is built today does not yet realise them. Five things dominate the risk:

1. **Both HTTP APIs are unauthenticated.** `POST /reviews/{jobId}/approve` is reachable by anyone with the URL. It resumes the Step Functions execution *and* writes caller-supplied text into the authoritative corpus that future answers are generated from. The human review gate is not an integrity control; it is a public endpoint.
2. **The redaction engine is dead code.** `lambdas/shared/redaction.py` is imported by nothing outside `lambdas/tests/`. The KB sidecar hardcodes `"redacted": True` regardless.
3. **No guardrail is ever applied to model input.** `shared/bedrock_client.py` explicitly ignores its own `apply_guardrail` parameter; only a post-hoc OUTPUT check exists, and it fails open. The `PROMPT_ATTACK` filter configured in CDK is never evaluated.
4. **Retrieval is not scoped by customer.** A questionnaire from Customer B retrieves prior answers given to Customer A and renders "Prior answer — <Customer A>, …, approved by <name>" into the workbook returned to B.
5. **SC4 as specified is achievable, but not by the design as written.** The AgentCore Gateway would cover only the classifier-flagged multi-hop minority path; the single-shot majority path calls `bedrock:InvokeModel` on `Resource: "*"` directly and routes around any gateway. See §6.

No credential, private key or API secret is committed to the tree or to any object in git history. The only `AKIA` string in the repository is `AKIAIOSFODNN7EXAMPLE`, AWS's published documentation example, in `lambdas/tests/test_redaction.py`. That is a positive finding and worth stating to the buyer.

---

## 1. Assets and data classification

| Asset | Where | Classification | Why an attacker wants it |
|---|---|---|---|
| Echoworx control documentation (SOC 2 / ISO 27001 evidence, DR, key management, SSDLC) | `referenceCorpusBucket`, S3 Vectors index | Confidential — a security vendor's own control posture | A map of an email-encryption vendor's controls, including known weaknesses phrased for auditors |
| Prior customer-attributed answers + `customer_name`, `approved_by`, `questionnaire_name` | Corpus objects + KB sidecar metadata | Confidential + third-party attributable | Reveals who assesses Echoworx, what they asked, and what they were told |
| Inbound questionnaires (untrusted) | `incomingBucket` | Untrusted input, customer-confidential | Injection vehicle; also identifies the prospect |
| Generated workbook + reviewer comments | `outputBucket`, presigned GET | Confidential draft assertions | Pre-review, unapproved compliance claims attributable to Echoworx |
| SME approval decision + task token | `jobsTable.task_token` | Integrity-critical | Whoever holds it can complete the human gate |
| Bedrock model access | Lambda execution roles | Cost + abuse | `InvokeModel` on `Resource: "*"` is an open model-inference primitive |

---

## 2. Trust boundaries

```
                        INTERNET (unauthenticated)
   ┌──────────────────────────┼──────────────────────────────┐
   │        TB1               │            TB2               │
   │  CloudFront/static site  │  HTTP APIs (upload + review) │
   └──────────┬───────────────┴───────────────┬──────────────┘
              │ upload.html / review.html     │ presign, start, status,
              │ (no auth, no WAF)             │ download, reviews, approve, reject
              ▼                               ▼
        ┌───────────────┐            ┌──────────────────┐
        │ incomingBucket│───TB3────▶ │ Step Functions   │
        │ (untrusted    │  parse     │ Standard         │
        │  workbook)    │            └────────┬─────────┘
        └───────────────┘                     │ per-question Map (conc. 10)
                                              ▼
                       TB4  ┌──────────────────────────────────┐
              model boundary│ classify → retrieve → generate   │
              (no input     │ → score → hard_rules             │
               guardrail)   └───────┬──────────────────────────┘
                                    │ bedrock:InvokeModel  Resource:"*"
                                    ▼
                            ┌───────────────────┐
                    TB5     │ Bedrock KB /      │◀── TB6 corpus write
                 retrieval  │ S3 Vectors        │    (unauth /approve →
                  boundary  └───────────────────┘     sme-approved/ prefix)
                                    │
                                    ▼ TB7 output boundary
                            outputBucket → presigned GET → customer SME
                                    │
                                    ▼ TB8 account boundary
                            shared ephemeral PoC account, TTL 2026-10-11
```

Planned but not built: **TB9 — AgentCore Gateway**, between the Map state's harness path and Echoworx's permissioned sources. Assessed in §6.

---

## 3. STRIDE per boundary

### TB1 — Public internet → static review/upload UI

Data crossing: nothing on the way in; on the way out, the UI itself and (via TB2) every pending job and its draft answers.

| STRIDE | Threat | What the attacker gets | What stops them today |
|---|---|---|---|
| S | Anyone loads `/review.html` and acts as the SME | Full reviewer capability | **Nothing.** `static-site-stack.ts:35-48` — CloudFront distribution, no auth, no WAF, no signed URLs, no Cognito. `errorResponses` maps 403/404 → 200 `/upload.html`, so probing returns 200. |
| T | Reflected XSS via `?job=` | Script execution in a real reviewer's browser | **Nothing.** `ui/review.html:121-122` reads `params.get('job')` and interpolates it unescaped at `ui/review.html:196` (`<h2>${jobId}</h2>`) and into `onclick="doReject('${jobId}')"`. |
| T | Stored XSS via hard-rule triggers | Same, no user interaction beyond opening a job | **Nothing.** `ui/review.html:187` renders `${t}` for each `hard_rule_triggers` entry with no escaping. Triggers include `f"unapproved_reference:{customer}"` (`lambdas/hard_rules/rules.py:129`) where `customer` comes from the model's `invoked_customers` — attacker-steerable from a questionnaire cell. Adjacent fields *are* escaped (`escHtml` at 181/183/184/304/328-331), so this is an omission, not a design choice. |
| I | Enumerate the site | API endpoints of the personal demo account | `ui/review.html:116-117` and `ui/upload.html:92-93` hardcode live API Gateway URLs (`u6i419jl59…`, `9fdxjyjafl…`). Note these differ from `infra/outputs.json:33-34` (`rusa84gwa4…`, `xt2cs4vj4c…`) — more than one deploy has happened and the checked-in outputs are stale. **HANDOFF §5 Phase 0 does not list `ui/*.html`; the de-personalisation checklist is incomplete.** |

### TB2 — Public internet → HTTP APIs

Data crossing inbound: filenames, job ids, arbitrary `answers[]` text, `approved_by`, rejection reasons. Outbound: presigned S3 PUT and GET URLs, every pending job, every reviewable question with draft answers, citations and provenance.

Neither API has an authorizer. `infra/lib/orchestration-stack.ts:401-412` (review) and `:444-455` (upload) call `addRoutes` with no `authorizer:` property, and both set `allowOrigins: ['*']` (`:406`, `:449`).

| STRIDE | Threat | What the attacker gets | Stopped by |
|---|---|---|---|
| **E of P / T** | `POST /reviews/{jobId}/approve` | Completes the SME gate for any pending job and resumes the execution | **Nothing.** `lambdas/review_api/handler.py:198-241`. `job_id` need not be guessed — `GET /reviews` (`:54-66`) lists every pending job. |
| **T (corpus write)** | The same call carries `answers[{questionId, answer_text}]`, written verbatim to `s3://referenceCorpus/sme-approved/<doc_id>.md` with sidecar `source_type: sme_approved_answer` | **Permanent, authoritative poisoning of the answer corpus by an anonymous caller.** The PutObject event fires `ingestion_trigger` (`orchestration-stack.ts:362-380`) and the text is embedded and retrievable within minutes. | **Nothing.** `review_api/handler.py:130-195`, esp. `:188-192`. |
| S | `approved_by` is caller-supplied | Forged SME identity in the audit trail *and* in the sidecar, which `render_citation` prints into the workbook as "approved by X" | **Nothing.** `review_api/handler.py:235`, `:181`; rendered at `shared/provenance.py:188` via `excel_writer/writer.py:87`. |
| I | `GET /reviews/{jobId}` | Every draft answer, citation, provenance and confidence breakdown for a job — i.e. Echoworx's unreviewed control assertions | **Nothing.** `review_api/handler.py:69-112`. |
| I | `GET /upload/{jobId}/download` | A 15-minute presigned GET on the finished workbook | **Nothing.** `upload_api/handler.py:158-175`. |
| D / cost | `POST /upload/presign` → `POST /upload/{jobId}/start` | Unlimited Step Functions executions and Bedrock spend against a USD 1,500 ceiling | **Nothing.** No throttling, no usage plan, no per-caller limit. `upload_api/handler.py:73-127`. |
| E of P | `reviewApiFn` holds `states:SendTaskSuccess`/`SendTaskFailure` on `Resource: ['*']` | Resume/fail *any* state machine execution in the account, not just this one | `orchestration-stack.ts:339-342`. Combined with the unauthenticated route this is an internet-reachable primitive over every Step Functions execution in a shared PoC account. |
| R | No caller identity is recorded anywhere | Approvals are unattributable | `logger.info("review_api.approved", job_id=…, edits=…)` (`:230`) records no principal, no source IP, no request id. |

### TB3 — Untrusted workbook → parser → state

Data crossing: attacker-authored cell text, sheet names, cell coordinates, from a file the customer sent Echoworx.

- **A questionnaire is an untrusted document.** Cell text is parsed (`lambdas/excel_parser/parser.py`) and flows unmodified into two model prompts: the classifier at `lambdas/question_classifier/handler.py:110-116` (`user_prompt=f"Q: {text}\nA:"`) and the generator at `lambdas/generator/handler.py:70` (`lines.append(f"QUESTION: {question}")`). There is no delimiting, no escaping, no instruction-hierarchy marker, and — see TB4 — no input guardrail.
- **Injection payoffs are concrete, not theoretical:**
  - Steer `invoked_customers` (`generator/handler.py:118`) → controls the Rule-3 trigger string → stored XSS at TB1, and/or *omit* a customer the answer actually names so Rule 3 (`hard_rules/rules.py:124-129`) never fires.
  - Steer `citations` (`generator/handler.py:117`) → directly controls the C component of the composite score (`confidence_scorer/scorer.py:75-84` matches `p.document_id in generated_citations`). Two ids from two source systems ⇒ `C = 1.0`. **This is model output on the path into the confidence scorer**, which SC3 asserts does not exist.
  - Emit an answer beginning with `=` → written by openpyxl as a live formula, not a string (`excel_writer/writer.py:123` `answer_cell.value = ans.answer_text`), in the workbook returned to the assessing customer's reviewer.
- **Parser DoS:** `parser.py` walks every non-empty cell on every sheet; a zip-bomb or multi-million-cell workbook hits a 60 s timeout at worst. Bounded — the Map is capped at concurrency 10 and the parser runs once. Low.

### TB4 — Lambda → Bedrock (the model boundary)

Data crossing: system prompt + untrusted question + retrieved corpus passages, out: free text.

| STRIDE | Threat | What stops them |
|---|---|---|
| T | Prompt injection reaching the model | **Nothing on the input side.** `shared/bedrock_client.py:64-82`: `invoke()` documents that `apply_guardrail` "is kept for signature compatibility but ignored"; `extra` is always `{}`, so no `guardrailIdentifier` is ever sent on `invoke_model`. Consequently the `PROMPT_ATTACK` content filter declared at `orchestration-stack.ts:114` is configured and never evaluated. `guardrail_applied: bool(extra)` (`bedrock_client.py:74`) is therefore always `False` — the audit field is accurate, but only because the control is absent. |
| T | Guardrail bypass on output | `check_output()` (`bedrock_client.py:41-61`) is OUTPUT-only and **fails open twice**: `if not guardrail_id: return {"blocked": False}` (`:45-47`) and `except ClientError: return {"blocked": False}` (`:60-61`). A throttle on `ApplyGuardrail` silently disables the only guardrail in the system. |
| R | Which policy version was in force | `GUARDRAIL_VERSION: 'DRAFT'` (`orchestration-stack.ts:133`). DRAFT is mutable; an audit record stamped "DRAFT" identifies no fixed policy. |
| — | Guardrail intervention is silently discarded | `generator/handler.py:113-116` takes `guardrail_result["text"]` but drops `guardrail_result["blocked"]`. Nothing in the state machine ever populates `guardrail_flags`, which `confidence_scorer/handler.py:22` reads with a default of `[]`, so `score_g` (`scorer.py:115-117`) returns **1.0 unconditionally, forever** — including on answers the guardrail actually blocked. A blocked answer for an `auth_*` topic with any primary passage is scored GREEN (`scorer.py:146-151`), `reviewer_required=False`, and the blocked-message placeholder is written into the customer's workbook as the answer. |
| E of P | Model-access scope | `bedrock:InvokeModel` on `Resource: ['*']` for retriever, generator, classifier and scorer (`orchestration-stack.ts:218-223`, comment concedes "narrow to specific foundation-model ARNs in prod"). Any of those roles can invoke any model in the account. This is also what defeats a gateway chokepoint — see §6. |

### TB5 — Knowledge Base retrieval boundary (and KB metadata as an exfiltration channel)

Data crossing: a query string; back, chunk text plus **the entire sidecar**.

- **No tenant/customer scoping on retrieval.** The only filter applied is `{"equals": {"key": "source_type", …}}` (`retriever/handler.py:143-152`). `customer_id` and `audience` exist in the sidecar (`shared/provenance.py:94-120`) and are never used as a filter.
- `prior_rfps` is a **secondary source for every non-gated class** — `auth_compliance`, `auth_product` and `unclassified` alike (`question_classifier/dispatch.py:49-73`). So on essentially every question, prior customer-attributed answers are retrieved.
- The whole sidecar is carried through verbatim by design (`retriever/handler.py:187-203`, "carry the whole sidecar through, not a hand-picked three keys"), hydrated into `Provenance` (`:206-230`), and rendered by `render_citation` as *"Prior answer — Acme Financial Corp, Vendor Security Assessment, 11 Mar 2026, approved by R. Sandhu"* (`shared/provenance.py:161-198`) into the Excel cell comment (`excel_writer/writer.py:87`) of the workbook **returned to a different customer**.
- **Net:** answering Customer B's questionnaire can disclose that Customer A assessed Echoworx, on what, when, and what they were told. This is the direct consequence of HANDOFF §2.3 (names in the clear) with no compensating retrieval-time or render-time gate. The intended compensation — SME review — does not apply: `auth_*` topics with a primary passage are GREEN and `reviewer_required=False`, and their citations still render.
- **Metadata as an inbound channel, not just outbound.** `SourceDocument.sidecar()` merges `extra_metadata` into the attribute set with no allowlist (`ingestion/connectors.py:86-99`), and `_sidecar_metadata` accepts every scalar/str-list key the KB returns. Anything an attacker can write into a `.metadata.json` (which TB6 gives them, indirectly) is carried into `Provenance` and printed into the reviewer's comment box.
- **`"redacted": True` is hardcoded** at `ingestion/connectors.py:96` and asserted for every document regardless of whether any redaction ran. An auditor filtering on `redacted=true` gets the whole corpus. This is a false attestation embedded in the evidence surface.

### TB6 — Corpus write boundary (ingest / flywheel)

Two writers reach the authoritative corpus. Neither redacts.

- **`review_api` flywheel** (`handler.py:130-195`) — unauthenticated, covered at TB2.
- **`scripts/ingest_corpus.py`** — operator-run, no redaction call.
- **The redaction gate does not exist in the deployed system.** `grep` across the whole tree: `shared/redaction.py` is referenced only by `lambdas/tests/test_redaction.py`, its own docstrings, and prose in `config/sources.yaml`. No Lambda imports it; no CDK stack deploys it; there is no ingestion Lambda that reads `config/sources.yaml`; `lambdas/ingestion/` is not packaged as a function anywhere in `orchestration-stack.ts`. There is also no `quarantine/` prefix, no entity-map table and no `REDACTION_HMAC_SECRET_ARN` in any stack (HANDOFF §5 "Also outstanding" concedes the ingestion stack is deferred).
- **Where the gate would run, it is misconfigured against the locked decision.** `config/sources.yaml` sets `redaction_profile: strict` on every enabled source (`:29`, `:44`, `:54`, `:68`) and `Redactor.redact()` defaults to `profile=STRICT` (`shared/redaction.py:405`). `CITATION_SAFE` — the profile HANDOFF §2.3 locks as the POC default, and the only one that preserves customer names for citation — is constructed at `shared/redaction.py:233-242` and selected by nothing. As configured, the gate would pseudonymise the very customer names the citation design depends on.
- **`CITATION_SAFE` cannot deliver its own claim.** It lists `EntityClass.PERSON` in `classes` but sets `use_ml_detection=False` (`shared/redaction.py:235-241`). There is no `PERSON` entry in `_PATTERNS` (`:122-153`), and the dictionary pass only matches `known_entities`, which is sourced from the *CustomerRefs* table (companies, not people). **Named individuals are therefore undetectable under the POC's own default profile.** No test exercises `CITATION_SAFE` or PERSON detection — `lambdas/tests/test_redaction.py` has 24 tests, none of them touching either.
- **`Redactor.verify()` cannot produce the SC5 artifact.** It is a substring search over a caller-supplied `known_surfaces` list (`shared/redaction.py:470-484`). It can prove a *known* surface did not survive. It cannot demonstrate "0 residual emails, phone numbers, named individuals or credentials" across a corpus, which is what SC5 asks for. That needs an independent detector pass over the sanitised text, not a list lookup.
- **HMAC secret falls back to a constant.** `self._secret = … or b"dev-only-insecure-secret"` (`shared/redaction.py:308-310`). If `REDACTION_HMAC_SECRET` is unset *or set to empty string*, every pseudonym token in the corpus becomes offline-derivable by anyone holding the source, defeating the "corpus alone does not permit reversal" claim in the module's own docstring.

### TB7 — Output boundary

- Presigned GET, 15 min, unauthenticated caller (TB2).
- Excel formula injection (TB3).
- Cross-customer citation disclosure (TB5).
- Provenance rendered from attacker-influenceable sidecar fields (TB5/TB6).
- Positive: `outputBucket` is CMK-encrypted, versioned, SSL-enforced, public access blocked (`storage-stack.ts:93-102`). The exposure is the presigning path, not the bucket ACL.

### TB8 — Account and blast radius

Shared VeUP ephemeral PoC-OU account, TTL 2026-10-11.

- **`states:SendTaskSuccess` on `*`** (`orchestration-stack.ts:339-342`) and **`bedrock:InvokeModel` on `*`** (`:218-223`) are the two account-wide grants. In a *shared* PoC account, an internet-reachable Lambda holding `SendTaskSuccess: *` can complete callbacks belonging to other engagements' state machines. This is the cross-tenant question in its sharpest form and it is a property of the IAM policy, not of the PoC's data.
- **`auditBucket` uses Object Lock in COMPLIANCE mode for 7 years** (`storage-stack.ts:110-111`). Compliance-mode retention cannot be shortened or deleted by anyone, including the account root. In an account with a 45-day TTL this makes clean teardown impossible: the bucket cannot be emptied, so the stack cannot delete, so the account cannot close, and objects survive the engagement.
- **Every data bucket defaults to `RemovalPolicy.RETAIN`** (no `removalPolicy` set on the four buckets in `storage-stack.ts:49-112`). At TTL the corpus, the incoming questionnaires and the outputs are orphaned rather than destroyed.
- **Step Functions logs at `LogLevel.ALL`** (`orchestration-stack.ts:330`) with no `encryptionKey` on the log group (`:322-324`). Full execution payloads — question text, retrieved corpus passages, draft answers, provenance including customer names — land in CloudWatch Logs under an AWS-managed key, readable by any principal with `logs:GetLogEvents`. The four CMKs on the buckets buy less than they appear to.
- **Recurring cost/attack surface after handover:** weekly and yearly EventBridge re-ingestion rules (`orchestration-stack.ts:385-398`) keep firing in an account nobody is watching.
- **No S3 server access logging or CloudTrail data events** on any bucket. There is an `auditBucket`, but nothing is configured to write access logs to it.
- **`MockSourcesApi`** (`orchestration-stack.ts:418-423`) is a third unauthenticated public endpoint; the retriever authenticates to it with the literal string `Bearer demo-key` (`retriever/handler.py:250-253`). Low impact (mock data), but it is a public endpoint in the demo surface and a hardcoded credential-shaped literal a reviewer will find.

---

## 4. Attack chains worth naming

**Chain A — anonymous corpus poisoning (the one that matters).**
`GET /reviews` → pick any pending job → `POST /reviews/{jobId}/approve` with crafted `answer_text` and `approved_by: "R. Sandhu, CISO"` → text written to `sme-approved/` → `ingestion_trigger` fires → embedded within minutes → subsequently retrieved as `source_type: sme_approved_answer`, the highest-authority H signal (`scorer.py:57-64`, weight 0.45) → drives future answers to *any customer's* questionnaire, with a citation line naming a forged approver. Requires: the URL. No credentials. No user interaction. Persistent, and survives the job that created it.

**Chain B — injection → tier laundering.**
Malicious cell in an inbound questionnaire → unguarded classifier and generator prompts → model emits `citations: ["compliance_doc_1","product_doc_2"]` (C = 1.0) and `invoked_customers: []` (Rule 3 silent) → composite clears 0.80 → GREEN → `reviewer_required=False` → answer ships without a human ever seeing it. The deterministic tier controls are deterministic *functions of model-supplied inputs*.

**Chain C — poisoned prior suppresses the divergence alarm.**
Chain A plants a prior answer; the divergence comparator embeds two prior answers in a prompt (`divergence/comparator.py:169-174`) using the same unguarded `invoke()`; a one-word `AGREE` is all the closed vocabulary needs (`comparator.py:88-107`) to turn a `CUSTOMER_DIVERGENCE` RED floor into silence. Currently theoretical only because the divergence detector **has no Step Functions state at all** — `hard_rules/handler.py:82` reads `event.get("divergence") or {}` and Rule 6 never fires in the deployed pipeline.

**Chain D — reviewer-browser compromise.**
Injected questionnaire cell → model emits an `invoked_customers` entry containing markup → Rule 3 builds `unapproved_reference:<markup>` → unescaped at `ui/review.html:187` → script runs in the reviewer's browser on the review origin → the same-origin fetch calls `/approve` on every pending job. Chains into A without needing the endpoint to be publicly reachable.

---

## 5. Controls that are genuinely present (do not re-derive these)

- Four separate CMKs, key rotation on, `BLOCK_ALL` public access, `enforceSSL`, versioning on all four data buckets (`storage-stack.ts:28-112`).
- KB service role scoped to the corpus bucket, its CMK, one embedding-model ARN and the specific vector index/bucket ARNs (`knowledge-base-stack.ts:64-89`) — the tightest role in the repo.
- `retrieverFn` restricted to `dynamodb:UpdateItem` on one table and `bedrock:Retrieve` on the specific KB ARN (`orchestration-stack.ts:206-217`).
- DynamoDB PITR on jobs and questions tables (`data-stack.ts:26`, `:35`).
- X-Ray active tracing on every Lambda and the state machine (`orchestration-stack.ts:159`, `:329`).
- Hard rules can only demote, never promote (`hard_rules/rules.py:78-83`) — structurally correct.
- Divergence comparator fails to `UNCERTAIN` on every error path and parses a four-word closed vocabulary (`divergence/comparator.py:88-107`, `:186`). The one model judgement is correctly bounded.
- No secrets in the tree or in git history (verified across all 213 reachable blobs in all 6 commits).

---

## 6. SC4 — is the guardrail actually enforced outside agent code?

SC4: *"An out-of-policy or prompt-injection request is refused at the AgentCore Gateway rather than by the agent, and emits a CloudWatch audit record naming the blocked policy."*

**Verified against AWS documentation on 27 Aug 2026** (the repo's notes are dated 26 Aug and predate nothing relevant, but the component names in HANDOFF §2.1 are imprecise):

- The capability is **AgentCore *Policy*** with Bedrock Guardrails, GA **17 June 2026**, available in `us-east-1`. It is not a property of Gateway on its own — you create a policy engine and attach it to the gateway in `ENFORCE` mode.
- Enforcement is genuinely **at the gateway perimeter, before the request reaches the agent**; a blocked call returns `403 Request Denied: … [Policy evaluation denied due to blockviolence-xxxxx]`, naming the deciding policy.
- Audit: with tracing enabled on the gateway, span data lands in **CloudWatch Logs group `aws/spans`** carrying `aws.agentcore.policy.authorization_decision` (ALLOW/DENY) and `aws.agentcore.policy.determining_policies` (the policy IDs that decided it), plus `AllowDecisions`/`DenyDecisions` metrics in `AWS/Bedrock-AgentCore`. **Tracing is not on by default** — it must be explicitly enabled on the gateway resource or the CloudWatch half of SC4 does not exist.

So the demo SC4 asks for is achievable, exactly as written.

**But the claim it would let VeUP make is not.** "The control is enforced outside the model and the model cannot route around it" requires that the model has no other route. In this architecture it does, twice over:

1. **Architectural.** HANDOFF §2.1 puts the harness (and therefore the Gateway) inside the Map state *only for classifier-flagged multi-hop questions* — "simple lookups stay on the existing single-shot path." That single-shot path is `classify → retrieve → generate` calling `bedrock-runtime` directly from Lambda. On a CAIQ v4 run, that is the large majority of questions. A gateway guardrail would cover the minority path. Demonstrating a refusal on the harness path and describing it as the system's guardrail would be materially misleading to a security buyer.
2. **IAM.** `orchestration-stack.ts:218-223` grants `bedrock:InvokeModel` on `Resource: ['*']` to four Lambdas. Nothing forces traffic through the gateway; nothing forces a guardrail onto a direct call. AWS publishes the fix: the **`bedrock:GuardrailIdentifier` condition key**, which applies to `InvokeModel`, `InvokeModelWithResponseStream`, `Converse` and `ConverseStream`, used as an `Allow` with `StringEquals` plus a paired explicit `Deny` on `StringNotEquals` so the guardrail cannot be dropped. Two caveats matter here: it does **not** cover `RetrieveAndGenerate` or `InvokeAgent` (which make internal un-guardrailed `InvokeModel` calls), and it should pin a **numeric guardrail version**, not `DRAFT` as currently set at `orchestration-stack.ts:133`.

**Verdict.** SC4 is achievable and worth building, but only if the design changes: the guardrail must be enforced on *every* model call, not only the harness path. The minimum honest configuration is (a) AgentCore Policy in `ENFORCE` mode with tracing enabled for the harness path, **and** (b) `bedrock:GuardrailIdentifier` IAM enforcement with a pinned numeric guardrail version on every Lambda role, so the single-shot path cannot invoke a model without the guardrail attached. With (b) in place the claim holds for the whole system and is the strongest thing this PoC can say. Without (b), SC4 relocates a prompt-level check onto one branch and calls it a perimeter.

---

## 7. Required control set for G3 (ranked by exploitability × blast radius)

1. Authenticate both HTTP APIs (Cognito user pool + JWT authorizer, or IAM auth) and put the review UI behind it. Constrain CORS to the site origin. Record the authenticated principal on every approve/reject.
2. Remove the corpus-write side effect from the request path. `/approve` must not accept free-text that lands in the KB; promote to `sme-approved/` only from server-side state, under an authenticated identity, with `approved_by` taken from the token and never from the body.
3. Scope `states:SendTaskSuccess`/`SendTaskFailure` to this state machine's ARN, and `bedrock:InvokeModel` to the specific inference-profile ARNs.
4. Enforce the guardrail on input, not just output: `guardrailIdentifier` on every `invoke_model` call, `bedrock:GuardrailIdentifier` IAM condition with a pinned numeric version, and `check_output` must fail **closed**.
5. Propagate guardrail interventions into `guardrail_flags` so `score_g` means something, and force a blocked answer to RED with `reviewer_required=True`.
6. Stop letting model output decide score inputs: compute `C` from passages the retriever actually returned and the answer actually quotes, not from a model-emitted `citations` list; derive `invoked_customers` by matching the approved-customer dictionary against `answer_text`.
7. Filter retrieval by `customer_id`/`audience`, and redact or suppress third-party `customer_name` in any citation rendered into a workbook destined for a different customer.
8. Build the ingestion gate that `config/sources.yaml` describes: an ingestion Lambda that runs `Redactor` with `CITATION_SAFE`, a real quarantine prefix, an HMAC secret from Secrets Manager with **no insecure default**, and `redacted` set from the actual result rather than hardcoded.
9. Escape every interpolation in `ui/review.html`; prefix-guard cell values in `excel_writer` so a leading `=`/`+`/`-`/`@` cannot become a formula.
10. Set `removalPolicy: DESTROY` + `autoDeleteObjects` on PoC buckets and drop COMPLIANCE-mode Object Lock (use GOVERNANCE, or nothing, for a 45-day account). Set `LogLevel.ERROR` or a CMK on the state-machine log group.
11. Delete `infra/cdk.context.json` and `infra/outputs.json`, parameterise the domain and CORS origin, and **add `ui/*.html` to the Phase 0 de-personalisation list** — it is currently missing from it.
