#!/usr/bin/env bash
# deploy-for-demo.sh — full deploy + seed for the RFP Copilot demo.
#
# Idempotent: safe to re-run after a failed deployment.
# Prereqs: node >= 20, python3 >= 3.12, aws CLI v2, Docker Desktop running.
#
# Usage:
#   cd rfp-copilot
#   bash scripts/deploy-for-demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA="$ROOT/infra"

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
REGION=$(aws configure get region || echo "us-east-1")
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

# ── Deploy all stacks ─────────────────────────────────────────────────────────
echo ""
echo "─── Deploying all stacks (this takes ~10–15 min on first deploy) ───"
npx cdk deploy --all --require-approval never --outputs-file "$ROOT/infra/outputs.json"

echo "✓ Deploy complete"

# ── Resolve outputs ───────────────────────────────────────────────────────────
cfn_output() {
  aws cloudformation describe-stacks \
    --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" \
    --output text
}

CORPUS_BUCKET=$(cfn_output "rfp-copilot-dev-storage" "ReferenceCorpusBucketName")
INCOMING_BUCKET=$(cfn_output "rfp-copilot-dev-storage" "IncomingBucketName")
CUSTOMER_REFS_TABLE=$(cfn_output "rfp-copilot-dev-data" "CustomerRefsTableName")
REVIEW_API_URL=$(cfn_output "rfp-copilot-dev-orchestration" "ReviewApiUrl")
MOCK_API_URL=$(cfn_output "rfp-copilot-dev-orchestration" "MockSourcesApiUrl")
SM_ARN=$(cfn_output "rfp-copilot-dev-orchestration" "StateMachineArn")

# ── Seed reference corpus ──────────────────────────────────────────────────────
echo ""
echo "─── Seeding reference corpus into s3://$CORPUS_BUCKET/ ───"
cd "$ROOT"
python3 scripts/seed_s3_corpus.py --bucket "$CORPUS_BUCKET"

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
echo " Review UI:        open ui/review.html in your browser"
echo " API URL:          $REVIEW_API_URL"
echo " Mock sources URL: $MOCK_API_URL"
echo " State machine:    $SM_ARN"
echo ""
echo " Demo flow:"
echo "   1. Run the pipeline:"
echo "      aws stepfunctions start-execution \\"
echo "        --state-machine-arn '$SM_ARN' \\"
echo "        --input '{\"bucket\":\"$INCOMING_BUCKET\",\"key\":\"$RFP_KEY\",\"jobId\":\"demo-001\"}'"
echo ""
echo "   2. Open ui/review.html, set API URL to: $REVIEW_API_URL"
echo "   3. Approve answers — flywheel writes to LibraryFeedback."
echo "   4. Click ⟳ Staleness to show the daemon flagging stale answers."
echo ""
echo " To reset between demos: bash scripts/reset-demo.sh"
echo " To tear down:           bash scripts/teardown.sh"
echo "════════════════════════════════════════════════════════════"
