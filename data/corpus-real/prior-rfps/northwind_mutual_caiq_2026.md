# Completed CAIQ v4.0.3 extract — Cryptography, Encryption and Key Management

Submitted 12 May 2026. Reviewed and approved for outbound use by the security
assurance team.

## EKM-01 — Are encryption keys maintained by the cloud consumer or a trusted key management provider?

Both arrangements are offered. Under the default, tenant key-encryption keys
are held by us in a managed key service. Enterprise tenants may instead supply
a customer-managed key held in their own account, or hold root key material in
a hardware security module they operate. Where the tenant holds the key, it can
revoke our grant unilaterally, which renders sealed content unreadable
immediately and reversibly, or schedule destruction, which is irreversible
after the mandatory waiting period.

## EKM-02 — Do you have a capability to allow creation of unique encryption keys per tenant?

Yes. Key-encryption keys are per tenant and, within a tenant, per data class —
message bodies and attachments, message metadata, derived indexes, and event
records each have their own. Data keys are per message.

## EKM-03 — Do you encrypt tenant data at rest?

Yes. Message bodies, attachments, metadata and derived indexes are sealed with
AES-256-GCM under per-message data keys, which are wrapped by the tenant
key-encryption key. Data keys exist in plaintext only in process memory for the
duration of a single operation. The stored envelope carries the ciphertext, the
nonce, the authentication tag and the wrapped data key.

## EKM-04 — What is your key rotation policy?

Key-encryption keys rotate on a 365-day cycle by default. Tenant administrators
may rotate on demand at any time from the administration plane or the API, with
effect within minutes. Rotation does not require rewriting existing envelopes,
because each envelope names the key version that wrapped it; a background
process migrates envelopes onto the current version over time.

## EKM-08 — Do you have documented ownership for each stage of the key lifecycle?

Yes. Generation, distribution, storage, use, rotation, archival and destruction
each have a named owner and a documented procedure. The procedures are covered
by the annual independent assessment and were tested without exception in the
most recent report period.

## Reviewer note

These responses were approved against the encryption and key management
documentation current at the date of submission. Confirm against the current
product documentation before reuse; the key management posture is the area most
likely to have gained options since.
