# Claude Code Delegation — Test Guide Fixes + Demo Script

Three fixes to `docs/test-guide.md`, then write the demo script (Task 5 from the delegation doc).

---

## Fix 1: DynamoDB table names in §5a, §5c, §5d

The commands hardcode `rfp-copilot-dev-data-JobsTable` and `rfp-copilot-dev-data-QuestionsTable`, but deployed table names have CloudFormation suffixes. Add a lookup block at the top of §5 (before §5a) that fetches both names, matching the pattern already used in §6b for LibraryFeedbackTable:

```bash
JOBS_TABLE=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-dev-data \
  --query 'Stacks[0].Outputs[?OutputKey==`JobsTableName`].OutputValue' \
  --output text)

QUESTIONS_TABLE=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-dev-data \
  --query 'Stacks[0].Outputs[?OutputKey==`QuestionsTableName`].OutputValue' \
  --output text)
```

Then replace every `rfp-copilot-dev-data-JobsTable` with `$JOBS_TABLE` and every `rfp-copilot-dev-data-QuestionsTable` with `$QUESTIONS_TABLE` in §5a, §5c, §5d, and §8 (the fallback CLI download also references JobsTable).

## Fix 2: Misleading variable name in §6a

Rename `EXEC_ARN` to `SM_ARN` in §6a. It holds the state machine ARN, not an execution ARN. The old guide used `EXEC_ARN` for execution ARNs, so reusing the name here would confuse someone running both docs.

## Fix 3: Explicit auto-load check in §3

After step 7 ("Confirm the Recent Jobs row..."), add step 8:

> 8. Confirm the review UI auto-loads the job's questions without requiring you to click Load or re-enter the API URL. The `?api=...&job=...` query params should populate both fields and trigger the fetch automatically.

---

## Task 5: Write `docs/demo-script.md`

The test guide is the verification document — what to click, what to check. The demo script is the presentation document — what to say, in what order, with transitions. Write `docs/demo-script.md` following the structure from `docs/claude-code-delegation-phase-ui.md` Task 5:

### Structure

```
# RFP Copilot — Live Demo Script

## Pre-flight checklist
## Act 1: Upload & Pipeline (2 min)
## Act 2: SME Review with Citations (3 min)
## Act 3: Governance Deep-Dive (3 min)
## Act 4: Flywheel & Self-Cleaning (2 min)
## Teardown / Reset
## Appendix: Talking Points for Q&A
```

### Requirements

- Each Act has numbered steps with **actions** (what to click/show) and **talk track** (what to say, in quotes or italics).
- Cover all 12 demo moments in order. Refer to `docs/claude-code-delegation-phase-ui.md` Task 5 for the exact list and suggested talk tracks per moment.
- The Appendix must include prepared answers for these 5 questions:
  1. "Why not use agents/AgentCore?"
  2. "How does this scale?"
  3. "What about Glean integration?"
  4. "Cost?"
  5. "Why Step Functions over EventBridge Pipes?"
- Keep the total script under 500 lines. It should be scannable during a live demo, not a wall of text.
- Do NOT duplicate CLI verification commands from test-guide.md. The demo script references test-guide.md for backend checks; it focuses on what the audience sees and hears.

### Verification

After writing demo-script.md, confirm every demo moment from the delegation doc's Task 5 list appears in the script. If any moment is missing, add it before marking done.
