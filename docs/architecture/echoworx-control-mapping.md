# Control Mapping — Echoworx Questionnaire Copilot PoC (ENG-2, Bolt 2)

**Author:** security-architect · **Bolt:** 2 · **Date:** 2026-08-27
**Scope:** the PoC system as it would deploy from `rfp-copilot@echoworx-wiring` (`739133b`) into a VeUP ephemeral PoC-OU account in `us-east-1`, TTL 2026-10-11.

**How to read this.** Status is about the PoC, not about Echoworx. A PoC is allowed large gaps; what it is not allowed is to claim coverage it does not have. Echoworx sells email encryption to security-conscious enterprises and the buyer for this PoC is their compliance function — the same people who will read whatever VeUP writes here. Every "Partial" and "Absent" below is deliberate and should be shown, not smoothed.

Legend: **Present** = implemented and exercised in the deployed path · **Partial** = implemented but bypassable, unenforced, or unverified · **Absent** = not in the deployed path (may exist as unreferenced code) · **N/A (PoC)** = out of scope per NG1.

---

## 1. What obligations actually apply

Three distinct things get conflated and should not be:

1. **VeUP's obligations while running the PoC.** Handling a customer's confidential control documentation and their customers' attributed answers in a VeUP-controlled account. This is where the real gaps are.
2. **Echoworx's own SOC 2 Type II / ISO 27001 obligations**, which this system touches because it ingests the evidence *behind* those attestations and generates assertions *about* them. The PoC is not in Echoworx's audit scope, but its outputs are statements Echoworx would sign.
3. **The questionnaire frameworks themselves** — CAIQ v4 (CSA CCM v4), SIG (Shared Assessments), VSA. These are the *content* the system answers, and they are also the sharpest checklist available for "what will a reviewer of this system ask?" A CAIQ-literate reader will notice that a system built to answer CAIQ fails several CAIQ domains itself.

---

## 2. SOC 2 Trust Services Criteria (Security / Confidentiality)

| TSC | Requirement in substance | PoC status | Evidence |
|---|---|---|---|
| **CC6.1** Logical access — restrict access to protected information | **Absent** | No authorizer on either HTTP API: `infra/lib/orchestration-stack.ts:401-412`, `:444-455`. No auth on the review UI: `infra/lib/static-site-stack.ts:35-48`. Anyone with the URL can read every draft answer and approve any job. |
| **CC6.2** Registration/authorisation of users before granting access | **Absent** | No identity provider anywhere in `infra/`. HANDOFF §5 Phase 0 flags "Add Cognito" as outstanding; it is. |
| **CC6.3** Role-based access, least privilege | **Partial** | Good: KB role scoped to one bucket/CMK/model/index (`knowledge-base-stack.ts:64-89`); retriever limited to `UpdateItem` + `bedrock:Retrieve` on the KB ARN (`orchestration-stack.ts:206-217`). Bad: `states:SendTaskSuccess` on `Resource:['*']` (`:339-342`) and `bedrock:InvokeModel` on `Resource:['*']` for four functions (`:218-223`). |
| **CC6.6** Boundary protection | **Partial** | Buckets are `BLOCK_ALL` + `enforceSSL` + CMK (`storage-stack.ts:49-112`). But three public API/CDN surfaces with no WAF, no rate limit and `allowOrigins:['*']` (`orchestration-stack.ts:406`, `:449`, `:418-423`). |
| **CC6.7** Restrict transmission/movement of information | **Absent** | Cross-customer disclosure by design: retrieval filters only on `source_type` (`retriever/handler.py:143-152`); `prior_rfps` is a secondary source for every non-gated class (`question_classifier/dispatch.py:49-73`); prior answers' `customer_name` / `approved_by` are rendered into the workbook returned to a *different* customer (`shared/provenance.py:161-198` via `excel_writer/writer.py:87`). |
| **CC6.8** Prevent/detect unauthorised software or data | **Absent** | Unauthenticated `POST /reviews/{jobId}/approve` writes caller-supplied text into the authoritative corpus and triggers embedding (`review_api/handler.py:130-195`, `:198-241`; `orchestration-stack.ts:362-380`). No integrity check, no provenance verification, no approval attribution. |
| **CC7.1** Detect configuration changes / vulnerabilities | **Partial** | CI runs ruff/mypy/pytest and `cdk synth` (`.github/workflows/ci.yml`), which is more than most PoCs. No SAST, no dependency scanning, no IaC policy scanning (cdk-nag), no secret scanning. |
| **CC7.2** Monitoring for anomalies | **Partial** | X-Ray on every Lambda and the state machine (`orchestration-stack.ts:159`, `:329`); a CloudWatch dashboard exists (`observability-stack.ts`) but tracks only execution counts and DynamoDB throttles. No alarms. No security-relevant metric: no failed-auth (there is no auth), no guardrail-intervention, no quarantine-volume metric (`config/sources.yaml:126-129` describes one that does not exist). |
| **CC7.3 / CC7.4** Incident evaluation and response | **N/A (PoC)** | NG1. Worth naming explicitly rather than leaving implied. |
| **CC8.1** Change management for policy changes | **Partial** | `RULES_VERSION = "2026-08-27"` (`hard_rules/rules.py:36`) is stamped on every audit record — genuinely good practice. Undermined by `GUARDRAIL_VERSION: 'DRAFT'` (`orchestration-stack.ts:133`): DRAFT is mutable, so the guardrail half of the policy is unversioned and an audit record cannot identify which policy was in force. |
| **C1.1** Identify and maintain confidential information | **Partial** | Classification exists conceptually (`source_type`, `audience` in `shared/provenance.py:36-42`) but is not enforced at any boundary — `audience` is never read as a filter or a render gate. |
| **C1.2** Dispose of confidential information | **Absent** | All four buckets default to `RemovalPolicy.RETAIN` (`storage-stack.ts:49-112`, no removalPolicy set). `auditBucket` uses Object Lock **COMPLIANCE mode for 7 years** (`:110-111`), which cannot be shortened or deleted by anyone including the root account — in an account with a 45-day TTL, the corpus cannot be destroyed at engagement end. Output bucket has a 90-day expiry (`:100`); the corpus bucket has none. |
| **A1.x** Availability | **N/A (PoC)** | NG1 excludes SLA/HA/DR. |

---

## 3. ISO/IEC 27001:2022 Annex A

| Control | Substance | PoC status | Evidence |
|---|---|---|---|
| **A.5.15** Access control | **Absent** | Unauthenticated APIs and UI, as CC6.1. |
| **A.5.16 / A.5.17** Identity management, authentication information | **Absent** | No identities exist. `approved_by` is taken from the request body (`review_api/handler.py:235`) — the audit trail records an identity the caller chose. |
| **A.5.23** Cloud services security | **Partial** | Sound service choices (managed KB, S3 Vectors, Step Functions Standard for full execution history). Undermined by account-wide IAM grants and public endpoints. |
| **A.5.33** Protection of records | **Partial** | Step Functions execution history is a real audit record and Standard type was chosen for it. But `LogLevel.ALL` with an unencrypted log group (`orchestration-stack.ts:322-330`) puts full payloads — questions, retrieved corpus passages, draft answers, customer names — into CloudWatch under an AWS-managed key, which is a wider audience than the CMK-encrypted buckets suggest. |
| **A.5.34** Privacy and PII protection | **Absent in the deployed path** | The engine exists (`shared/redaction.py`, 484 lines, 24 tests) and is **referenced by nothing outside `lambdas/tests/`**. No ingestion Lambda, no quarantine prefix, no entity-map table, no HMAC secret in any stack. Meanwhile `ingestion/connectors.py:96` stamps `"redacted": True` on every sidecar unconditionally. |
| **A.8.9** Configuration management | **Partial** | All infrastructure is CDK. But account `658203403363` and live ARNs are committed (`infra/cdk.context.json:2`, `infra/outputs.json:4-36`), a personal domain is hardcoded (`infra/bin/app.ts:59`), a CORS origin points at it (`storage-stack.ts:58`), model IDs and region defaults are hardcoded (`shared/bedrock_client.py:24-25`, `:29`), and **live API Gateway endpoints are baked into the shipped UI** (`ui/review.html:116-117`, `ui/upload.html:92-93`) — the last of which HANDOFF §5 Phase 0 does not list. |
| **A.8.12** Data leakage prevention | **Absent** | See CC6.7. Additionally, KB sidecar keys are carried through wholesale into rendered citations with no allowlist (`retriever/handler.py:187-203`; `ingestion/connectors.py:86-99`). |
| **A.8.16** Monitoring activities | **Partial** | As CC7.2. No S3 server access logging and no CloudTrail data events on any bucket, despite an `auditBucket` existing for exactly this. |
| **A.8.24** Use of cryptography | **Present** | Four separate CMKs with rotation, SSL enforced, versioning on all data buckets (`storage-stack.ts:28-112`). The strongest control area in the repo. One caveat: the redaction HMAC falls back to the literal `b"dev-only-insecure-secret"` (`shared/redaction.py:308-310`), which would make every pseudonym offline-derivable if the gate were ever switched on without the secret set. |
| **A.8.25 / A.8.28** Secure development, secure coding | **Partial** | Lint + type-check + 152-test suite in CI. Countered by: unescaped HTML interpolation in the review UI (`ui/review.html:187`, `:196`), untrusted text concatenated directly into model prompts (`generator/handler.py:70`, `question_classifier/handler.py:113`), and model output written to Excel without formula-prefix guarding (`excel_writer/writer.py:123`). |
| **A.8.31** Separation of environments | **Partial** | `stage` context exists (`infra/bin/app.ts:19`) but everything defaults to `dev` and the committed outputs are from a personal account. |

---

## 4. What the questionnaires themselves imply (CAIQ v4 / CCM v4)

The system is built to answer these domains. A reviewer will test it against them.

| CCM domain | What Echoworx will be asked | What this PoC would answer for itself |
|---|---|---|
| **IAM** (Identity & Access Management) | "Do all interfaces enforce authentication?" | **No.** Two public APIs and a public UI. This is the single most quotable gap, because it is a CAIQ question the system fails while answering CAIQ questions. |
| **DSP** (Data Security & Privacy) | "Is customer data segregated between tenants?" | **No.** Retrieval is not scoped by `customer_id`; one customer's questionnaire can surface another customer's attributed prior answer, by name, in the returned workbook. |
| **DSP** | "Is PII removed/minimised before processing?" | **No.** The redaction gate is unreferenced code. The sidecar asserts `redacted: true` anyway. |
| **AIS** (Application & Interface Security) | "Are inputs validated? Is output encoded?" | **Partially no.** Untrusted questionnaire text reaches the model unfiltered and unbounded; review-UI output is inconsistently escaped. |
| **LOG** (Logging & Monitoring) | "Are privileged actions logged with the actor's identity?" | **No.** Approval — the privileged action in this system — is logged without any principal (`review_api/handler.py:230`). |
| **CEK** (Cryptography, Encryption & Key Management) | "Is data encrypted at rest with customer-managed keys?" | **Yes.** Four CMKs, rotation enabled. |
| **GRC / A&A** | "Can you evidence which policy version produced an output?" | **Half.** `RULES_VERSION` yes; guardrail version pinned to mutable `DRAFT`, so no. |
| **TVM** (Threat & Vulnerability Management) | "Is dependency and code scanning in place?" | **No.** CI has lint/type/test only. |

---

## 5. Success criteria — honest status against controls

| SC | Claim | Status | Why |
|---|---|---|---|
| **SC1** Scale + end-to-end | Not yet | Plain `Map`, `maxConcurrency: 10`, inline `$.parsed.questions` payload against a 256 KB state limit (`orchestration-stack.ts:284-288`). Distributed Map is required for ≥250 questions and is not built. One `waitForTaskToken` for the whole job with no heartbeat (`:305-318`) — topic-batching is designed but absent. Not a security finding; noted because SC1 is the gate for everything else. |
| **SC2** Accuracy on a proven holdout | Not yet | `golden-questionnaire-holdout` is correctly `enabled: false` (`config/sources.yaml:49-55`), but `data/golden/` **does not exist in the tree** — neither the training nor the holdout split is present. The split-manifest evidence SC2 requires has nothing to describe yet. |
| **SC3** Tiering deterministic, provably not model-set | **Not achievable as built, and this is a control finding, not a wording one.** | Three model-dependent inputs sit on the path into the deterministic tier: `generated.citations` decides component **C** (`confidence_scorer/scorer.py:75-84` ← `handler.py:21`); `invoked_customers` decides whether hard Rule 3 fires (`hard_rules/rules.py:124-129` ← `handler.py:74`); the generator runs at `temperature=0.2` (`generator/handler.py:93`). Separately `score_f` uses `datetime.now()` (`scorer.py:93-112`), so identical inputs tier differently across a freshness boundary. Two runs producing identical tiers for 100% of questions is not a property this code has. |
| **SC4** Guardrail enforced outside agent code | **Achievable, but not by the design as written.** | AgentCore Policy + Guardrails is GA (17 Jun 2026, `us-east-1`), enforces at the gateway perimeter before the agent, returns a 403 naming the deciding policy, and emits `aws.agentcore.policy.authorization_decision` + `determining_policies` to CloudWatch Logs group `aws/spans` **only when tracing is explicitly enabled on the gateway**. But HANDOFF §2.1 routes only classifier-flagged multi-hop questions through the harness; the single-shot majority path calls `bedrock:InvokeModel` on `Resource:['*']` directly (`orchestration-stack.ts:218-223`) and bypasses any gateway. Today, no guardrail touches model *input* at all: `invoke()` ignores its `apply_guardrail` argument (`shared/bedrock_client.py:64-82`), so the `PROMPT_ATTACK` filter at `orchestration-stack.ts:114` is configured and never evaluated, and `check_output()` fails open on both an unset ID and any `ClientError` (`bedrock_client.py:45-47`, `:60-61`). **Required addition:** the `bedrock:GuardrailIdentifier` IAM condition key (Allow on `StringEquals` + explicit Deny on `StringNotEquals`, pinned to a numeric guardrail version) on every Lambda role, so the direct path cannot invoke a model without the guardrail. Caveat: that condition key does not cover `RetrieveAndGenerate` or `InvokeAgent`. |
| **SC5** Redaction evidence a compliance buyer can hold | **Not achievable with the current engine.** | (a) The gate is not deployed — `shared/redaction.py` is imported by nothing outside tests. (b) The locked default profile `CITATION_SAFE` (HANDOFF §2.3) is selected by nothing; `config/sources.yaml` sets `strict` everywhere and `redact()` defaults to `STRICT`, which would pseudonymise the customer names the citation design requires. (c) `CITATION_SAFE` lists `PERSON` but sets `use_ml_detection=False` and there is no `PERSON` pattern (`shared/redaction.py:122-153`, `:233-242`) — **named individuals are undetectable under the POC's own default profile**, so "0 residual named individuals" cannot be evidenced. (d) `verify()` is a substring search over a caller-supplied list (`:470-484`); it can prove a *known* surface is gone, not that *no* email/phone/person/credential remains. SC5 needs an independent detector sweep over sanitised text, not `verify()`. |
| **SC6** Cost known per run | Feasible | Tag-scoping is straightforward; `cdk.Tags` already applies `project`/`stage`/`managed-by` (`infra/bin/app.ts:62-64`) but not `veup:engagement`. Weekly + yearly EventBridge re-ingestion rules (`orchestration-stack.ts:385-398`) will accrue cost in an account nobody watches after handover. FinOps owns the number; flagged here only because the recurring rules are a security-adjacent leftover. |

---

## 6. Gaps stated plainly, for the record

These are the sentences that should appear in any G2 architecture pack, unedited:

1. The PoC has **no authentication of any kind**. Both HTTP APIs and the review UI are reachable by anyone with the URL.
2. The **SME review gate is not an integrity control** in the current build. It is an unauthenticated HTTP endpoint that both completes the human sign-off and writes caller-supplied text into the corpus that future answers are generated from.
3. The **redaction engine is not deployed.** It is well-written, well-tested code that no execution path calls. The KB metadata asserts `redacted: true` regardless.
4. **Retrieval is not scoped by customer.** Prior answers given to one customer can be retrieved and cited by name in a workbook returned to another.
5. **Deterministic tiering consumes model output.** The hard rules and the composite score are deterministic functions of values the model chose.
6. **No guardrail is applied to model input**, and the output guardrail fails open.
7. The repo is still bound to a **personal AWS account** (`658203403363`), a personal domain, and — not previously listed — **live API endpoints hardcoded into the shipped UI**.
8. **Teardown at TTL is currently impossible**: COMPLIANCE-mode Object Lock for 7 years on a bucket in a 45-day account, with `RETAIN` removal policies on the other three.
9. **No credentials or keys are committed** to the tree or to git history. Verified across every reachable blob in all commits; the only `AKIA` string is AWS's own documentation example in a redaction test.

A PoC with items 1–8 written down and scheduled is defensible. The same PoC demonstrated to a compliance buyer without them is not, and this buyer will find them.
