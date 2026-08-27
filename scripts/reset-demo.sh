#!/usr/bin/env bash
# reset-demo.sh — restore the demo to a known state in < 2 minutes.
#
# Clears job/question/review data from DynamoDB, resets the SME-approved
# corpus back to its seed documents, re-seeds the reference corpus and
# customer references, and re-uploads the sample RFP. Safe to run between
# back-to-back demos without redeploying, and safe to run twice.
#
# Two things this script exists to guarantee, both of which used to fail:
#
#   1. It resolves every name it needs BEFORE it deletes anything. The
#      previous version looked up an output that no longer exists
#      (LibraryFeedbackTableName, removed in Phase F), got an empty string,
#      and under `set -e` aborted on the resulting API error — after emptying
#      the Jobs, Questions and Reviews tables and before re-seeding anything.
#      That left the environment worse than it found it.
#
#   2. It resets the flywheel. review_api writes ~40 sme-approved/lfb-*.md
#      documents into the corpus bucket on every approval, and nothing else
#      removes them. On the second demo of the day CAIQ-038 already has an SME
#      answer in the knowledge base, so it comes back AMBER on run one and the
#      red -> amber beat — the thing the whole demo builds to — silently does
#      not happen. Clearing the prefix back to the seeds and re-running
#      ingestion makes that beat repeatable.
#
# Usage:
#   bash scripts/reset-demo.sh
#   STAGE=dev bash scripts/reset-demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
START=$(date +%s)

# Stage must match the `stage` CDK context the stacks were deployed with.
STAGE="${STAGE:-poc}"
export STAGE
PREFIX="rfp-copilot-$STAGE"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}}"
# Every `aws` call below is pinned with --region "$REGION". Pin the boto3 calls
# in the helper scripts to the same value, so the region this script deletes
# from and the region it re-seeds into cannot diverge.
export AWS_DEFAULT_REGION="$REGION"

SEED_DIR="$ROOT/data/corpus-real/sme-approved"
RFP_SRC="$ROOT/data/incoming/sample_rfp_acmesec.xlsx"
RFP_KEY="incoming/sample_rfp_acmesec.xlsx"

echo "=== RFP Copilot — Reset Demo ==="
echo "Stage: $STAGE   Region: $REGION"

die() { echo "ERROR: $*" >&2; exit 1; }

# ── Preflight: everything that must exist locally ──────────────────────────
# Checked before any AWS call, because a missing seed file is the one failure
# that would leave the corpus cleared and not re-seeded.
command -v aws >/dev/null || die "aws CLI not found"
command -v python3 >/dev/null || die "python3 not found"
[ -d "$SEED_DIR" ] || die "$SEED_DIR is missing — run scripts/generate_corpus_documents.py"
SEED_COUNT=$(find "$SEED_DIR" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
[ "$SEED_COUNT" -gt 0 ] || die "$SEED_DIR contains no seed documents"
[ -f "$RFP_SRC" ] || die "$RFP_SRC is missing"
aws sts get-caller-identity >/dev/null 2>&1 || die "AWS session is not authenticated"

# ── Resolve every AWS resource name up front ───────────────────────────────
# Nothing above this block is destructive and nothing in it is either. A
# missing output aborts here, with the environment untouched.
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
    die "stack '$1' has no output '$2'. Is $PREFIX deployed in $REGION with STAGE=$STAGE?"
  fi
  printf '%s' "$value"
}

echo ""
echo "─── Resolving stack outputs ───"
CORPUS_BUCKET=$(require_output "$PREFIX-storage" "ReferenceCorpusBucketName")
INCOMING_BUCKET=$(require_output "$PREFIX-storage" "IncomingBucketName")
JOBS_TABLE=$(require_output "$PREFIX-data" "JobsTableName")
QUESTIONS_TABLE=$(require_output "$PREFIX-data" "QuestionsTableName")
REVIEWS_TABLE=$(require_output "$PREFIX-data" "ReviewsTableName")
CUSTOMER_REFS_TABLE=$(require_output "$PREFIX-data" "CustomerRefsTableName")
KB_ID=$(require_output "$PREFIX-knowledge-base" "KnowledgeBaseId")
DS_ID=$(require_output "$PREFIX-knowledge-base" "DataSourceId")
SM_ARN=$(require_output "$PREFIX-orchestration" "StateMachineArn")
echo "✓ All outputs resolved — safe to proceed"

# ── Regenerate synthetic data (local only) ─────────────────────────────────
echo ""
echo "─── Regenerating synthetic data ───"
cd "$ROOT"
python3 scripts/generate_synthetic_data.py

# ── Clear DynamoDB tables ──────────────────────────────────────────────────
echo ""
echo "─── Clearing DynamoDB tables ───"
JOBS_TABLE="$JOBS_TABLE" QUESTIONS_TABLE="$QUESTIONS_TABLE" REVIEWS_TABLE="$REVIEWS_TABLE" \
python3 - <<'PYEOF'
import os

import boto3

ddb = boto3.resource("dynamodb")


def clear_table(table_name, key_attrs):
    table = ddb.Table(table_name)
    deleted = 0
    last_key = None
    while True:
        kwargs = {
            "ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(key_attrs))),
            "ExpressionAttributeNames": {f"#k{i}": k for i, k in enumerate(key_attrs)},
        }
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


# Table names arrive through the environment, not through shell interpolation
# into the heredoc: a name is data, and this loop is the destructive one.
# CustomerRefs is deliberately absent — it is reference data, re-seeded below
# by seed_customer_refs.py, and clearing it here would leave hard rule 4
# (customer-name gating) with nothing to check against if that re-seed failed.
for var, keys in (
    ("JOBS_TABLE", ["jobId"]),
    ("QUESTIONS_TABLE", ["jobId", "questionId"]),
    ("REVIEWS_TABLE", ["jobId", "reviewedAt"]),
):
    name = os.environ[var]
    print(f"  cleared {name}: {clear_table(name, keys)} items deleted")
PYEOF

# ── Reset the flywheel ─────────────────────────────────────────────────────
# The demo's closing beat depends on CAIQ-038 having NO SME-approved answer in
# the knowledge base at the start of the run. Every previous approval left ~40
# of them behind.
echo ""
echo "─── Resetting the flywheel (sme-approved/ corpus) ───"
count_prefix() {
  local n
  n=$(aws s3api list-objects-v2 --bucket "$CORPUS_BUCKET" --prefix "sme-approved/" \
    --region "$REGION" --query 'length(Contents)' --output text 2>/dev/null || echo 0)
  case "$n" in ''|None) n=0 ;; esac
  printf '%s' "$n"
}

BEFORE=$(count_prefix)
echo "  before: s3://$CORPUS_BUCKET/sme-approved/ holds $BEFORE object(s)"
aws s3 rm "s3://$CORPUS_BUCKET/sme-approved/" --recursive --region "$REGION" --quiet
echo "  cleared every object under sme-approved/, including lfb-* from past approvals"
aws s3 cp "$SEED_DIR/" "s3://$CORPUS_BUCKET/sme-approved/" \
  --recursive --region "$REGION" --quiet
AFTER=$(count_prefix)
echo "  re-uploaded the $SEED_COUNT seed answer(s) from $SEED_DIR"
echo "  after:  sme-approved/ holds $AFTER object(s):"
aws s3 ls "s3://$CORPUS_BUCKET/sme-approved/" --region "$REGION" | sed 's/^/    /'

# ── Re-seed corpus and customer refs ───────────────────────────────────────
# seed_s3_corpus.py re-uploads all four corpus prefixes and then runs a full
# Bedrock ingestion pass. That pass is what actually removes the deleted
# lfb-* vectors from the knowledge base — deleting the S3 objects alone does
# not, and a stale vector answers CAIQ-038 just as well as a live one does.
echo ""
echo "─── Re-seeding reference corpus + re-ingesting the knowledge base ───"
echo "    kb=$KB_ID  ds=$DS_ID — this ingestion is what makes red→amber repeatable"
python3 scripts/seed_s3_corpus.py --bucket "$CORPUS_BUCKET" --stack-prefix "$PREFIX"

echo ""
echo "─── Re-seeding customer references ───"
python3 scripts/seed_customer_refs.py --table "$CUSTOMER_REFS_TABLE"

# ── Re-upload sample RFP ───────────────────────────────────────────────────
echo ""
echo "─── Re-uploading sample RFP ───"
aws s3 cp "$RFP_SRC" "s3://$INCOMING_BUCKET/$RFP_KEY" --region "$REGION" --quiet
echo "✓ Uploaded s3://$INCOMING_BUCKET/$RFP_KEY"

# ── Summary ────────────────────────────────────────────────────────────────
END=$(date +%s)
ELAPSED=$((END - START))
echo ""
echo "════════════════════════════════════════════════════════════"
echo " Reset complete in ${ELAPSED}s"
echo "════════════════════════════════════════════════════════════"
echo " FLYWHEEL RESET: sme-approved/ went from $BEFORE object(s) to $AFTER —"
echo "   the $SEED_COUNT seed answer(s) plus sidecars, and nothing else."
echo "   CAIQ-038 has no SME answer in the KB, so it comes back RED on run one"
echo "   and the red→amber beat lands on the next approval."
echo ""
echo " Start a fresh demo run:"
echo "   aws stepfunctions start-execution \\"
echo "     --state-machine-arn '$SM_ARN' \\"
echo "     --input '{\"bucket\":\"$INCOMING_BUCKET\",\"key\":\"$RFP_KEY\",\"jobId\":\"demo-$(date +%s)\"}'"
echo "════════════════════════════════════════════════════════════"
