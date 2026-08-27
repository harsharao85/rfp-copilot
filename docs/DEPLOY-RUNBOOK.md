# Deploy runbook — Echoworx questionnaire copilot

**For:** H, running this on a Mac against an AWS SSO session.
**Written against:** branch `work` @ `ac32b59`, 27 Aug 2026.
**Time:** ~35–45 minutes end to end on a cold account. The CDK deploy is the long pole.

This document is self-contained. You should not need to open anything else to get from a
clean AWS account to a demo you can drive. If you only read one other page, make it
`docs/DEMO-SCRIPT.md`, which is what you say once this is up.

---

## 0. Read this before you start

**Nothing in this repository has ever been deployed to real AWS from the build session.**
Every AWS endpoint was firewalled for the whole of the work that produced this commit. The
pipeline has been exercised end to end twice — once by `scripts/verify_fixture.py` and once
by `scripts/e2e_local.py`, each against a different offline retrieval stub — and both agree
on the numbers quoted in the demo script. But that is Python running on a laptop against
moto and a TF-IDF index. It is not CloudFormation, it is not Lambda, and it is not Bedrock.

The specific thing to brace for:

> **Docker Lambda bundling has never run.** `infra/lib/orchestration-stack.ts` builds all
> eleven Lambdas with `@aws-cdk/aws-lambda-python-alpha`'s `PythonFunction`, which shells out
> to `docker build` to `pip install` each function's `requirements.txt` and then copies
> `lambdas/shared/` into the bundle via an `afterBundling` command hook. Synth was only ever
> run with bundling disabled, which produces asset directories containing just `handler.py`,
> `__init__.py` and `requirements.txt` — no `shared/`, no dependencies. That is exactly what
> bundling-off is supposed to produce, so it is not evidence of a defect. It is also not
> evidence that bundling works. **Your first `cdk deploy` is the first time this code path has
> ever executed.** Budget an hour of slack the first time, and do the deploy well before you
> need the demo, not the morning of.

If bundling fails, the failure is loud and local — `cdk deploy` stops with a Docker error
before anything reaches CloudFormation. See §8.

Two more honesty notes, both small:

- `scripts/deploy-for-demo.sh`, `scripts/recycle.sh` and `scripts/reset-demo.sh` were being
  repaired by another agent while this runbook was written, and landed fixes mid-draft. The
  steps below are written as explicit commands verified against the CDK source, so they hold
  whatever state the scripts are in. Where a script now does a step for you I say so, and
  where a gap remains I name it. Re-check §6 and §9 against the scripts as you find them —
  they were moving.
- The stack set is deployed with `-c stage=poc`, giving stack names prefixed
  `rfp-copilot-poc-`. Everything below assumes that. If you deploy another stage, set
  `STAGE` in your shell and every script and stack name follows.

---

## 1. Prerequisites

| Thing | Version | Why |
|---|---|---|
| Node | **20 or newer** | CDK v2.150. `.nvmrc` pins 20. |
| Python | **3.12** | `.python-version` pins 3.12; Lambdas are `PYTHON_3_12`. |
| Docker Desktop | any current | **Required, and must be running.** Lambda bundling shells out to `docker build`. Without it the deploy stops before it starts. |
| AWS CLI | **v2** | v1 will not do SSO the way the scripts expect. |

```bash
node --version          # want v20.x or later
python3 --version       # want 3.12.x
docker info             # must succeed, not just `docker --version`
aws --version           # want aws-cli/2.x
```

`docker --version` returns successfully even when the daemon is stopped. `docker info` is the
check that matters — start Docker Desktop and wait for the whale to settle before continuing.

### Sign in

```bash
aws sso login --profile <your-profile>
export AWS_PROFILE=<your-profile>
export AWS_REGION=us-east-1

aws sts get-caller-identity
```

`us-east-1` is the decision on record (`HANDOFF.md` §2.2). Data residency was explicitly set
aside for the PoC; do not re-open it here.

The SSO session expires — typically after 8–12 hours depending on your permission set. If a
command starts failing with `ExpiredToken` partway through, re-run `aws sso login` and pick up
where you left off. Everything below is idempotent.

### Clone and position

```bash
git clone <repo-url> rfp-copilot
cd rfp-copilot
git checkout work
```

---

## 2. Bedrock model access — do this first

This is the single most common first-deploy failure, and it fails *late*: CloudFormation
deploys cleanly, the pipeline runs, and every question comes back with an empty or errored
answer, because `bedrock:InvokeModel` returns `AccessDeniedException` on a model the account
has never been granted.

Three models must be enabled in `us-east-1`:

| Model | Used by |
|---|---|
| `anthropic.claude-sonnet-4-5-20250929-v1:0` | `lambdas/generator` — answer synthesis |
| `anthropic.claude-haiku-4-5-20251001-v1:0` | `lambdas/question_classifier`, `lambdas/divergence` |
| `amazon.titan-embed-text-v2:0` | Knowledge Base embeddings (1024 dims, cosine) |

The Lambdas invoke the first two through **cross-region inference profiles** — the IDs in
`lambdas/shared/bedrock_client.py` carry a `us.` prefix (`us.anthropic.claude-sonnet-4-5-…`).
An inference profile routes to the underlying foundation model in whichever US region has
capacity, which means access must be granted for the foundation model, not only the profile.

**Check:**

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'claude-sonnet-4-5')||contains(modelId,'claude-haiku-4-5')||contains(modelId,'titan-embed-text-v2')].modelId" \
  --output table
```

That tells you the models exist in the region. It does **not** tell you your account has
access — listing is not entitlement. The direct test is to try an invoke:

```bash
aws bedrock-runtime invoke-model \
  --region us-east-1 \
  --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

A JSON completion means you are fine. `AccessDeniedException` means access is not enabled.

**Enable:** AWS console → Bedrock → *Model access* (left nav, under Configure and learn) →
*Modify model access* → tick Anthropic Claude Sonnet 4.5, Claude Haiku 4.5 and Amazon Titan
Text Embeddings V2 → Submit. Anthropic models may present a short use-case form. Grants are
usually effective within a minute or two, occasionally longer. Re-run the invoke above until
it returns a completion.

There is no CLI to grant access — it is a console action, and it is per account per region.
Do it before you deploy so you are not debugging it under time pressure.

---

## 3. CDK bootstrap

Once per account per region. Idempotent — safe to re-run.

```bash
cd infra
npm ci

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
npx cdk bootstrap "aws://$ACCOUNT/us-east-1"
```

`npm ci` takes a couple of minutes the first time. If bootstrap reports the environment is
already bootstrapped at a sufficient version, that is a success.

---

## 4. Generate the data the deploy seeds from

The corpus and the fixtures are generated, not committed as final artefacts. Run this from
the repo root before the first deploy:

```bash
cd /path/to/rfp-copilot
python3 scripts/generate_synthetic_data.py
python3 scripts/generate_corpus_documents.py
```

`generate_corpus_documents.py` writes `data/corpus-real/` — 24 documents across
`compliance/`, `product-docs/`, `prior-rfps/` and `sme-approved/`, each with a sidecar
`*.metadata.json`. That directory is what §6 uploads and what the Knowledge Base indexes.

The demo fixtures (`data/incoming/echoworx-caiq-v4-40q.xlsx` and
`echoworx-caiq-followup-5q.xlsx`) are already in the repo. If you want to regenerate them:
`python3 scripts/create_echoworx_fixture.py`.

---

## 5. Deploy — two passes, and why

The CloudFront domain does not exist until the static-site stack has been created, and the
Cognito Hosted UI needs that domain in its callback list before a browser can complete a
login from it. Wiring the two as construct references is a dependency cycle, so
`infra/bin/app.ts` takes the site origin as CDK **context** on a second pass instead. This is
deliberate and documented in `bin/app.ts` and `lib/auth-stack.ts` — it is not a workaround
you can skip.

### Pass 1 — everything, with localhost callbacks

```bash
cd infra
npx cdk deploy --all --require-approval never \
  -c stage=poc -c region=us-east-1 \
  --outputs-file outputs.json
```

Expect **10–15 minutes** on a cold account. Seven stacks deploy in dependency order:

```
rfp-copilot-poc-storage           S3 buckets + KMS CMK
rfp-copilot-poc-data              DynamoDB: Jobs, Questions, Reviews, CustomerRefs
rfp-copilot-poc-knowledge-base    S3 Vectors bucket + index, Bedrock KB, data source
rfp-copilot-poc-auth              Cognito user pool, reviewers group, Hosted UI
rfp-copilot-poc-orchestration     11 Lambdas, Step Functions, 2 HTTP APIs, Guardrail
rfp-copilot-poc-observability     Dashboards + alarms
rfp-copilot-poc-static-site       S3 + CloudFront + generated config.js
```

Two synth-time warnings are expected on this pass and neither is a failure — see §8.

Docker will be busy for the first several minutes as it builds eleven bundles. The first
build pulls the Lambda Python 3.12 build image, which is a large download.

### Capture the site URL

```bash
SITE_URL=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-poc-static-site --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text)
echo "$SITE_URL"
```

The static-site stack also emits a `CognitoCallbackContext` output whose value is literally
the argument you need for pass 2 — a convenience, not a second source of truth:

```bash
aws cloudformation describe-stacks --stack-name rfp-copilot-poc-static-site \
  --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoCallbackContext'].OutputValue" --output text
```

### Pass 2 — auth stack only, with the real origin

```bash
npx cdk deploy rfp-copilot-poc-auth --require-approval never \
  -c stage=poc -c region=us-east-1 \
  -c siteUrl="$SITE_URL"
```

About a minute. This rewrites the user pool client's callback and logout URL lists to
`$SITE_URL/`, `$SITE_URL/upload.html` and `$SITE_URL/review.html`. Nothing else in the app
reads `siteUrl`, so only this stack changes.

**Skipping pass 2 is the number-one cause of `redirect_mismatch` at the Hosted UI.** Without
it, the only registered callbacks are `http://localhost:8000/...`.

Confirm the callbacks landed:

```bash
aws cloudformation describe-stacks --stack-name rfp-copilot-poc-auth --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='CallbackUrls'].OutputValue" --output text
```

You want three CloudFront URLs, not three localhost URLs.

> **Every later redeploy must carry `-c siteUrl=…` too.** If you run
> `npx cdk deploy --all` again without it, the auth stack reverts to localhost callbacks and
> the demo stops logging in. Pin it in your shell — `export SITE_URL=…` — and always pass it.

---

## 6. Seed the corpus and wait for ingestion

```bash
cd /path/to/rfp-copilot
python3 scripts/seed_customer_refs.py
python3 scripts/seed_s3_corpus.py
```

Both resolve their targets from CloudFormation outputs when given no arguments, using the
`rfp-copilot-$STAGE` prefix (`poc` by default). Pass `--table` / `--bucket` to override.

`seed_s3_corpus.py` does three things in order: uploads `data/corpus-real/` with its
sidecars, starts a Bedrock Knowledge Base ingestion job, and **polls that job to completion**
(10-second interval, 10-minute cap). You do not need to wait separately — the script does not
return until ingestion has finished or failed. Expect **1–3 minutes** for 24 documents. It
exits non-zero if ingestion fails.

`seed_customer_refs.py` loads the reference-customer table that hard rule 3 gates on. Without
it, CAIQ-033 and CAIQ-034 both behave like unapproved references and the demo's
approved-vs-lapsed contrast collapses.

On completion, `seed_s3_corpus.py` prints a summary of the deliberately superseded SOC 2
evidence. That is the CAIQ-015/016 beat in the demo script; it is worth reading once so the
narration lands.

### The scripted path

`scripts/deploy-for-demo.sh` now wraps §3 through §7 in a single command, including both
deploy passes and the reviewer bootstrap:

```bash
bash scripts/deploy-for-demo.sh you@yourdomain.example
```

`scripts/recycle.sh up you@yourdomain.example` does the same thing with a
generate-corpus-documents step in front. Both were repaired after this runbook was drafted and
both now: deploy all stacks, read `SiteUrl` from the static-site stack, redeploy the auth
stack with `-c siteUrl=…`, run `scripts/bootstrap-reviewer.sh`, then seed customer refs and
the corpus. That matches §5–§7 above. Re-running either is safe — pass 2 against an already
correct callback list is a no-op deploy.

**One gap to know about:** both scripts still upload `data/incoming/sample_rfp_acmesec.xlsx`
— the old sales-side workbook — as their sample, and their closing summary tells you to start
the pipeline against it. The Echoworx demo runs on
`data/incoming/echoworx-caiq-v4-40q.xlsx`, uploaded through the web page. Ignore that part of
the summary and follow `docs/DEMO-SCRIPT.md`.

If you would rather see each step, run §3–§7 by hand. The explicit commands are what I
verified against the CDK source; the scripts are a convenience over them, not a different
procedure.

---

## 7. Create the reviewer user

**Self sign-up is disabled** (`auth-stack.ts`: `selfSignUpEnabled: false`), deliberately —
the `/approve` route writes SME text into the corpus that seeds future answers, and an open
pool in front of that is a security finding wearing a login page. The consequence is that a
freshly deployed account has a working Hosted UI and nobody who can sign in to it. You create
the first reviewer.

```bash
POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-poc-auth --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)

bash scripts/bootstrap-reviewer.sh "$POOL_ID" you@yourdomain.example us-east-1
```

The script is idempotent — an existing user is left alone, and re-adding a group member is a
no-op — so it is safe to re-run, and safe to run again later to add a second reviewer.

By default Cognito emails a temporary password to the address. **If email delivery is not
usable** — a throwaway demo address, a sandbox where SES is unverified — set a temporary
password instead and the invite email is suppressed:

```bash
REVIEWER_TEMP_PASSWORD='Ch00se-Something-Strong!' \
  bash scripts/bootstrap-reviewer.sh "$POOL_ID" you@yourdomain.example us-east-1
```

Password policy is 12+ characters with upper, lower, digit and symbol.

**Either way, Cognito forces a password change at first sign-in.** The Hosted UI presents that
change-password screen itself, so it works — but it is an interstitial, and you do not want to
meet it for the first time in front of the client. **Sign in once, complete the password
change, and confirm you land on `upload.html` before demo day.**

Group membership rides in the `cognito:groups` claim. No route authorises on it today, but the
script adds the user to `reviewers` anyway, which keeps the audit rows honest and leaves room
for per-route authorisation later without another deploy shape.

If you prefer to set a permanent password and skip the change-password screen entirely:

```bash
aws cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" \
  --username you@yourdomain.example --password 'Ch00se-Something-Strong!' --permanent
```

That is a convenience for a demo account only. Do not do it for anything that outlives the PoC.

---

## 8. Verification — what proves it worked

Run these in order. Each has a pass condition you can see.

### 8.1 Stacks are up

```bash
bash scripts/recycle.sh status
```

All seven stacks — including `rfp-copilot-poc-auth` — should read `CREATE_COMPLETE` or
`UPDATE_COMPLETE`. If the auth stack is not in the list your `recycle.sh` predates the fix
that added it; check it directly:

```bash
aws cloudformation describe-stacks --stack-name rfp-copilot-poc-auth \
  --region us-east-1 --query 'Stacks[0].StackStatus' --output text
```

### 8.2 The UI has a config, and it points at this account

```bash
curl -s "$SITE_URL/config.js"
```

You should get a `window.RFP_CONFIG = { … }` object holding `uploadApiUrl`, `reviewApiUrl`
and an `auth` block with `region`, `userPoolId`, `userPoolClientId` and `hostedUiUrl`. This
file is generated by the static-site stack at deploy time from that synthesis's own outputs —
there is no `config.js` in `ui/` and there should never be one committed. If any required key
is missing, `ui/auth.js` refuses to make a single API call and says so on screen rather than
failing one fetch at a time into the console.

### 8.3 The APIs reject an unauthenticated call

```bash
REVIEW_API=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-poc-orchestration --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='ReviewApiUrl'].OutputValue" --output text)

curl -s -o /dev/null -w '%{http_code}\n' "$REVIEW_API/reviews"
```

**Expect `401`.** A `200` here means the JWT authorizer is not attached and you should stop
and investigate — every route on both HTTP APIs is supposed to be behind it.

### 8.4 The Knowledge Base has vectors

```bash
KB_ID=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-poc-knowledge-base --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue" --output text)
DS_ID=$(aws cloudformation describe-stacks \
  --stack-name rfp-copilot-poc-knowledge-base --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='DataSourceId'].OutputValue" --output text)

aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" --region us-east-1 \
  --query 'ingestionJobSummaries[0].{status:status,stats:statistics}'
```

Want `status: COMPLETE` with a non-zero `numberOfDocumentsScanned` and
`numberOfNewDocumentsIndexed`. **Zero documents scanned with a COMPLETE status is the
signature of a KMS problem** — the corpus bucket is CMK-encrypted and the KB role needs
`kms:Decrypt`; the stack grants it, but if you have modified key policy, this is where it
shows up.

Then prove retrieval actually returns something:

```bash
aws bedrock-agent-runtime retrieve \
  --knowledge-base-id "$KB_ID" --region us-east-1 \
  --retrieval-query '{"text":"How is customer message content encrypted at rest?"}' \
  --query 'retrievalResults[].location.s3Location.uri'
```

Non-empty is the pass condition. If this is empty but ingestion says COMPLETE, wait a minute
and retry — the index is eventually consistent for a short window after a job finishes.

### 8.5 Sign in and drive one job

1. Open `$SITE_URL` in a browser. CloudFront serves `upload.html` as the default root object.
2. You are redirected to the Cognito Hosted UI. Sign in with the reviewer you created. The
   first sign-in includes a forced password change — complete it now, not on demo day.
3. You land back on `upload.html` with your email shown in the header.
4. Drag `data/incoming/echoworx-caiq-v4-40q.xlsx` into the drop zone.
5. Watch the status badge move `PROCESSING` → `AWAITING_REVIEW`.
6. Open `review.html`, click into the job, and confirm you see a review queue with
   **fewer items than 40** — locally it is 28, and 28 is what to expect if retrieval behaves
   the way the offline harnesses predict. Treat any number materially different from 28 as
   information about live retrieval, not as a failure; the tiering logic is deterministic but
   the retrieval scores that feed it are not.
7. Approve. Confirm the job moves `APPROVED` → `FINALIZING` → `COMPLETE` and the download
   button appears on `upload.html`.

If all of that works, you have a demo.

### 8.6 The offline checks, for a second opinion

These need no AWS at all and are worth running once on your Mac so you know what "right"
looks like before you look at live output:

```bash
cd lambdas && pip install -e . --break-system-packages && cd ..
PYTHONPATH=. python3 scripts/verify_fixture.py     # expect RESULT: PASS
python3 -m pytest lambdas/tests -q
```

`verify_fixture.py` prints the tier spread (12 green / 22 amber / 6 red across 40 questions),
the hard-rule trigger coverage, and the expired-SOC-2 handling for CAIQ-015/016.

---

## 9. Troubleshooting

### `redirect_mismatch` at the Hosted UI

The single most likely failure, and almost always the same cause: pass 2 of §5 was skipped,
or a later `cdk deploy --all` was run *without* `-c siteUrl=…` and reverted the callbacks to
localhost.

```bash
aws cloudformation describe-stacks --stack-name rfp-copilot-poc-auth --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='CallbackUrls'].OutputValue" --output text
```

If you see `http://localhost:8000/...`, re-run §5 pass 2. Fix takes about a minute.

Less common variants:

- **Trailing slash.** The registered callbacks are `<origin>/`, `<origin>/upload.html` and
  `<origin>/review.html`. Cognito matches exactly. Opening `$SITE_URL` bare works because the
  bare origin is registered on purpose. Opening a deep path that is not one of the three will
  not.
- **Stale browser session.** `ui/auth.js` keeps the ID token in `sessionStorage`, which is
  per-tab. If you are mid-debug, open a fresh tab or clear site data for the CloudFront
  domain rather than reasoning about a half-completed PKCE exchange.

### Wildcard-CORS synth warnings

On pass 1 you will see, three times, an annotation along the lines of:

> `corsOrigins defaulted to ["*"] — every HTTP API and the incoming-bucket CORS policy will
> accept any origin.`

Plus a companion warning that `siteUrl` is unset and the Cognito callbacks are localhost-only.

**Both are expected on pass 1 and neither blocks the deploy.** They are deliberate: `app.ts`
announces the wildcard loudly so it is never shipped by accident. The `siteUrl` warning
disappears once you have done pass 2.

If you want to close the CORS wildcard for the demo — reasonable, and it costs one redeploy:

```bash
npx cdk deploy --all --require-approval never \
  -c stage=poc -c region=us-east-1 \
  -c siteUrl="$SITE_URL" \
  -c corsOrigins="$SITE_URL"
```

Note `siteUrl` is carried on that command too. Doing this narrows the APIs to the CloudFront
origin, which is a better answer if anyone from Echoworx asks about the security posture of
the PoC itself. It is not required for the demo to work.

### Bedrock `AccessDeniedException` at runtime

Model access is not enabled for the account. See §2. The symptom is a pipeline that completes
but produces empty or error answers; check the generator Lambda's log group:

```bash
aws logs tail /aws/lambda/rfp-copilot-poc-orchestration-GeneratorFn --since 15m --region us-east-1
```

(Function names carry a CDK-generated suffix; `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/rfp-copilot-poc-orchestration`
will give you the exact names.)

### Docker bundling fails

Remember this path has never run before (§0). What to check, in order:

1. `docker info` succeeds — the daemon is actually up, not just installed.
2. Disk space. Eleven bundles plus the Lambda build image is several GB.
3. **Apple Silicon.** The build image is pulled for the Lambda runtime's architecture. If you
   see `exec format error` or a platform mismatch, that is the shape of the problem; CDK's
   Python bundler normally handles the emulation, but this has not been proven here.
4. Colima or Rancher Desktop instead of Docker Desktop: make sure the socket is where the CDK
   expects it (`DOCKER_HOST`).

The failure happens at synth/asset-build time, before any CloudFormation change is submitted,
so a bundling failure leaves the account untouched. Re-running after a fix is safe.

### KB ingestion latency

Ingestion takes **1–3 minutes** for the 24-document corpus. `seed_s3_corpus.py` polls to
completion so you should not normally see this as latency — but you *will* see it in the
demo's step-7 flywheel beat, where the SME's approved answer has to be re-ingested before the
follow-up run can match against it. Plan for that pause; the demo script says where to put it.

If ingestion fails, `list-ingestion-jobs` (§8.4) carries `failureReasons`. The usual causes
are the KMS decrypt grant and a malformed sidecar — the sidecars are generated, so the second
is unlikely unless you have hand-edited `data/corpus-real/`.

### Something is wedged and you want to start over

```bash
bash scripts/recycle.sh down      # destructive; requires typing 'recycle' to confirm
bash scripts/recycle.sh up you@yourdomain.example
```

`down` empties the buckets (including versions and delete markers), destroys all stacks,
deletes orphaned DynamoDB tables — they default to RETAIN — and cleans up orphaned Bedrock
knowledge bases. Read the warning it prints. **Only ever against a sandbox account.**

`up` does both deploy passes and the reviewer bootstrap, so pass the reviewer email — the user
pool is destroyed with the stack, which means the reviewer goes with it and has to be recreated.

One caveat that no script can work around: if the audit bucket ever accumulates Object-Lock'd
objects, `destroy` will fail on it and the compliance-mode retention period has to expire
before the bucket can be emptied. The script says so too.

### Between demos

```bash
bash scripts/reset-demo.sh
```

**You must run this between demos** or the flywheel beat will not reproduce. It does four
things, and the third is the one that matters:

1. Clears the Jobs, Questions and Reviews tables.
2. Re-seeds customer references.
3. **Resets the `sme-approved/` corpus prefix back to its seed documents** and re-runs KB
   ingestion. `review_api` writes roughly forty `lfb-*.md` documents into the corpus on every
   approval, and nothing else removes them — so on the second demo of the day CAIQ-038 already
   has an approved answer in the knowledge base, comes back amber on run one, and the
   red-to-amber beat the whole demo builds to silently does not happen.
4. Re-uploads the sample workbook.

It resolves every name it needs before deleting anything, so a missing stack output aborts it
harmlessly rather than halfway through. Allow about two minutes — the re-ingestion in step 3
is the bulk of it.

Note it still stages `sample_rfp_acmesec.xlsx` rather than the Echoworx fixture, which is
harmless: you upload the CAIQ workbook through the page during the demo.

---

## 10. Tear-down

```bash
bash scripts/recycle.sh down
```

Or `bash scripts/teardown.sh` for the lighter-weight version. The PoC account has a TTL of
2026-10-11 and a USD 1,500 ceiling on record; steady-state cost is dominated by S3 Vectors
(~$5/month at this scale) and is negligible when idle, but CloudFront, the Bedrock guardrail
and the KB all keep small meters running. Tear down when you are done presenting.

---

## Appendix — running the UI from localhost

Not the supported path, and not what you should demo from. Recorded because the archived
guides tell you to do it wrongly and someone will try.

Two things have to be true. First, the port must be **8000**, not 8080 — the localhost
callback registered when `siteUrl` is unset is `http://localhost:8000/...` and Cognito matches
exactly. Second, `ui/` ships **no `config.js`**; that file only exists as a deploy-time
artefact of the static-site stack. Serving `ui/` locally gives you pages that immediately
report a missing configuration.

If you genuinely need it, fetch the deployed config and put it beside the pages:

```bash
curl -s "$SITE_URL/config.js" > ui/config.js
cd ui && python3 -m http.server 8000
```

Then open `http://localhost:8000/upload.html`. This only works if the auth stack still has
its localhost callbacks — that is, if you have not yet run §5 pass 2, or you deploy the auth
stack again without `-c siteUrl`. It is not worth juggling for a demo. Use CloudFront.

`ui/config.js` must never be committed.
