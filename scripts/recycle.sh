#!/usr/bin/env bash
# recycle.sh — single-command infra lifecycle for the RFP Copilot demo.
#
# Subcommands:
#   up        generate synthetic data + cdk deploy --all + seed
#   down      empty buckets + cdk destroy --all --force + clean orphans
#   recycle   down, wait briefly for propagation, up
#   status    print current CloudFormation stack statuses
#
# Prereqs: node >= 20, python3.12+, aws CLI v2, Docker Desktop running.
# WARNING: `down` and `recycle` DELETE DATA. Only use against dev/sandbox
# accounts. Requires confirmation.
#
# `up` deploys in two passes. The Cognito callback URLs must name the
# CloudFront domain, which does not exist until the static-site stack does,
# while the static site needs the Cognito identifiers for its config.js.
# Pass 2 redeploys only the auth stack with `-c siteUrl=…` read from pass 1.
# Without it every login from CloudFront fails with `redirect_mismatch`.
# `up` also creates the first reviewer, because self sign-up is disabled.
#
# Usage:
#   bash scripts/recycle.sh up [reviewer@example.com]
#   REVIEWER_EMAIL=reviewer@example.com bash scripts/recycle.sh up
#   bash scripts/recycle.sh down
#   bash scripts/recycle.sh recycle [reviewer@example.com]
#   bash scripts/recycle.sh status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA="$ROOT/infra"
# Stage and region follow the operator's environment, not this file. STAGE must
# match the `stage` CDK context the stacks were deployed with (default: poc).
STAGE="${STAGE:-poc}"
export STAGE
PREFIX="rfp-copilot-$STAGE"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}}"

# Pick the newest available Python (scripts use type hints that need 3.10+).
PY="$(command -v python3.13 || command -v python3.12 || command -v python3)"

# Deployment order, and the list `status` reports. The auth stack was missing
# here, so `status` never mentioned Cognito — the one stack a first-time
# deployer most needs to see the state of.
STACKS=(
  "$PREFIX-storage"
  "$PREFIX-data"
  "$PREFIX-knowledge-base"
  "$PREFIX-auth"
  "$PREFIX-orchestration"
  "$PREFIX-observability"
  "$PREFIX-static-site"
)

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

cfn_output() {
  aws cloudformation describe-stacks \
    --stack-name "$1" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" \
    --output text 2>/dev/null || true
}

check_prereqs() {
  for c in node "$PY" aws docker; do
    command -v "$c" >/dev/null || { echo "ERROR: '$c' not found" >&2; exit 1; }
  done
  docker info >/dev/null 2>&1 || { echo "ERROR: Docker is not running" >&2; exit 1; }
  aws sts get-caller-identity >/dev/null || { echo "ERROR: AWS not authenticated" >&2; exit 1; }
}

confirm_destructive() {
  local account; account=$(aws sts get-caller-identity --query Account --output text)
  echo ""
  echo "WARNING: This will destroy all RFP Copilot resources in account $account / region $REGION."
  echo "All S3 buckets will be emptied. DynamoDB tables will be deleted. The Bedrock KB will be torn down."
  echo ""
  read -r -p "Type 'recycle' to continue (anything else aborts): " CONFIRM
  if [ "$CONFIRM" != "recycle" ]; then
    echo "Aborted." ; exit 1
  fi
}

# ─── up ────────────────────────────────────────────────────────────────────

require_output() {
  local value
  value=$(cfn_output "$1" "$2")
  if [ -z "$value" ] || [ "$value" = "None" ]; then
    echo "ERROR: stack '$1' has no output '$2' — the deploy did not complete." >&2
    exit 1
  fi
  printf '%s' "$value"
}

cmd_up() {
  check_prereqs
  local reviewer_email="${1:-${REVIEWER_EMAIL:-}}"
  local account region
  account=$(aws sts get-caller-identity --query Account --output text)
  log "AWS account $account in $REGION. Python: $PY"

  log "Generating synthetic data + corpus documents…"
  cd "$ROOT"
  "$PY" scripts/generate_synthetic_data.py
  "$PY" scripts/generate_corpus_documents.py

  log "Pass 1/2: deploying all stacks (10–15 min first time, ~2–3 min for updates)…"
  cd "$INFRA"
  [ -d node_modules ] || npm ci --silent
  npx cdk bootstrap "aws://$account/$REGION" 2>/dev/null || true
  npx cdk deploy --all --require-approval never \
    -c "stage=$STAGE" -c "region=$REGION" \
    --outputs-file "$INFRA/outputs.json"

  local site_url user_pool_id
  site_url=$(require_output "$PREFIX-static-site" "SiteUrl")
  user_pool_id=$(require_output "$PREFIX-auth" "UserPoolId")

  # Only the auth stack reads siteUrl, and redeploying it with a callback list
  # that is already correct is a no-op — so `up` stays re-runnable.
  log "Pass 2/2: registering $site_url as a Cognito callback…"
  cd "$INFRA"
  npx cdk deploy "$PREFIX-auth" --require-approval never \
    -c "stage=$STAGE" -c "region=$REGION" -c "siteUrl=$site_url"

  # Self sign-up is disabled on the pool. Without a reviewer, the deploy is
  # complete and unusable.
  if [ -n "$reviewer_email" ]; then
    log "Bootstrapping reviewer $reviewer_email…"
    bash "$SCRIPT_DIR/bootstrap-reviewer.sh" "$user_pool_id" "$reviewer_email" "$REGION"
  else
    log "No reviewer email given — nobody can log in yet."
    log "  Run: bash scripts/bootstrap-reviewer.sh $user_pool_id <email> $REGION"
  fi

  log "Seeding CustomerRefs + KB corpus…"
  cd "$ROOT"
  "$PY" scripts/seed_customer_refs.py
  "$PY" scripts/seed_s3_corpus.py --stack-prefix "$PREFIX"

  local upload_api review_api sm_arn reviewer_line
  upload_api=$(cfn_output "$PREFIX-orchestration" "UploadApiUrl")
  review_api=$(cfn_output "$PREFIX-orchestration" "ReviewApiUrl")
  sm_arn=$(cfn_output "$PREFIX-orchestration" "StateMachineArn")
  if [ -n "$reviewer_email" ]; then
    reviewer_line="$reviewer_email (temporary password emailed by Cognito)"
  else
    reviewer_line="NONE — run scripts/bootstrap-reviewer.sh before demoing"
  fi
  # The site is served from CloudFront, not from disk. Opening ui/upload.html
  # locally cannot work: auth.js hard-stops without the deploy-generated
  # config.js, which only exists in the bucket the site stack deploys.
  cat <<EOF

═══════════════════════════════════════════════════════════
 DEMO READY
═══════════════════════════════════════════════════════════
 SITE URL (open this): $site_url
 Upload API:           $upload_api
 Review API:           $review_api
 State machine:        $sm_arn
 Cognito user pool:    $user_pool_id
 Reviewer:             $reviewer_line

 Open $site_url and sign in to submit an RFP.
 Review amber/red answers at $site_url/review.html.
═══════════════════════════════════════════════════════════
EOF
}

# ─── down ──────────────────────────────────────────────────────────────────

cmd_down() {
  confirm_destructive

  log "Emptying S3 buckets so CDK destroy can delete them…"
  for suffix in incoming output auditbucket referencecorpusbucket; do
    # Find the bucket by prefix + suffix substring
    bucket=$(aws s3api list-buckets --region "$REGION" \
      --query "Buckets[?contains(Name, '${PREFIX}-storage-${suffix}')].Name" \
      --output text 2>/dev/null | head -1)
    if [ -n "$bucket" ] && [ "$bucket" != "None" ]; then
      log "  emptying s3://$bucket/ (versions + delete-markers)"
      # aws s3 rm handles current versions; list-object-versions covers
      # prior versions and delete markers that s3 rm misses on versioned buckets.
      aws s3 rm "s3://$bucket/" --recursive --quiet --region "$REGION" 2>/dev/null || true
      versions=$(aws s3api list-object-versions --bucket "$bucket" --region "$REGION" \
        --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || echo '{}')
      if [ "$(echo "$versions" | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('Objects') or []))" 2>/dev/null || echo 0)" != "0" ]; then
        aws s3api delete-objects --bucket "$bucket" --region "$REGION" --delete "$versions" >/dev/null 2>&1 || true
      fi
      markers=$(aws s3api list-object-versions --bucket "$bucket" --region "$REGION" \
        --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || echo '{}')
      if [ "$(echo "$markers" | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('Objects') or []))" 2>/dev/null || echo 0)" != "0" ]; then
        aws s3api delete-objects --bucket "$bucket" --region "$REGION" --delete "$markers" >/dev/null 2>&1 || true
      fi
    fi
  done

  log "Running cdk destroy --all --force…"
  cd "$INFRA"
  npx cdk destroy --all --force -c "stage=$STAGE" -c "region=$REGION" || log "  (cdk destroy returned non-zero — continuing cleanup)"

  log "Cleaning up retained resources (DDB tables default to RETAIN)…"
  for table in $(aws dynamodb list-tables --region "$REGION" \
      --query "TableNames[?contains(@, '${PREFIX}-data-')]" --output text 2>/dev/null); do
    log "  deleting orphan DDB table $table"
    aws dynamodb delete-table --table-name "$table" --region "$REGION" >/dev/null 2>&1 || true
  done

  log "Checking for orphaned Bedrock KBs / vector buckets…"
  for kb_id in $(aws bedrock-agent list-knowledge-bases --region "$REGION" \
      --query "knowledgeBaseSummaries[?contains(name, 'rfp-copilot')].knowledgeBaseId" \
      --output text 2>/dev/null); do
    [ -z "$kb_id" ] && continue
    log "  orphan KB $kb_id — deleting data sources then KB…"
    for ds_id in $(aws bedrock-agent list-data-sources --knowledge-base-id "$kb_id" --region "$REGION" \
        --query 'dataSourceSummaries[].dataSourceId' --output text 2>/dev/null); do
      aws bedrock-agent delete-data-source --knowledge-base-id "$kb_id" --data-source-id "$ds_id" \
        --region "$REGION" >/dev/null 2>&1 || true
    done
    aws bedrock-agent delete-knowledge-base --knowledge-base-id "$kb_id" --region "$REGION" >/dev/null 2>&1 || true
  done

  log "Down complete."
  echo ""
  echo "Note: if the AuditBucket had Object-Lock'd objects, CDK destroy will have"
  echo "failed on it. Compliance-mode retention cannot be overridden — the bucket"
  echo "must wait for the retention period to expire before it can be emptied."
}

# ─── recycle (down + up) ───────────────────────────────────────────────────

cmd_recycle() {
  local reviewer_email="${1:-${REVIEWER_EMAIL:-}}"
  cmd_down
  log "Waiting 30s for AWS resource propagation before re-deploy…"
  sleep 30
  cmd_up "$reviewer_email"
}

# ─── status ────────────────────────────────────────────────────────────────

cmd_status() {
  if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "ERROR: AWS session expired — run your auth command and retry." >&2
    exit 2
  fi
  printf 'Stack statuses in %s:\n' "$REGION"
  for stack in "${STACKS[@]}"; do
    # Separate stderr so NOT_DEPLOYED really means "stack absent" vs. a transient error.
    status=$(aws cloudformation describe-stacks --stack-name "$stack" --region "$REGION" \
      --query 'Stacks[0].StackStatus' --output text 2>&1)
    if [[ "$status" == *"does not exist"* ]]; then
      status="NOT_DEPLOYED"
    fi
    printf '  %-40s %s\n' "$stack" "$status"
  done
}

# ─── dispatch ──────────────────────────────────────────────────────────────

case "${1:-}" in
  up)       cmd_up "${2:-}" ;;
  down)     cmd_down ;;
  recycle)  cmd_recycle "${2:-}" ;;
  status)   cmd_status ;;
  *)        echo "Usage: $0 {up|down|recycle|status} [reviewer@example.com]" >&2; exit 1 ;;
esac
