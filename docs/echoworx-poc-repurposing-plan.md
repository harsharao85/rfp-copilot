# Repurposing `rfp-copilot` → Echoworx Security Questionnaire POC

**Audience:** VeUP internal (Anthony / Chris / Tommy)
**Status:** Draft for review — three decisions needed before build starts (§7)
**Basis:** Repo inspected at commit `5a90587`; AWS facts verified against AWS docs 26 Aug 2026 (§8)

---

## 1. Headline verdict

**The domain fit is strong. The AgentCore question has a nuanced answer: use it, but not as the orchestrator.**

Two things to separate:

**Domain fit — very good, less work than expected.** The repo already answers exactly the class of question Echoworx receives ("What encryption do you use? Are you SOC 2 certified? Where does data reside?"). The v0.4 design non-negotiables — pricing always red, compliance claims always amber-minimum, references gated on a `public_reference` flag, no LLM self-reported confidence — are precisely the controls a security-questionnaire tool needs. The `confidence_scorer` composite model and `hard_rules` engine are the reusable IP here and should survive the port largely intact.

**Orchestration — do not rip out Step Functions.** The pipeline is deterministic, bounded and auditable: parse → per-question chain → human gate → write. That is what Step Functions is for. Replacing it with a free-roaming agent loop would trade away the exact property that makes this sellable to Echoworx, whose buyer *is* a compliance function. An agent that decides its own path is much harder to defend in front of the people who will audit its output.

The right move is a **hybrid**, and as of June 2026 this is a first-class supported pattern rather than a workaround: <cite index="9-1">AWS Step Functions now integrates natively with AgentCore harness, letting teams embed agent reasoning steps directly into production workflows — running harnesses in parallel or sequence within a workflow and wrapping them with human approval, error handling or conditional routing steps</cite>.

So: Step Functions stays the spine. AgentCore goes inside it, in four specific places where it genuinely earns its keep (§3).

---

## 2. The gating constraint: region and residency

Echoworx sells data sovereignty. That makes region selection the first decision, not the last, and it constrains everything downstream.

**AgentCore in `ca-central-1` — green light.** Verified against the AWS supported-regions table: Canada (Central) has harness, Runtime microVMs, Memory, Gateway, Identity, Built-in Tools, Observability, Policy, Evaluations and optimization. Everything this design needs is there. Absent from `ca-central-1`: Runtime Instances, payments, Agent Registry, Web Search Tool — none of which we need.

**Model inference — this is the live problem.** The repo currently pins `us.anthropic.claude-sonnet-4-5` and `us.anthropic.claude-haiku-4-5` cross-region inference profiles. Canadian customers can reach current-generation Claude models from `ca-central-1`, but the routing needs settling explicitly:

- <cite index="18-1">Canadian customers can choose between two CRIS profile types from `ca-central-1`: US cross-Region inference, where requests are routed to supported US Regions with capacity, and Global inference, routing to any Region in the AWS global CRIS profile.</cite>
- <cite index="13-1">In both cases, while inference processing (the transient computation) may occur in another Region, data at rest — logs, knowledge bases, stored configurations — remains exclusively within the source Region.</cite>
- A `ca.` geographic CRIS prefix is also reported as available in `ca-central-1`. This needs first-party confirmation.

The "data at rest stays in Canada, only transient inference leaves" distinction is technically accurate and legally meaningful. It is also **exactly the distinction Echoworx's own customers interrogate them about**, and it is very likely to appear in the questionnaires this tool answers. If the tool that answers "does our data leave Canada?" is itself routing inference to Virginia, that is a conversation we should have on our terms, in advance — not one Randy discovers later.

**Action before build:** run `aws bedrock list-inference-profiles --region ca-central-1` in the target account and confirm which Claude profiles are actually invocable and whether a `ca.`-prefixed in-geography profile exists. This is a five-minute check that determines whether we can promise in-Canada inference or must scope the POC as "in-Canada at rest, US transient compute."

**Second residency item:** the repo's Knowledge Base uses S3 Vectors + Titan Embed v2. S3 Vectors availability in `ca-central-1` must be confirmed the same way. If it isn't there, the fallback options are OpenSearch Serverless (materially more expensive — the repo's own architecture doc puts it at roughly 70× S3 Vectors at demo scale) or the newer AgentCore Managed Knowledge Base, which <cite index="9-1">went GA in June 2026 with six native connectors (S3, SharePoint, Confluence, Google Drive, OneDrive, Web Crawler), managed vector storage, hybrid search and document ranking, queryable by agents through the gateway</cite>. Given Echoworx's evidence almost certainly lives in SharePoint or Confluence, the managed KB connectors may be worth adopting on their own merits.

---

## 3. Where AgentCore actually earns its place

Four components, ranked by value to this POC. Everything else in the AgentCore surface area is deliberately out of scope.

### 3.1 Gateway — highest value

Echoworx's evidence is scattered: policy documents, their SOC 2 and ISO 27001 artefacts, prior completed questionnaires, remediation status in a ticketing system, possibly a trust portal. Gateway turns each of those into an MCP tool behind one authenticated endpoint, replacing the repo's `mock_sources` Lambda stub with something real.

The stronger argument is governance. <cite index="9-1">AgentCore now supports Bedrock Guardrails in Policy: guardrails evaluate outputs from authorized agent actions and inputs to gateway targets for prompt injection, harmful content and sensitive data exposure — and these checks run at the gateway layer, outside the agent's code, where the agent cannot reason around them. Because every tool and context source routes through the gateway, every new agent capability is automatically governed by the same security layer.</cite>

"The control is enforced outside the model, and the model cannot route around it" is a sentence that lands with a security buyer in a way that "we prompt it carefully" does not. Today the repo applies guardrails on generator output inside the Lambda. Moving enforcement to the gateway is a genuine control improvement, not just a re-platforming.

### 3.2 Harness inside the Map state — real capability gain

Replace the current fixed `retriever → generator` Lambda pair with a harness invocation, for hard questions only.

Justification: a question like *"Describe your key rotation procedure for customer-managed keys and how you evidence it to auditors"* needs multi-hop work — find the policy, check whether a SOC 2 control maps to it, check whether a prior answer exists, detect whether policy and prior answer now conflict. A single-shot retrieve-then-generate under-serves that. This is where agentic behaviour is a capability gain rather than architectural fashion.

Keep it bounded: classify first (cheap, deterministic, Haiku), and route only questions the classifier marks as multi-hop or evidence-dependent to the harness. Simple factual lookups stay on the existing single-shot path. This controls both cost and variance.

### 3.3 Evaluations — the sleeper, and probably the most important POC deliverable

The POC will be judged on one question: *how accurate is it, and how do you know?* The repo has nothing for this today.

<cite index="9-1">AgentCore Evaluations reached GA in March 2026 with 13 built-in evaluators covering response quality, safety, task completion and tool usage, plus Ground Truth support that measures agent performance against reference answers, behavioural assertions and expected tool execution sequences.</cite>

Echoworx has a corpus of historically-answered questionnaires. That is a ready-made ground-truth set. Holding some back, running the pipeline against it and reporting a measured accuracy figure converts the POC from a demo into evidence. I would make this a named deliverable rather than a nice-to-have — it is the thing that survives contact with their procurement process.

### 3.4 Identity — phase 2, but scope it now

Needed if the agent must read sources under the permissions of a specific Echoworx user rather than a service account. <cite index="9-1">Identity supports on-behalf-of token exchange, letting agents access protected resources on behalf of authenticated users without repeated consent flows</cite>, and <cite index="9-1">Private Key JWT client authentication, where the private key stays in KMS, never leaves it, and every signing operation is recorded in CloudTrail</cite>.

Worth raising with Randy early even if we don't build it: "whose permissions does the agent inherit" is a question he will ask, and having an answer ready is better than improvising.

### 3.5 Explicitly out of scope

Memory (marginal for a batch workload; the existing DynamoDB + SME-approved-answer loop already covers the learning path), Code Interpreter, payments, Agent Registry, Web Search. **Browser tool** is the interesting deferred one — if Echoworx receives questionnaires through portals like OneTrust, Whistic or Panorays rather than as files, Browser becomes genuinely valuable in phase 2. Worth asking what proportion of their inbound arrives that way, but do not promise it in the POC.

---

## 4. Compliance posture — a point worth making explicitly

Selling an AI tool to a security vendor means the tool's own compliance status is in scope. Both relevant facts are recent and favourable:

- <cite index="9-1">AgentCore is SOC compliant and in scope for SOC 1, 2 and 3 reports, listed on the AWS Compliant Services in Scope page (June 2026).</cite>
- <cite index="9-1">AgentCore achieved ISO and CSA STAR certification in February 2026 and is listed on the AWS compliant services page.</cite>

Lead with this. Randy will ask, and "in scope for SOC 1/2/3, ISO and CSA STAR certified, here is the AWS page" is a much better answer than "we'll check."

---

## 5. Concrete change list

### Phase 0 — De-personalise and land in VeUP (est. 1–2 days, no design risk)

Mechanical, needed regardless of the AgentCore decision. All of it is currently hardcoded to a personal demo account.

| File | Change |
|---|---|
| `infra/bin/app.ts` | Region default `us-east-1` → context-driven; `domainName` hardcoded to `rfp-copilot.meringue-app.com` → per-stage context |
| `infra/lib/storage-stack.ts:58` | CORS `allowedOrigins` hardcoded to the personal domain |
| `infra/cdk.context.json`, `infra/outputs.json` | Contain personal account `658203403363` and live resource ARNs — **delete and gitignore, do not port** |
| `lambdas/shared/bedrock_client.py:23-29` | `us.`-prefixed model profiles and `us-east-1` fallback → environment-driven |
| `scripts/ingest_corpus.py:25-26` | `region_name="us-east-1"` hardcoded on both clients |
| `scripts/recycle.sh`, `deploy-for-demo.sh` | Region defaults |
| Repo name | `rfp-copilot` → suggest `questionnaire-copilot`; "RFP" language is throughout and reads oddly for an inbound-assessment use case |

Also add in this phase: **Cognito**. The repo's architecture doc lists auth as a deliberate demo-tier omission — the UI is public to anyone with the link. That is fine for a personal demo and unacceptable the moment Echoworx's actual security posture documents are in the bucket.

### Phase 1 — Scale the spine for real questionnaires (est. 3–4 days)

The current Map state is capped at concurrency 10 and the architecture doc notes it maxes out around 30 questions. Real security questionnaires are an order of magnitude larger — CAIQ is in the hundreds of questions, SIG runs into the low thousands. This forces the Distributed Map upgrade that is already flagged TODO in the README (phase 5). It is no longer optional.

- `infra/lib/orchestration-stack.ts`: Map → Distributed Map
- Revisit the review-gate pattern: `waitForTaskToken` across hundreds of amber/red items needs batching by topic, not one token per question, or the SME review experience will be unusable

### Phase 2 — AgentCore integration (est. 1–1.5 weeks)

- Stand up Gateway; convert `lambdas/mock_sources` into real MCP targets against Echoworx sources
- Move Bedrock Guardrails from generator-output to gateway-layer Policy enforcement
- Introduce harness via the native Step Functions integration; route classifier-flagged multi-hop questions to it, leave simple lookups on the existing path
- **Leave `confidence_scorer` and `hard_rules` as deterministic Lambdas.** The tiering decision must not be made by the agent. This preserves the "no LLM self-reported confidence" non-negotiable, which is more important here than it was in the original design
- CDK note: <cite index="9-1">the `aws-bedrockagentcore` L2 constructs graduated from alpha to stable in `aws-cdk-lib` in May 2026, so AgentCore resources can be defined with full backward-compatibility guarantees and no separate `-alpha` package</cite> — the Policy submodule remains alpha

### Phase 3 — Evaluation harness (est. 3–5 days, highest POC value per day spent)

- Ingest Echoworx's historically-answered questionnaires, hold back a test split
- Wire AgentCore Evaluations with ground-truth scoring
- Produce a measured accuracy figure, broken down by question category
- This is the deliverable that closes the POC

### Rewrite the design non-negotiables

The v0.4 rules are sales-RFP-shaped. The security-questionnaire equivalents need agreeing **with Randy**, not assumed by us. Straw man to react to:

1. Any claim of certification or attestation status → amber minimum, must cite the specific artefact and its validity period
2. Any answer describing a control that is planned rather than operating → red (forward-looking claims to an assessor are a materially different risk from an optimistic sales answer)
3. Any answer touching sub-processors or data residency → red (contractual exposure)
4. Any answer contradicting a prior SME-approved answer → red with both versions surfaced, never silently resolved
5. Answers citing evidence past its validity window → suppressed, not downgraded

Rule 4 is the one I would emphasise in the pitch. Silent drift between what a company said last quarter and what it says now is the specific failure mode that makes security questionnaires expensive and dangerous, and it is a failure no amount of human effort catches reliably at volume. That is a sharper value proposition than "saves time."

---

## 6. Indicative cost shape

**Internal figures only — do not put these in a customer document without re-verifying against the AWS pricing page for `ca-central-1`.**

AgentCore is consumption-metered per capability, with the harness itself free. Runtime, Browser and Code Interpreter bill at roughly $0.0895 per vCPU-hour and $0.00945 per GB-hour, with a 128 MB memory floor; Gateway bills per MCP operation, Memory per event stored and per retrieval.

Two things that actually shape the bill:

- **CPU is billed on active use only.** Idle I/O wait while the model is thinking is not billed as CPU — a real advantage over pre-allocated compute for a workload like this, which is mostly waiting.
- **Memory is billed on peak footprint for the session lifetime**, so long sessions sitting idle still accrue. Set aggressive idle timeouts.

At POC scale the AgentCore infrastructure lines will be small — likely tens of dollars a month. The bill will be dominated by **Bedrock model tokens**, and secondarily by CloudWatch span ingestion if observability is left wide open. Two levers worth modelling before quoting anything: prompt caching on the shared corpus context (potentially large, since every question in a questionnaire shares much of the same policy context), and the token burndown multiplier on reasoning models, which is not 1:1 and will make quota arrive sooner than raw token counts suggest.

I'd rather build a proper model against a real questionnaire from Randy than quote a range. Given the PoC cost-model gap we identified on the network egress workstream, arriving with a defensible number here would be a good look.

---

## 7. Decisions needed before build starts

1. **Whose account?** VeUP's environment for the POC, or an Echoworx-owned account? This is a data question before it is a commercial one — the POC needs Echoworx's real policy corpus and prior questionnaire answers to be worth anything, and putting a security vendor's complete control documentation into a VeUP account is a conversation with their legal team, not a technical detail. It also connects directly to the dedicated-account and PrivateLink discussion already in flight on the network workstream.

2. **Inference routing.** In-Canada only, or in-Canada-at-rest with US transient compute? Determines model availability and needs Randy's explicit sign-off rather than our assumption. Blocked on the `list-inference-profiles` check in §2.

3. **Corpus scope for the POC.** Which subset of Echoworx's evidence, and how many historically-answered questionnaires can they supply for ground truth? Fewer than roughly 50 answered questions and the accuracy figure in Phase 3 won't be statistically meaningful, which removes the strongest thing the POC could produce.

Worth asking Randy alongside these: what proportion of inbound questionnaires arrive as files versus through assessment portals? It changes the phase-2 roadmap materially and costs nothing to ask now.

---

## 8. Sources

All AWS facts verified 26 August 2026. AgentCore is moving very fast — the release notes carry entries in most months of 2026, so re-verify anything here that is more than a few weeks old before it goes in front of a customer.

- AgentCore release notes — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html
- AgentCore supported Regions — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html
- AgentCore GA announcement — https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available
- Bedrock CRIS from Canada — https://aws.amazon.com/blogs/machine-learning/accelerate-generative-ai-innovation-in-canada-with-amazon-bedrock-cross-region-inference
- AgentCore pricing — https://aws.amazon.com/bedrock/agentcore/pricing/
- Step Functions ↔ harness integration — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-step-functions.html
- Well-Architected Framework — https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html
