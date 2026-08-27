# Demo script — Echoworx questionnaire copilot

**For:** H, presenting to Echoworx.
**Runs on:** the CloudFront site deployed per `docs/DEPLOY-RUNBOOK.md`.
**Length:** 12–15 minutes for all seven beats. Beats 1–5 are the demo; 6 and 7 are the
argument for why it is worth buying.

Seven beats, one per step of the workflow. The workbook is
`data/incoming/echoworx-caiq-v4-40q.xlsx` — 40 questions drawn from CSA CAIQ v4.0.3, in the
sections Echoworx actually receives.

---

## Before you start

- [ ] `bash scripts/reset-demo.sh`, **finished** — not optional, and it takes about two
      minutes because it re-runs KB ingestion. See "Reset between demos" below.
- [ ] Signed in at `$SITE_URL` already, in the tab you will present from. Do not do the
      Cognito redirect on camera unless you want to talk about auth; if you do, it is a
      thirty-second beat and a good one.
- [ ] `data/incoming/echoworx-caiq-v4-40q.xlsx` and `echoworx-caiq-followup-5q.xlsx` open in
      Finder, ready to drag.
- [ ] A terminal open, sized for screen-share, with `AWS_PROFILE` and `AWS_REGION` set.
- [ ] Know your job ID pattern: the upload page shows it, and every terminal command below
      keys off it.

One presentation note that will save you: `ui/auth.js` keeps the ID token in
`sessionStorage`, which is scoped per browser tab. A second tab is a second sign-in. Cognito
will usually bounce you straight through on its own session cookie, but it is a visible
redirect flash. Drive the whole demo from one tab, or open and sign in to both tabs before
you share your screen.

---

## Beat 1 — Sign in and upload

**Step 1 of the workflow: a questionnaire arrives.**

Drag `echoworx-caiq-v4-40q.xlsx` onto the drop zone. Watch the badge move through
`Requesting upload URL…` → `Uploading…` → `PROCESSING`.

> "This is a real CAIQ v4 workbook — forty controls across cryptography, audit and assurance,
> identity, commercial terms, customer references. The kind of thing that lands in a security
> team's inbox and consumes a senior engineer for two days.
>
> Nothing is public. Every route on both APIs is behind a Cognito authorizer, and my identity
> is on the request. That matters more than it sounds like it does, and you'll see why in
> beat six."

**What to point at:** your email, top right. It came from the ID token, not from a text box.

---

## Beat 2 — The work record exists before any model has run

**Step 2: the job is a tracked artefact from the first byte.**

Switch to the terminal. Substitute the job ID from the page:

```bash
JOBS=$(aws cloudformation describe-stacks --stack-name rfp-copilot-poc-data \
  --region us-east-1 --query "Stacks[0].Outputs[?OutputKey=='JobsTableName'].OutputValue" \
  --output text)

aws dynamodb get-item --table-name "$JOBS" \
  --key '{"jobId":{"S":"<job-id>"}}' \
  --query 'Item.{status:sessionStatus.S,created:createdAt.S,edits:sme_edit_count.N}'
```

> "The work record was created at the moment the upload URL was issued — before a single byte
> of the workbook existed in S3, and long before any model saw a question. Session status
> `CREATED`, then `UPLOADED`, then `PROCESSING`. There is never a window where work is
> happening that the system cannot account for.
>
> For a compliance function that is the whole ball game. If you cannot say what state a
> submission was in at a given time, you cannot answer the question your own auditor is going
> to ask."

**Timing:** the pipeline takes a minute or two on 40 questions. This beat is what you do while
it runs. If it finishes early, that is fine — move on.

---

## Beat 3 — The colour-coded workbook, with citations

**Step 3 and 4: every question answered, ranked, and quoting its sources.**

Go to `review.html`. Open the job.

> "Forty questions, forty answers, and a tier on each one: **twelve green, twenty-two amber,
> six red.**
>
> Green is 'a rep can send this'. Amber is 'an SME must look before it ships'. Red is 'this
> does not leave the building without compliance or commercial sign-off'.
>
> The number that matters isn't the twelve. It's that the system tells you which twelve —
> and it doesn't decide that with a model. The tier comes from a deterministic scorer over
> five signals and a deterministic rules engine on top of it. No LLM ever assigns a colour.
> You can read the arithmetic, and so can your auditor."

Open any answer and show the citation block underneath it.

> "Every answer names the documents it came from. Not a similarity score — the actual source
> document, from your corpus."

**The five signals**, if asked: H is match against prior approved answers, R is retrieval
strength, C is coverage of the question, F is source freshness, G is guardrail pass. Weights
are 0.45 / 0.25 / 0.15 / 0.10 / 0.05, in `lambdas/confidence_scorer/scorer.py`.

**One thing to know before someone asks.** In the downloaded workbook, the cell comment on a
green answer shows `Confidence: 0.95` while its own H/R/C/F/G breakdown multiplies out to
something much lower. That is deliberate, not a bug: for questions the classifier resolved to
a known topic class, the tier is class-driven and the composite is audit-only, so the comment
shows a representative confidence for the tier rather than a composite that would look
confusingly low next to a GREEN badge. For unclassified questions — where the composite *is*
the decision — the real number is shown verbatim. It is in `lambdas/hard_rules/handler.py`
with the reasoning in a comment. If someone spots it, that is a good conversation, not an
awkward one.

---

## Beat 4 — A clean answer, and an answer that is not clean

**Still step 4 — this is the beat that separates this from a chatbot.**

First, **CAIQ-001**: *"How is customer message content encrypted at rest? State the algorithm,
the mode of operation, and the key length."* Green.

> "Straightforward control. There's a product document that answers it exactly, the retrieval
> is strong, the source is current, and the answer cites it. Green: send it."

Now scroll to **CAIQ-015** and **CAIQ-016**, both in *C. Audit and Assurance*, both amber.

- CAIQ-015: *"The SOC 2 Type II report we hold on file is dated 2024. Is that report still
  current evidence of your control environment?"*
- CAIQ-016: *"State the report period and issue date of your current SOC 2 Type II
  attestation, and confirm which earlier reports it supersedes."*

> "Two SOC 2 reports are in the corpus. One is current. One expired in June 2025 and is still
> sitting there, because in a real evidence library expired documents don't delete themselves.
>
> Look at the citation on this one: it ends in **(EXPIRED)**. The freshness signal on that
> document scores 0.2 against the current report's 1.0. And — this is the part I'd flag — on
> a question that asks about the 2024 report *by date*, the expired report legitimately ranks
> first, because it is the most relevant document to that question. That is correct retrieval
> behaviour.
>
> What stops it shipping isn't a filter. It's that the answer is amber, the citation is
> marked expired, and a human sees both before anything goes out. We didn't build a rule that
> hides stale evidence. We built a system that cannot ship stale evidence without a person
> looking at it. Those are very different guarantees, and only the second one survives contact
> with a real corpus."

This is the strongest ninety seconds in the demo for a security audience. Don't rush it.

---

## Beat 5 — The review queue, and the rules that put things in it

**Step 5: the human sees the uncertain items, not all of them.**

> "Forty questions came in. **Twenty-eight are in the review queue.** The other twelve are
> green and a rep handles them. That ratio is the product: an SME's afternoon instead of an
> SME's week."

Then show two specific rows.

**CAIQ-031** — *"What is your per-seat cost for the Enterprise tier at our headcount?"* — RED,
triggers `pricing_detected` and `pricing_never_autogenerated`.

> "This one never went near the retrieval layer. Pricing is a gated topic class: the
> classifier forces red at dispatch, retrieval is skipped entirely, and what you're reading is
> a deflection, not a generated answer. The system is structurally incapable of inventing a
> price. Not 'instructed not to' — incapable. For anyone whose job is commercial exposure,
> that's the difference between a policy and a control."

**CAIQ-034** — *"Can Helix Reinsurance act as a client reference for a deployment of
comparable scale?"* — flagged `unapproved_reference:Helix Reinsurance`.

> "Helix Reinsurance's reference approval has lapsed. The rules engine caught it by name and
> flagged it. Compare it with CAIQ-033, the question about Northwind Mutual — Northwind's
> approval is current and public, so that trigger doesn't fire there. Same question shape,
> different governance outcome, decided by a table you control rather than by a model's
> judgment."

**Be precise here:** CAIQ-033 and CAIQ-034 are *both* red. The observable difference is the
trigger list, not the colour. Say "the flag fires on one and not the other" — if you say
"this one's amber and that one's red", the screen will contradict you. (The reason: for gated
topic classes the tier is composite-derived, and the customer-reference dispatch plan returns
no passages, so neither can reach the amber threshold. It is documented in
`data/incoming/echoworx-caiq-v4-40q.expected.json` under `divergences_from_brief`.)

---

## Beat 6 — Edit, approve, and the audit trail

**Step 6: the human's answer is the answer that ships.**

Find **CAIQ-038**: *"How does the service integrate with unified endpoint management tooling
for corporate-managed mobile devices?"* Red.

> "Nothing in the corpus answers this. The system says so rather than guessing — red, low
> confidence, weak retrieval. That's the behaviour you want on a question you have no
> evidence for."

Type a real answer into the box. Something like:

> *Corporate-managed mobile devices are enrolled in the tenant's own UEM platform; the
> application is distributed through the managed app catalogue and honours per-app VPN and
> managed-configuration policy pushed by the UEM.*

Click **Approve & Resume**.

Then, in the terminal:

```bash
REVIEWS=$(aws cloudformation describe-stacks --stack-name rfp-copilot-poc-data \
  --region us-east-1 --query "Stacks[0].Outputs[?OutputKey=='ReviewsTableName'].OutputValue" \
  --output text)

aws dynamodb query --table-name "$REVIEWS" \
  --key-condition-expression 'jobId = :j' \
  --expression-attribute-values '{":j":{"S":"<job-id>"}}' \
  --query 'Items[].{step:step.S,actor:actor.S,at:reviewedAt.S}' --output table
```

> "Seven rows. `CREATED`, `UPLOADED`, `PROCESSING`, `AWAITING_REVIEW`, `IN_REVIEW`,
> `APPROVED`, `COMPLETE` — every transition, in order, with a timestamp. The `APPROVED` row
> carries my email address, taken from the JWT, and records which questions I edited.
>
> That table is append-only and write-only. Nothing in the pipeline reads it back. An audit
> trail a handler can rewrite isn't an audit trail."

Now go back to `upload.html` and click **Download**.

> "And this is the workbook that goes back to the prospect — with my text in the CAIQ-038
> cell, not the machine's."

**This is a real fix, and worth a sentence.** The pipeline writes the workbook twice: a draft
before review and a final pass after approval. An earlier build handed the reviewer the draft
— their edits were persisted but never reached the file. The download button doesn't appear
at all until the final pass has written; the job status goes `APPROVED` → `FINALIZING` →
`COMPLETE`, and only `COMPLETE` is downloadable.

---

## Beat 7 — The flywheel

**Step 7: the answered questionnaire makes the next one cheaper.**

> "Every approved answer — all forty, green ones included — was just written back into the
> evidence corpus as a first-class document, with a sidecar recording that a human approved
> it, who, when, and which job it came from.
>
> Which means the next questionnaire that asks this question is not starting from zero."

**Wait for ingestion.** Re-indexing the approved answers takes **1–3 minutes**. Put a natural
pause here — this is the moment to take architecture questions, or to talk about what a real
corpus of Echoworx's own historical questionnaires would do to the green count. Don't stand
in silence watching a spinner.

Then drag `echoworx-caiq-followup-5q.xlsx` onto the drop zone. Five questions, one of which
is CAIQ-038 re-asked verbatim.

Open the review view.

> "CAIQ-038 was red on the first run. It's **amber** now.
>
> The prior-answer signal on that question went from **0.33 to 0.60** — the system found a
> near-exact match for this control, because the near-exact match is the answer I wrote
> twenty minutes ago. The composite went from 0.47 to 0.59 and crossed the amber threshold.
> One human answer, one question moved out of 'no idea' and into 'a human should confirm
> this'.
>
> The other four are controls: a green one, two ambers, a red. **None of them moved.** The
> flywheel turned exactly one wheel, which is what you want — a system that quietly re-tiers
> unrelated controls after every review is a system nobody will trust twice."

### Two things to say accurately, not optimistically

**It goes to amber, not to green — and say so before anyone asks.**

`confidence_scorer/scorer.py` caps every *unclassified* question at amber regardless of
composite, and an SME-approved answer is not a primary authoritative source in its own right.
A human's answer to a question with no supporting evidence cannot make that question green by
itself. That is a design decision, and it is a good one:

> "It stops at amber, and it's supposed to. One SME answering once doesn't turn an
> unevidenced control into a shippable one — it turns it from 'we have nothing' into 'we have
> a human's answer that another human should confirm'. Green requires an authoritative
> source. If one approval could mint green answers, the flywheel would be a way of laundering
> a single opinion into forty documents."

If the script said "it goes green", the screen would contradict you. It says amber.

**Do not promise that it cites your own answer.**

The retriever returns the prior SME answer as a *prior match*, which feeds the H signal, and
it is put in front of the model as context — but only *passages* produce citation brackets,
and prior matches are not passages. Verified in both offline harnesses: on the second run the
citation list on CAIQ-038 names other corpus documents, not the SME's answer.

So narrate the signal and the tier, which are real and on screen, and not the citation, which
is not:

> "The system now holds a human-approved answer for this control, and you can see the
> confidence move because of it."

Do **not** say "and look, it's citing the answer you just wrote." It will not be.

---

## If something breaks live

**The pipeline stalls or a job sits in `PROCESSING`.**

> "Let me show you the state machine while that runs — this is the part that makes it
> auditable anyway."

Open the Step Functions console execution graph. It is a genuinely good visual and it buys
you two minutes. If it has actually failed, the execution shows you where, and the honest
line is *"that's a Bedrock throttle / a cold start — the pipeline is resumable from the gate,
which is the point of parking on a task token rather than holding a Lambda open."* Then move
to beat 6 using the job from your rehearsal run.

**The Hosted UI throws `redirect_mismatch`.**

Almost always means the auth stack was redeployed without `-c siteUrl=…`. You cannot fix it
in under a minute live. Fall back to a workbook you downloaded during rehearsal and narrate
beats 3–6 from the file — the colour coding, cell comments and citations are all in it. Fix
it afterwards per `docs/DEPLOY-RUNBOOK.md` §9.

**Have a rehearsed job ID in your notes.** A completed job from an earlier run is the single
best insurance policy in this demo: every terminal command in beats 2 and 6 works against it,
and the review view will still render it.

---

## Reset between demos

```bash
bash scripts/reset-demo.sh
```

**Run this between every demo, and allow about two minutes.** Beat 7 depends on CAIQ-038
having no approved answer in the corpus at the start. Approving once puts roughly forty
`lfb-*.md` documents into the `sme-approved/` prefix, and nothing else removes them — so on
your second demo of the day CAIQ-038 comes back **amber on run one**, and beat 7 shows a
question going from amber to amber. Which demonstrates nothing, in front of the client,
after you have spent ten minutes building to it.

`reset-demo.sh` clears the job, question and review tables, resets that prefix back to its
seed documents, and re-runs Knowledge Base ingestion. The re-ingestion is most of the two
minutes. Do not start a demo while it is still running.

---

## Where the numbers come from

Everything quoted above is traceable, and nothing is estimated:

| Claim | Source |
|---|---|
| 12 green / 22 amber / 6 red across 40 | `data/incoming/echoworx-caiq-v4-40q.expected.json` → `expected_tier_counts`; reproduced exactly by both offline harnesses |
| 28 items in the review queue | `scripts/e2e_local.py`, "reviewable questions returned — 28" |
| CAIQ-031 triggers, CAIQ-034 trigger | `expected.json` per-question `expected_triggers`; verified by `scripts/verify_fixture.py` trigger coverage |
| CAIQ-038 red → amber, H 0.33 → 0.60, composite 0.47 → 0.59 | `scripts/e2e_local.py` run-one and run-two score events |
| Citations on run two do not name the SME answer | `scripts/e2e_local.py` — this check fails, under both stubs, by design of the retriever |
| SOC 2 freshness F=0.2 vs 1.0, `(EXPIRED)` citation suffix | `scripts/verify_fixture.py` §5 |

**Caveat worth carrying in your head:** those figures come from two independent *offline*
harnesses — `verify_fixture.py` uses a cosine cutoff of 0.045, `e2e_local.py` uses none — and
they agree on the tier spread, the queue size and the CAIQ-038 movement. They are not
measurements from live Bedrock. The tiering logic is deterministic and will behave
identically; the retrieval scores that feed it will not be bit-identical against a real
Knowledge Base. Expect the spread to be close. If it is materially different on the day,
that is information about live retrieval, not a broken demo — and 12/22/6 is what the fixture
was engineered to produce, so a large drift is worth investigating before you present.
