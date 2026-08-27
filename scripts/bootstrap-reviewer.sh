#!/usr/bin/env bash
# bootstrap-reviewer.sh — create the first reviewer in the Cognito pool.
#
# Self sign-up is disabled on the pool (auth-stack.ts): an open pool in front
# of an endpoint that writes into the knowledge base is the same Critical
# finding wearing a login page. The consequence is that a freshly deployed
# account has a working Hosted UI and nobody who can sign in to it, which is
# where every first-time deploy stalled. Both deploy paths call this.
#
# Idempotent: an existing user is left alone, and adding a user to a group
# they are already in is a no-op in Cognito. Safe to re-run, and safe to run
# by hand later to add a second reviewer.
#
# Usage:
#   bash scripts/bootstrap-reviewer.sh <user-pool-id> <email> [region]
#
# The invite (with a temporary password) is emailed by Cognito. Where email
# delivery is not usable — a throwaway demo address, a sandbox with SES
# unverified — set REVIEWER_TEMP_PASSWORD to a value satisfying the pool's
# policy (>=12 chars, upper + lower + digit + symbol) and the invite email is
# suppressed instead. Either way Cognito forces a password change at first
# sign-in.
set -euo pipefail

POOL_ID="${1:-}"
EMAIL="${2:-}"
REGION="${3:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}}"
GROUP="reviewers"

if [ -z "$POOL_ID" ] || [ -z "$EMAIL" ]; then
  echo "Usage: $0 <user-pool-id> <email> [region]" >&2
  exit 1
fi

case "$EMAIL" in
  *@*.*) ;;
  *) echo "ERROR: '$EMAIL' does not look like an email address" >&2; exit 1 ;;
esac

if aws cognito-idp admin-get-user \
     --user-pool-id "$POOL_ID" --username "$EMAIL" --region "$REGION" >/dev/null 2>&1; then
  echo "  reviewer $EMAIL already exists in $POOL_ID — leaving it alone"
else
  echo "  creating reviewer $EMAIL in $POOL_ID"
  if [ -n "${REVIEWER_TEMP_PASSWORD:-}" ]; then
    aws cognito-idp admin-create-user \
      --user-pool-id "$POOL_ID" \
      --username "$EMAIL" \
      --user-attributes "Name=email,Value=$EMAIL" "Name=email_verified,Value=true" \
      --temporary-password "$REVIEWER_TEMP_PASSWORD" \
      --message-action SUPPRESS \
      --region "$REGION" >/dev/null
    echo "  invite email suppressed — sign in with REVIEWER_TEMP_PASSWORD and change it"
  else
    aws cognito-idp admin-create-user \
      --user-pool-id "$POOL_ID" \
      --username "$EMAIL" \
      --user-attributes "Name=email,Value=$EMAIL" "Name=email_verified,Value=true" \
      --desired-delivery-mediums EMAIL \
      --region "$REGION" >/dev/null
    echo "  Cognito has emailed $EMAIL a temporary password"
  fi
fi

# Group membership rides in the `cognito:groups` claim. Re-adding an existing
# member is a no-op, so this runs unconditionally and repairs a user created
# by hand without the group.
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --group-name "$GROUP" \
  --region "$REGION"
echo "  $EMAIL is in the '$GROUP' group"
