# Scoring Mechanism — how knowledge sources become red/amber/green

**Companion to:** `docs/architecture.md` and `docs/knowledgesources.md`
**Last updated:** 2026-04-16

This doc answers the question: *given the state of the five knowledge sources, what tier does a question end up at, and why?*

It's organised in four layers — signals, corroboration rules, hard rules, and concrete scenarios — and closes with a summary matrix you can actually memorise.

---

## Layer 1 — Which source feeds which signal?

The composite formula is:

```
composite = 0.45·H + 0.25·R + 0.15·C + 0.10·F + 0.05·G
```

Each signal has a dominant source and some contributing sources.

| Signal | Weight | What it measures | Dominant source | Contributing sources |
|---|---|---|---|---|
| **H** (prior-answer similarity) | 0.45 | Is there an SME-approved prior that matches this question? | **LibraryFeedback** | — |
| **R** (retrieval relevance) | 0.25 | How well do retrieved passages match the question keywords/topics? | Compliance + Product-docs | Seismic + Gong (tertiary) |
| **C** (source coverage) | 0.15 | How many different sources contributed evidence? | All of them | The more diverse, the better |
| **F** (freshness decay) | 0.10 | How recently were the retrieved docs updated? | Compliance + Product-docs (via `updated_at`) | LibraryFeedback (via `approved_at`) |
| **G** (guardrail pass) | 0.05 | Did Bedrock Guardrails flag anything? | None — model-level | — |

**CustomerRefs doesn't feed any signal in the composite.** It's a pure hard-rule gate — it answers a binary question ("is naming this customer allowed?") that happens *after* scoring.

### The structural consequence of H = 0.45

The non-H signals together cap at `0.25 + 0.15 + 0.10 + 0.05 = 0.55`. That's *exactly* at the amber/red boundary. So:

- **An empty LibraryFeedback means no question can possibly reach green.** Best case is amber, and only if every other signal maxes out.
- **The single biggest driver of answer quality is whether a prior SME has signed off on something similar.** Everything else combined is less than that one signal.
- **This is intentional**, not a weighting accident. Green literally means "a human expert already approved something like this."

---

## Layer 2 — How the corroboration rules modify retrieval

Before the composite is even calculated, four rules prune what reaches the generator. This means the same raw sources can produce different signal values depending on the *state* of the sources.

| Rule | What it checks | Effect on signals |
|---|---|---|
| **1. Freshness suppression** | Is a LibraryFeedback prior older than any relevant Primary source? | If yes → prior is suppressed before retrieval returns. H drops to 0 for that question. |
| **2. Primary required** | Topic marked `corroboration_required: true` but no Primary source returned hits? | Answer tier is **capped at amber** regardless of composite. R and C stay as calculated. |
| **3. Force-tier** | Topic has `force_tier: RED` (e.g., pricing)? | Tier forced to red regardless of composite. Signals are calculated but ignored. |
| **4. Customer name gating** | Generated answer names a customer not cleanly in CustomerRefs? | Tier forced to at-least-amber regardless of composite. |

Rule 1 is the subtle one. Same library entry, same Primary source, but over time:

```
Day 0:   prior approved_at = 2025-10-01,  SOC2 updated_at = 2025-10-01  →  H signal lights up
Day 180: new SOC2 uploaded, updated_at = 2026-04-01, prior still approved_at = 2025-10-01
                                                                        →  prior SUPPRESSED
                                                                        →  H drops to 0
                                                                        →  same question now scores amber
```

Until someone re-approves the prior against the new SOC 2 report, the library has lost that answer's contribution. **The library is younger than the Primary by design.**

---

## Layer 3 — The hard-rule overrides

Hard rules run *after* the composite and can only demote, never promote. They take a tier and push it down.

| Detected in answer | Override | Which source triggers it |
|---|---|---|
| Pricing / commercial language | Force RED | Topic dispatch (`pricing` has `force_tier: RED`) |
| Compliance claim without citation | Min AMBER | The answer text itself (regex) + missing Compliance corpus citation |
| Customer name | Min AMBER unless cleared | CustomerRefs lookup |
| Forward-looking language ("will deliver") | Min AMBER | The answer text itself (regex) |
| Competitor disparagement | Force RED | The answer text itself (regex) |

---

## Layer 4 — The full matrix: source state → tier

Combining all three layers, here's what determines the final tier:

| Scenario ID | LibraryFeedback state | Primary source state | Retrieval outcome | Hard-rule trigger? | Final tier |
|---|---|---|---|---|---|
| **G1** | Match, fresh | Hits, same era as prior | Strong | None | **GREEN** |
| **G2** | Multiple matches, fresh | Multiple hits | Very strong | None | **GREEN** |
| **A1** | Match, *but Primary is newer* → suppressed | Hits | Strong R, but H=0 | None | **AMBER** |
| **A2** | No match | Hits | Strong R, C, F but H=0 | None | **AMBER** (composite ≤ 0.55) |
| **A3** | Match, fresh | No hit, corroboration_required | H + modest R | Rule 2 caps at amber | **AMBER** |
| **A4** | Match, fresh | Hits | Strong everything | Forward-looking phrasing detected | **AMBER** |
| **A5** | Match, fresh | Hits | Strong everything | Customer named, approval expired | **AMBER** |
| **A6** | Match, fresh | Hits | Strong everything | Customer named, `public_reference=false` | **AMBER** |
| **R1** | No match | No hit | Everything weak | None | **RED** (composite < 0.55) |
| **R2** | Any state | Any state | Any state | Topic = pricing (force_tier: RED) | **RED** |
| **R3** | Any state | Any state | Any state | Competitor disparagement regex hit | **RED** |
| **R4** | No match | No hit on any Primary, only Seismic/Gong returned | Low R, low C, low F | None | **RED** |

---

## Concrete scenarios with math

Six instructive walkthroughs.

### Scenario G1 — "How is data encrypted at rest?"

**Sources consulted:**
- LibraryFeedback: SME-approved prior exists (`"All customer data is encrypted at rest using AES-256-GCM..."`) — approved 2026-01-15, corroborated by `encryption_whitepaper`.
- Product-docs: `encryption_whitepaper.json` hit, `updated_at: 2025-10-20`.
- No Seismic/Gong hit (not needed).
- No customer name in answer.

**Signals:**
- H = 0.9 (strong prior match)
- R = 0.8 (strong retrieval from product-docs)
- C = 0.5 (two sources: library + product-docs)
- F = 0.9 (both recent)
- G = 1.0 (no guardrail hits)

**Composite:** `0.45×0.9 + 0.25×0.8 + 0.15×0.5 + 0.10×0.9 + 0.05×1.0 = 0.405 + 0.20 + 0.075 + 0.09 + 0.05 = 0.82`

**Hard rules:** none triggered.

**Final: GREEN** ✅

---

### Scenario A1 — The freshness-suppression cascade

Same question as G1, but 90 days later: someone uploaded a new encryption whitepaper (`updated_at: 2026-07-01`). The prior approval (`approved_at: 2026-01-15`) is now older than the corroborating Primary.

**Corroboration Rule 1 fires:** prior is suppressed before retrieval returns.

**Signals:**
- H = 0 (prior suppressed)
- R = 0.8 (the new whitepaper is still a strong retrieval hit)
- C = 0.3 (only one source now — library entry dropped out)
- F = 1.0 (the new whitepaper is very fresh)
- G = 1.0

**Composite:** `0 + 0.20 + 0.045 + 0.10 + 0.05 = 0.395`

**Final: RED** (this is actually red, not amber, because losing H + losing C together drops below 0.55)

**What this means operationally:** the question that scored green yesterday now scores red. An SME gets it, reviews, approves against the new whitepaper → a new LibraryFeedback record with `approved_at: 2026-07-15` and `corroborated_by: [encryption_whitepaper_v2]` → next time the question runs, back to green. **That's the self-cleaning flywheel you feel in action.**

---

### Scenario A3 — Corroboration required, no Primary hit

Question: **"Describe your SOC 2 Type II audit scope and trust principles covered."**

- LibraryFeedback: has a prior SME-approved answer from 2025 (`"SOC 2 Type II covering Security and Availability..."`), `corroborated_by: [soc2_cert_2025]`.
- Compliance corpus: **no SOC 2 document was uploaded to S3** (simulate this by deleting the seed).
- Topic dispatch plan for `soc2` has `corroboration_required: true` and `max_tier_without_primary: AMBER`.

**Signals:**
- H = 0.9 (prior matches well)
- R = 0.3 (nothing from Compliance; only library content retrievable)
- C = 0.2 (one source)
- F = 0.5 (prior is ~6 months old)
- G = 1.0

**Composite:** `0.45×0.9 + 0.25×0.3 + 0.15×0.2 + 0.10×0.5 + 0.05×1.0 = 0.405 + 0.075 + 0.03 + 0.05 + 0.05 = 0.61`

That composite would be amber on its own. But **Rule 2 caps this at amber regardless** — the rule says "if corroboration_required and no Primary hit, never let it go green even if the composite somehow crosses 0.80."

**Final: AMBER** (capped, not because of math)

**Why this rule exists:** even if the prior answer is a great match, if we can't show the current SOC 2 report corroborating it *right now*, a human must confirm it before we ship. The library alone isn't allowed to vouch for a compliance claim.

---

### Scenario A5 — Customer name, expired approval

Question: **"Provide a reference customer of similar size in financial services."**

The generator, trying to be helpful, answers: *"We have deployed successfully at Kestrel Logistics, a 12,500-employee organization…"*

- **CustomerRefs lookup:** `Kestrel Logistics` → `public_reference: true`, but `approval_expires: 2026-03-17`. Today is 2026-04-16. **Expired.**

**Signals:**
- H = 0.0 (no good prior match for this specific prospect's industry)
- R = 0.6 (some retrieval from customer refs + prior RFPs)
- C = 0.4
- F = 0.7
- G = 1.0

**Composite:** `0 + 0.15 + 0.06 + 0.07 + 0.05 = 0.33` → red on composite alone.

**Hard-rule override:** customer naming triggered. The hard-rules engine *demotes*, not promotes. Composite is already red, so it stays red, but the trigger `unapproved_reference:kestrel_logistics` is added to the metadata so the reviewer sees *why*.

**Final: RED** with `hard_rule_trigger: unapproved_reference:kestrel_logistics`

**The useful difference:** a red on composite alone says "not confident enough." A red on composite + `unapproved_reference` trigger says "even the system flagged that the customer name is not cleared." Those are different diagnostics for the SME to act on.

---

### Scenario R2 — Pricing question

Question: **"What is your per-seat pricing for 500 users?"**

Classifier returns topic `pricing`. Dispatch plan for `pricing`:
```python
DispatchPlan(
    primary=["deal_desk"],  # not wired
    force_tier=Tier.RED,
    reason="pricing_never_autogenerated",
)
```

**Everything that happens downstream is short-circuited:** retriever still runs (for audit trail), generator still runs (will produce some draft the SME can edit), scorer still runs, but **the hard-rules engine sees `force_tier: RED` in the dispatch plan and overrides the tier unconditionally.**

**Final: RED** with `hard_rule_trigger: pricing_force_tier`

**This is the most important hard rule in the system.** Pricing answers are legal/commercial decisions Deal Desk owns. Auto-generating one is a legal exposure event, not a quality issue. The system is structurally incapable of shipping a green pricing answer by design — and that's the correct design.

---

### Scenario R4 — The "cold start" red

Brand-new deployment, seeder hasn't been run. Question: **"How is customer data encrypted at rest?"**

- LibraryFeedback: **empty** → H = 0
- Compliance corpus: **empty** → no hits
- Product-docs corpus: **empty** → no hits
- CustomerRefs: **empty** → no matches
- Seismic/Gong: returns some generic phrasing via mock
- Guardrail: clean

**Signals:**
- H = 0
- R = 0.2 (weak — only tertiary source)
- C = 0.1 (one source)
- F = 0.5 (neutral)
- G = 1.0

**Composite:** `0 + 0.05 + 0.015 + 0.05 + 0.05 = 0.165`

**Final: RED** — and not just red, dramatically red.

**This is what you see on a fresh deploy before you run `scripts/seed_reference_data.py`.** Every answer looks like this. New users panic. The fix is not in the code — it's in the data. Seed the sources and the same question scores green.

This is probably the most-important scenario to internalise: **the system cannot produce green answers without seeded knowledge sources, by construction**. "Green" literally means "multiple sources corroborate this, including a prior SME approval." Empty sources → no corroboration → no green. Ever.

---

## The summary matrix you should remember

| The question is… | And sources have… | Expected tier |
|---|---|---|
| Well-covered topic (encryption, SSO) | Prior + fresh Primary + decent retrieval | **GREEN** |
| Same well-covered topic | Prior exists BUT Primary is newer | **AMBER/RED** (freshness cascade) |
| Corroboration-required topic (SOC 2, ISO) | Prior exists but no Primary in corpus | **AMBER** (capped by Rule 2) |
| Any topic | Answer names unapproved customer | **AMBER min** (or RED, if composite is red) |
| Forward-looking phrasing | Any source state | **AMBER min** |
| Pricing / commercial | Any source state | **RED** (forced) |
| Competitor comparison | Any source state | **RED** (forced) |
| New/niche topic | Nothing in any source | **RED** (weak composite) |
| Any topic | Completely unseeded system | **RED** (no H possible) |

---

## The interpretation that matters

The tier isn't really "the model's opinion." It's a calculated statement about **how well-supported the answer is by the knowledge sources and how risky the content is from a legal/commercial lens.**

Model quality barely factors in — it's bounded by the G signal (5% weight) and the guardrail check. The rest is source-state arithmetic plus policy gates. Which means:

- **To improve green rates, seed more high-quality priors into LibraryFeedback.** Every SME approval adds an H-signal match for future similar questions.
- **To reduce amber surprise, keep the Compliance and Product-docs corpora fresh and complete.** Rule 2 (corroboration required) caps tiers at amber when a Primary is missing.
- **To reduce red surprise, keep CustomerRefs current.** Expired approvals cascade into hard-rule-triggered amber/red.
- **To tune tier thresholds**, you adjust the weights in the scorer — not the model prompt. The model is doing its job if the guardrail passes.

That's the scoring mechanism.
