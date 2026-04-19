#!/usr/bin/env bash
# teardown.sh — destroy all RFP Copilot stacks.
#
# WARNING: This deletes all data in DynamoDB and S3 (except buckets with
# Object Lock — those must be emptied manually before deletion).
#
# Usage:
#   bash scripts/teardown.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA="$(cd "$SCRIPT_DIR/../infra" && pwd)"

echo "WARNING: This will destroy all RFP Copilot stacks and delete their data."
echo "Stacks: StorageStack, DataStack, OrchestrationStack, ObservabilityStack"
echo ""
read -r -p "Type 'destroy' to confirm: " CONFIRM
if [ "$CONFIRM" != "destroy" ]; then
  echo "Aborted." ; exit 0
fi

echo ""
echo "─── Destroying all stacks ───"
cd "$INFRA"
npx cdk destroy --all --force

echo ""
echo "✓ All stacks destroyed."
echo ""
echo "Note: S3 buckets with Object Lock (IncomingBucket, AuditBucket) may require"
echo "manual emptying via the AWS Console before CloudFormation can delete them."
echo "Check the console if the destroy hangs on those resources."
