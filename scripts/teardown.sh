#!/usr/bin/env bash
# teardown.sh — destroy all RFP Copilot stacks.
#
# WARNING: This deletes all data in DynamoDB and S3 (except buckets with
# Object Lock — those must be emptied manually before deletion).
#
# Usage:
#   bash scripts/teardown.sh
#   STAGE=dev bash scripts/teardown.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA="$(cd "$SCRIPT_DIR/../infra" && pwd)"

# `cdk destroy` synthesises the app before it destroys anything, so it needs
# the same context the deploy was given. Without `-c stage=`, a non-default
# stage synthesises as `rfp-copilot-poc-*` and destroys the wrong stacks — or,
# more often, nothing at all, while reporting success.
STAGE="${STAGE:-poc}"
export STAGE
PREFIX="rfp-copilot-$STAGE"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}}"

echo "WARNING: This will destroy all RFP Copilot stacks and delete their data."
echo "Stage: $STAGE   Region: $REGION"
echo "Stacks: $PREFIX-{storage,data,knowledge-base,auth,orchestration,observability,static-site}"
echo ""
read -r -p "Type 'destroy' to confirm: " CONFIRM
if [ "$CONFIRM" != "destroy" ]; then
  echo "Aborted." ; exit 0
fi

echo ""
echo "─── Destroying all stacks ───"
cd "$INFRA"
npx cdk destroy --all --force -c "stage=$STAGE" -c "region=$REGION"

echo ""
echo "✓ All stacks destroyed."
echo ""
echo "Note: S3 buckets with Object Lock (IncomingBucket, AuditBucket) may require"
echo "manual emptying via the AWS Console before CloudFormation can delete them."
echo "Check the console if the destroy hangs on those resources."
