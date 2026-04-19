#!/usr/bin/env bash
# reset-demo.sh — restore demo to known state in < 2 minutes.
#
# Clears all job/question/review/feedback data from DynamoDB, re-seeds the
# S3 corpus and customer references, and re-uploads the sample RFP.
# Safe to run between back-to-back demos without redeploying.
#
# Usage:
#   bash scripts/reset-demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
START=$(date +%s)

echo "=== RFP Copilot — Reset Demo ==="

# ── Resolve AWS resource names from CloudFormation ─────────────────────────
cfn_output() {
  aws cloudformation describe-stacks \
    --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" \
    --output text
}

echo "Resolving stack outputs…"
CORPUS_BUCKET=$(cfn_output "rfp-copilot-dev-storage" "ReferenceCorpusBucketName")
INCOMING_BUCKET=$(cfn_output "rfp-copilot-dev-storage" "IncomingBucketName")
JOBS_TABLE=$(cfn_output "rfp-copilot-dev-data" "JobsTableName")
QUESTIONS_TABLE=$(cfn_output "rfp-copilot-dev-data" "QuestionsTableName")
REVIEWS_TABLE=$(cfn_output "rfp-copilot-dev-data" "ReviewsTableName")
LF_TABLE=$(cfn_output "rfp-copilot-dev-data" "LibraryFeedbackTableName")
CUSTOMER_REFS_TABLE=$(cfn_output "rfp-copilot-dev-data" "CustomerRefsTableName")
echo "✓ Outputs resolved"

# ── Regenerate synthetic data ──────────────────────────────────────────────
echo ""
echo "─── Regenerating synthetic data ───"
cd "$ROOT"
python3 scripts/generate_synthetic_data.py

# ── Clear DynamoDB tables ──────────────────────────────────────────────────
echo ""
echo "─── Clearing DynamoDB tables ───"
python3 - <<EOF
import boto3

def clear_table(table_name, key_attrs):
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)
    deleted = 0
    last_key = None
    while True:
        kwargs = {"ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(key_attrs))),
                  "ExpressionAttributeNames": {f"#k{i}": k for i, k in enumerate(key_attrs)}}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        with table.batch_writer() as batch:
            for item in resp["Items"]:
                batch.delete_item(Key={k: item[k] for k in key_attrs})
                deleted += 1
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return deleted

tables = [
    ("$JOBS_TABLE",       ["jobId"]),
    ("$QUESTIONS_TABLE",  ["jobId", "questionId"]),
    ("$REVIEWS_TABLE",    ["jobId", "reviewedAt"]),
    ("$LF_TABLE",         ["answerId", "version"]),
]
for name, keys in tables:
    n = clear_table(name, keys)
    print(f"  cleared {name}: {n} items deleted")
EOF

# ── Re-seed corpus and customer refs ───────────────────────────────────────
echo ""
echo "─── Re-seeding reference corpus ───"
python3 scripts/seed_s3_corpus.py --bucket "$CORPUS_BUCKET"

echo ""
echo "─── Re-seeding customer references ───"
python3 scripts/seed_customer_refs.py --table "$CUSTOMER_REFS_TABLE"

# ── Re-upload sample RFP ──────────────────────────────────────────────────
RFP_KEY="incoming/sample_rfp_acmesec.xlsx"
echo ""
echo "─── Re-uploading sample RFP ───"
aws s3 cp "$ROOT/data/incoming/sample_rfp_acmesec.xlsx" "s3://$INCOMING_BUCKET/$RFP_KEY" --quiet
echo "✓ Uploaded s3://$INCOMING_BUCKET/$RFP_KEY"

# ── Timing ────────────────────────────────────────────────────────────────
END=$(date +%s)
ELAPSED=$((END - START))
echo ""
echo "════════════════════════════════════════════════════════════"
echo " Reset complete in ${ELAPSED}s"
echo "════════════════════════════════════════════════════════════"
SM_ARN=$(cfn_output "rfp-copilot-dev-orchestration" "StateMachineArn")
echo ""
echo " Start a fresh demo run:"
echo "   aws stepfunctions start-execution \\"
echo "     --state-machine-arn '$SM_ARN' \\"
echo "     --input '{\"bucket\":\"$INCOMING_BUCKET\",\"key\":\"$RFP_KEY\",\"jobId\":\"demo-$(date +%s)\"}'"
