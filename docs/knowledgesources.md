# Knowledge Sources — what the retriever queries, and what each one simulates

**Companion to:** `docs/architecture.md`
**Last updated:** 2026-04-19

Each knowledge source in this architecture is a simulation of a real enterprise data surface. The simulation choices matter — they teach different architectural lessons. This doc goes through them one by one: what the real-world system looks like, what we simulate, what we simplify away, and why the overall set was picked.

---

## 1. Compliance corpus (S3: `compliance/`)

**What it simulates:** the company's compliance document vault.

**What a real one contains:**
- Annual SOC 2 Type II audit reports (typically 60-page PDFs)
- ISO 27001 certificate + Statement of Applicability
- FedRAMP authorization package (if applicable)
- PCI DSS attestation (if you process cards)
- HIPAA BAA (if you handle PHI)
- Bridge letters between audit periods
- Penetration-test executive summaries (redacted for customer sharing)
- Sub-processor lists (current + historical)
- Data Processing Agreements (versions over time)

**Where it lives in real companies:** SharePoint or Confluence is most common. Better-run shops use a dedicated GRC platform — Vanta, Drata, Secureframe. Embarrassingly often: the compliance team's shared Google Drive.

**What we simulate:** real multi-page PDFs (`data/corpus-real/compliance/*.pdf`, generated from compact JSON sidecars by `scripts/generate_corpus_documents.py`) indexed by a **Bedrock Knowledge Base on S3 Vectors**. SOC 2 audit report ≈ 5 pages, ISO 27001 cert + SoA ≈ 3 pages, FedRAMP status ≈ 3 pages — substantial enough that fixed-size chunking (300 tokens, 20% overlap) produces 4-10 chunks per document for retrieval to discriminate across. Each PDF has a paired `<name>.pdf.metadata.json` sidecar in the Bedrock format: `{"metadataAttributes": {"document_id", "source_type": "compliance_cert", "updated_at", "topic_ids"}}`. The retriever filters by `source_type` at query time.

**What makes this data architecturally special:**
- **Highest authority in the system.** Nothing overrides the SOC 2 report. If it says "Security and Availability trust principles only," then Confidentiality is out of scope and no amount of prior-RFP phrasing can claim otherwise.
- **Legally sensitive.** A hallucinated compliance claim is breach-of-warranty exposure in enterprise contracts.
- **Low volume, high importance.** A real company has maybe 50–100 such documents. Each one is a legal asset.
- **Refreshed on a known cadence.** SOC 2 annually, bridge letters quarterly, sub-processor lists monthly. The `updated_at` field is the *anchor* for the whole freshness-suppression mechanism.

**Why this source anchors the "source authority" design:** when the 2026 SOC 2 report drops, every prior RFP answer that cited the 2025 report becomes potentially wrong. Detecting that automatically — rather than hoping someone remembers to re-review the library — is the entire reason this project exists.

---

## 2. Product-docs corpus (S3: `product-docs/`)

**What it simulates:** internal product and engineering documentation.

**What a real one contains:**
- Architecture whitepapers (encryption model, infrastructure, security posture)
- Disaster-recovery runbooks + published RTO/RPO commitments
- SSO/MFA integration guides
- API documentation and rate-limit policies
- Feature capability matrices ("what's in Enterprise vs Pro?")
- Data retention + deletion policies
- Access-control model documentation
- Audit-logging capabilities

**Where it lives in real companies:** Confluence or Notion for internal consumption. A subset is published externally on a docs site (Docusaurus, GitBook, Mintlify). Sometimes just README files in a monorepo.

**What we simulate:** three long-form markdown documents (`data/corpus-real/product-docs/*.md`) — encryption whitepaper, SSO/MFA integration guide, DR/BCP overview — each ~1,500-1,700 words covering the real subject surface (cryptographic agility, post-quantum posture, SCIM vs JIT provisioning, regional failover runbook, dependency mapping). Indexed into the same Bedrock KB as the compliance corpus; discriminated at query time by `source_type: product_doc` on the metadata sidecar.

**How this differs architecturally from the compliance corpus:**
- **Medium authority** — authoritative for *what the product does* but Engineering owns it, not Legal
- **Higher velocity** — changes with every major release, sometimes more often
- **Larger corpus in real life** — hundreds of pages, not tens
- **Different review cadence** — a new encryption whitepaper doesn't need an external auditor, just internal sign-off

**The distinction between compliance and product-docs is worth internalising:** compliance docs prove claims *to outside auditors*. Product docs describe capabilities *to prospects evaluating you*. Both are Primary sources for their respective topics, but they have different owners, different review cycles, different risk profiles, and different expected velocities. Treating them as one undifferentiated "company documents" bucket would lose all that nuance.

---

## 3. SME-Approved Q&A — the prior RFP response library (Bedrock KB, `sme-approved/` prefix)

**As of Phase F (2026-04-19):** this source moved from DynamoDB LibraryFeedback into the single Bedrock KB under the `sme-approved/` prefix. The old DDB path is gone. What follows describes the current KB-backed design.


**What it simulates:** the single most valuable asset a mature RFP team has.

**What a real one contains:**
- Every question from every RFP the company has ever responded to (hundreds to thousands)
- The final answer that actually shipped
- Which SME approved it + approval date
- Associated topics/tags
- Win/loss outcome of the parent RFP (if that's tracked)
- Customer industry and size (for matching to similar future prospects)
- Sometimes: reviewer edit history, confidence notes, "don't use in context X" flags

**Where it lives in real companies:** this is a whole product category.
- **Loopio, Responsive** (formerly RFPIO), **Arphie**, **Qwilr**, **Superlegal** — dedicated RFP platforms
- A shared spreadsheet (small/immature teams)
- Salesforce custom objects (CRM-centric companies)
- Individual AE notes, never consolidated (worst case — shockingly common)

**What we simulate:** 12 seeded Q&A records covering encryption-at-rest, encryption-in-transit, SSO, MFA, DR, backup, incident response, access control, SOC 2 scope, ISO 27001 certification, DPA + sub-processors, and data residency. Each is stored as a markdown file under `sme-approved/<id>.md` in the reference corpus bucket with a paired `.metadata.json` sidecar carrying `document_id`, `source_type=sme_approved_answer`, `topic_ids`, `approved_by`, `approved_at`, `expires_on`, and `question_text`. The sidecar's `corroborated_by` (tracked in the source JSON at `data/corpus/sme-approved/*.json`) captures the Primary sources that justified the approval at approval time.

**What our simulation gets right that commercial products usually don't:**

The `corroborated_by` field is the killer feature. When the SOC 2 answer was approved, we recorded *which Primary source documents justified the approval* — e.g., `["soc2_cert_2025"]`. Loopio and Responsive don't do this. They have an "approved" checkmark but no link back to *why* the approval was valid. Without that link, you can't detect when the underlying justification has moved.

**Query-time freshness closes the loop:** the retriever's `_apply_freshness_suppression` runs on every retrieval. For each SME-approved passage that comes back from the KB, it compares `approved_at` to the freshest Primary source's `updated_at` in the same query's result set. If a Primary is newer, the approved passage is suppressed from the H signal for that query. Phase F deleted the old out-of-band staleness daemon — the KB + freshness rule run the whole anti-decay mechanism uniformly across all retrieval paths.

**What our simulation simplifies away:**
- Real libraries have 1K–100K entries, not twelve. At ~1K entries the KB ingestion starts to matter (multi-minute jobs); at 10K+ you'd want OpenSearch Serverless for the vector store.
- Real libraries fold in win/loss signal — answers from won deals get boosted, answers from lost deals get down-weighted.
- Real approval workflows track multi-stage reviews (SME → Legal → Compliance), versioning, and reviewer chains. The sidecar carries a single `approved_by` + `approved_at`.

**Why this is the source of the biggest signal (H = 0.45 weight):** SME approval is the strongest quality signal we have. Everything else is inferred. "A human expert literally signed off on substantially similar content" is *direct* evidence in a way that retrieval strength never is.

---

## 4. CustomerRefs — the customer reference program (DynamoDB)

**What it simulates:** the company's customer advocacy / logo / reference tracking program.

**What a real one contains:**
- Which customers have signed a reference agreement
- Reference tier: "will do a case study" vs "will take a reference call" vs "logo use only" vs "no public mention"
- Approval expiry dates (usually 12–24 month renewal cycle)
- Industry, size, use case (for matching future prospects to similar references)
- Named reference contact at the customer
- Products/tiers they've deployed
- Which competitors they evaluated against (useful when the prospect is eval-ing the same competitor)
- Sensitivity flags: "do not use this reference against Competitor X" or "strategic account, clear every use with Customer Marketing first"

**Where it lives in real companies:**
- **Salesforce** custom objects (most common at scale)
- **ReferenceEdge** or **Influitive** (dedicated reference-management platforms)
- Customer Marketing's spreadsheet (small/mid-size companies)
- Sometimes as a flag on the Account object in the CRM

**What we simulate:** 5 customer records with the core fields — `customerId`, `name`, `industry`, `employees`, `contract_size_usd`, `public_reference`, `approval_expires`, `deployed_products`.

**Important edge cases baked into the seed data:**
- **Kestrel Logistics** — `public_reference: true, approval_expires: 2026-03-17`. Today is 2026-04-16. **Their approval expired a month ago.** The system must not treat them as currently referenceable → amber.
- **Aurora Federal Credit Union** — `public_reference: false`. We cannot name them at all → amber.
- **Northwind, Helix, Meridian** — active public references → can be named in green answers (subject to other gates).

These aren't accidental — the seed data is specifically constructed to exercise all three paths of hard-rule #4 during a demo.

**Why this is a gate in the hard-rules layer, not a retrieval filter:** because naming customers is a **legal/commercial concern, not an answer-quality concern**. An answer could be technically perfect and still violate:
- A contract (NDA breach — customer agreements often restrict how you can reference them)
- Brand policy (customer calls Legal, Legal calls your CEO, bad week ensues)
- GDPR (in EU contexts, the customer's identity as your customer is itself a data point)

The hard-rules engine treats this as non-negotiable. Even a perfect answer citing a perfect customer goes amber if CustomerRefs doesn't clear it.

---

## 5. Seismic + Gong — external SaaS (mocked as one Lambda)

### Seismic

**What it simulates:** a Sales Enablement Platform. Seismic is the category leader; competitors are Highspot, Showpad, Mindtickle.

**What a real Seismic instance contains:**
- Sales decks (first-meeting, technical deep-dive, executive, vertical-specific)
- Competitive battle cards ("how we beat Competitor X")
- Case studies (PDF + slideware versions)
- Pricing calculators (internal tools)
- One-pagers per industry
- Email templates Marketing has approved
- Video testimonials
- Product demo recordings
- Analyst report excerpts (Gartner, Forrester — where licensing allows)

**Real-world access pattern:** REST API with OAuth 2.0. You authenticate, query by tag/folder/date/content-type, get metadata + download URLs. Content is often behind additional access controls ("this battle card is internal-only").

**Why it's *tertiary* in our dispatch table:** Seismic content is Marketing-approved *phrasing*. It's how the company says things in polished form. It is NOT authoritative for technical or compliance claims. "Our sales deck says we have SOC 2" is not equivalent to "the SOC 2 report itself says X."

### Gong

**What it simulates:** a Conversation Intelligence Platform. Category peers: Chorus, Wingman, Avoma.

**What a real Gong instance contains:**
- Recording and transcript of every recorded sales call (plus Zoom/Teams calls)
- Speaker-diarised transcripts (who said what, when)
- Sentiment + topic tagging per call
- "Deal warning" flags (mentions of competitors, pricing objections, etc.)
- Search across transcripts — "find all calls in Q1 where the prospect asked about SOC 2"
- AI-summarised call notes
- Comparison across won vs lost deals

**Real-world access pattern:** REST API with API key + workspace scope. Rate limits per workspace. Payload sizes can be huge (long-call transcripts).

**Why it's *tertiary* in our dispatch table:** Gong captures *spoken* language, not written policy. An AE might have said on a call "we're SOC 2 certified" when the more precise truth is "we have SOC 2 Type II for the Security and Availability trust principles only, last audit ended 2025-09-30." Great for finding how people talk about a topic. Not safe as a factual source.

### What we simulate about both (a single mock Lambda)

- **One Lambda, two routes** (`/seismic/content`, `/gong/calls`) — deliberately not two Lambdas. Their response shapes are similar; splitting would be duplication without payoff.
- **Bearer-token auth** — any token passes. Real OAuth would require token refresh, scope validation, audience checks.
- **5% random error rate** — this is non-negotiable realism. Production SaaS APIs flake.
- **Occasional 2-second tail latency on ~10% of calls** — they have bad days.
- **Per-minute rate limits** — every SaaS API caps you. Discover the cap in production at your peril.

### What the mock teaches architecturally

**Circuit breaker pattern.** The retriever wraps each source call in a breaker: three consecutive failures → skip this source for the rest of the job. Most engineers learn this pattern the hard way after their first production outage. Baking it in from day one is the architectural maturity signal that separates "built for demo" from "built for production."

**Graceful degradation.** If Gong is down, the answer still ships — flagged amber with `source_degraded:gong` and the confidence scorer's C (coverage) signal downweights accordingly. The pipeline doesn't crash. Achieving this requires deliberate code — the retriever must treat source failures as expected, not exceptional.

**External SaaS is a different risk class from your own storage.** S3 gives you four 9s of availability. Random SaaS APIs do not. A production architecture has to plan for this from day one. Ours does.

### What the mock simplifies away

- Real Seismic/Gong responses are much richer (pagination, permissions, asset lifecycles, deep filter parameters).
- Real authentication is OAuth 2 with token refresh and expiry, not "any bearer accepted."
- Real rate limits are per-API-key tiered, with different quotas for different endpoints.
- Real APIs have per-endpoint burst behaviors you only discover in production.

---

## Why the five together tell an architectural story

Step back and look at the set:

| Source | Storage | The pattern it teaches |
|---|---|---|
| Compliance corpus | PDFs in S3 `compliance/`, indexed by Bedrock KB + S3 Vectors | Legally sensitive, low-volume, high-authority. Primary source for regulatory claims. |
| Product-docs corpus | Markdown in S3 `product-docs/`, same KB | Higher-velocity internal docs. Owned by Engineering. Authoritative for capabilities. |
| Prior-RFPs corpus | Markdown in S3 `prior-rfps/`, same KB | Phrasing-only secondary source. Never factual authority. Subject to freshness suppression. |
| SME-Approved Q&A | Markdown in S3 `sme-approved/`, same KB (H signal) | Self-cleaning approval flywheel via real cosine similarity + query-time freshness. What commercial RFP tools don't do. |
| CustomerRefs | DynamoDB | Legal/commercial gate. Not about answer quality — about whether we're allowed to *say* it. |
| Seismic + Gong | Mock Lambda behind API Gateway | Flaky external SaaS. Demonstrates circuit breaker + graceful degradation. |

If all five were S3 buckets, the "unified search across heterogeneous enterprise sources" story wouldn't land. **The heterogeneity is the point.** You're seeing the actual shape of what a Glean, a Kendra, or a homegrown RAG stack has to contend with when it indexes a real enterprise — five different ownership models, five different refresh cadences, five different failure profiles, five different authority tiers.

The dispatch table (see `architecture.md` §5.3) encodes all of that into a routing policy. Every architectural decision downstream — freshness suppression, corroboration rules, circuit breakers, confidence weights — flows from the fact that these five sources are genuinely *different kinds of things* and have to be treated differently.

That's the story.
