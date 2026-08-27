#!/usr/bin/env bash
# deploy-for-demo.sh — full deploy + seed for the RFP Copilot demo.
#
# Idempotent: safe to re-run after a failed deployment.
# Prereqs: node >= 20, python3 >= 3.12, aws CLI v2, Docker Desktop running.
#
# The deploy is two passes on purpose. The Cognito callback URLs have to name
# the CloudFront domain, and that domain does not exist until the static-site
# stack has been created — while the static site needs the Cognito identifiers
# for its generated config.js. References both ways is a cycle, so pass 2
# redeploys only the auth stack with `-c siteUrl=https://<domain>` read from
# pass 1. Without it the callbacks stay on localhost and every login from
# CloudFront dies on `redirect_mismatch`.
#
# Self sign-up is off, so a fresh account also has nobody who can log in.
# Pass the first reviewer's email and this script creates them.
#
# Usage:
#   cd rfp-copilot
#   bash scripts/deploy-for-demo.sh reviewer@example.com
#   REVIEWER_EMAIL=reviewer@example.com bash scripts/deploy-for-demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA="$ROOT/infra"
# Stage must match the `stage` CDK context the stacks are deployed with.
STAGE="${STAGE:-poc}"
export STAGE
PREFIX="rfp-copilot-$STAGE"
# First reviewer, as an argument or an env var. Empty means "skip the
# bootstrap and tell the operator how to run it later" — a deploy is still
# useful without it, but a login is not possible until someone runs it.
REVIEWER_EMAIL="${1:-${REVIEWER_EMAIL:-}}"

echo "=== RFP Copilot — Deploy for Demo ==="

# ── Prereqs ──────────────────────────────────────────────────────────────────
check() {
  if ! command -v "$1" &>/dev/null; then
    echo "ERROR: $1 not found. $2" >&2; exit 1
  fi
}
check node  "Install Node.js >= 20 from https://nodejs.org"
check python3 "Install Python >= 3.12"
check aws   "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
check docker "Start Docker Desktop before deploying (needed for Lambda bundling)"

NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VER" -lt 20 ]; then
  echo "ERROR: Node.js >= 20 required (found v$NODE_VER)" >&2; exit 1
fi

echo "✓ Prereqs OK (node $(node --version), $(python3 --version), $(aws --version 2>&1 | head -1))"

# ── AWS identity ─────────────────────────────────────────────────────────────
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}}"
echo "✓ AWS account: $ACCOUNT  region: $REGION"

# ── Generate synthetic data ───────────────────────────────────────────────────
echo ""
echo "─── Generating synthetic data ───"
cd "$ROOT"
python3 scripts/generate_synthetic_data.py

# ── CDK install + bootstrap ───────────────────────────────────────────────────
echo ""
echo "─── Installing CDK dependencies ───"
cd "$INFRA"
npm ci --silent

echo "─── Bootstrapping CDK (idempotent) ───"
npx cdk bootstrap "aws://$ACCOUNT/$REGION" 2>&1 | grep -v "^$" || true

# ── Deploy, pass 1 of 2 ───────────────────────────────────────────────────────
echo ""
echo "─── Pass 1/2: deploying all stacks (~10–15 min on first deploy) ───"
npx cdk deploy --all --require-approval never \
  -c "stage=$STAGE" -c "region=$REGION" \
  --outputs-file "$ROOT/infra/outputs.json"

echo "✓ Pass 1 complete"

# ── Resolve outputs ───────────────────────────────────────────────────────────
cfn_output() {
  aws cloudformation describe-stacks \
    --stack-name "$1" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" \
    --output text 2>/dev/null || true
}

require_output() {
  local value
  value=$(cfn_output "$1" "$2")
  if [ -z "$value" ] || [ "$value" = "None" ]; then
    echo "ERROR: stack '$1' has no output '$2' — pass 1 did not complete." >&2
    exit 1
  fi
  printf '%s' "$value"
}

CORPUS_BUCKET=$(require_output "$PREFIX-storage" "ReferenceCorpusBucketName")
INCOMING_BUCKET=$(require_output "$PREFIX-storage" "IncomingBucketName")
CUSTOMER_REFS_TABLE=$(require_output "$PREFIX-data" "CustomerRefsTableName")
REVIEW_API_URL=$(require_output "$PREFIX-orchestration" "ReviewApiUrl")
SM_ARN=$(require_output "$PREFIX-orchestration" "StateMachineArn")
SITE_URL=$(require_output "$PREFIX-static-site" "SiteUrl")
USER_POOL_ID=$(require_output "$PREFIX-auth" "UserPoolId")

# ── Deploy, pass 2 of 2 ───────────────────────────────────────────────────────
# Only the auth stack reads siteUrl. Re-running this against an already-correct
# callback list is a no-op deploy, so the whole script stays re-runnable.
echo ""
echo "─── Pass 2/2: registering $SITE_URL as a Cognito callback ───"
cd "$INFRA"
npx cdk deploy "$PREFIX-auth" --require-approval never \
  -c "stage=$STAGE" -c "region=$REGION" -c "siteUrl=$SITE_URL"
echo "✓ Cognito callbacks now point at $SITE_URL"

# ── Bootstrap the first reviewer ──────────────────────────────────────────────
# Self sign-up is disabled on the pool, so without this nobody can log in.
echo ""
echo "─── Reviewer bootstrap ───"
if [ -n "$REVIEWER_EMAIL" ]; then
  bash "$SCRIPT_DIR/bootstrap-reviewer.sh" "$USER_POOL_ID" "$REVIEWER_EMAIL" "$REGION"
else
  echo "  no reviewer email given — NOBODY CAN LOG IN YET."
  echo "  Run: bash scripts/bootstrap-reviewer.sh $USER_POOL_ID <email> $REGION"
fi

# ── Seed reference corpus ──────────────────────────────────────────────────────
echo ""
echo "─── Seeding reference corpus into s3://$CORPUS_BUCKET/ ───"
cd "$ROOT"
python3 scripts/seed_s3_corpus.py --bucket "$CORPUS_BUCKET" --stack-prefix "$PREFIX"

# ── Seed customer references ───────────────────────────────────────────────────
echo ""
echo "─── Seeding customer references ───"
python3 scripts/seed_customer_refs.py --table "$CUSTOMER_REFS_TABLE"

# ── Upload sample incoming RFP ────────────────────────────────────────────────
RFP_SRC="$ROOT/data/incoming/sample_rfp_acmesec.xlsx"
RFP_KEY="incoming/sample_rfp_acmesec.xlsx"
echo ""
echo "─── Uploading sample RFP to s3://$INCOMING_BUCKET/$RFP_KEY ───"
aws s3 cp "$RFP_SRC" "s3://$INCOMING_BUCKET/$RFP_KEY"
echo "✓ Uploaded"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo " DEMO READY"
echo "════════════════════════════════════════════════════════════"
echo " SITE URL (open this): $SITE_URL"
echo " API URL:              $REVIEW_API_URL"
echo " State machine:        $SM_ARN"
echo " Cognito user pool:    $USER_POOL_ID"
if [ -n "$REVIEWER_EMAIL" ]; then
  echo " Reviewer:             $REVIEWER_EMAIL (temporary password emailed by Cognito)"
else
  echo " Reviewer:             NONE — run scripts/bootstrap-reviewer.sh before demoing"
fi
echo ""
echo " Demo flow:"
echo "   1. Open $SITE_URL and sign in as the reviewer."
echo "      Endpoints come from the deploy-time config.js — nothing to paste,"
echo "      and the callback URLs already name this domain (pass 2 above)."
echo "   2. Upload data/incoming/sample_rfp_acmesec.xlsx, or start the pipeline"
echo "      against the copy already in S3:"
echo "      aws stepfunctions start-execution \\"
echo "        --state-machine-arn '$SM_ARN' \\"
echo "        --input '{\"bucket\":\"$INCOMING_BUCKET\",\"key\":\"$RFP_KEY\",\"jobId\":\"demo-001\"}'"
echo "   3. Review the amber/red answers at $SITE_URL/review.html and approve."
echo "      Approved answers are written to the sme-approved/ corpus and"
echo "      ingested into the knowledge base, so the next run answers better."
echo "   4. Download the final workbook from the upload page."
echo ""
echo " To reset between demos: bash scripts/reset-demo.sh"
echo " To tear down:           bash scripts/teardown.sh"
echo "════════════════════════════════════════════════════════════"
