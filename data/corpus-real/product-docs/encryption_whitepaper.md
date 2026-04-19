# Encryption Architecture Whitepaper

## Overview

This whitepaper describes how the platform protects customer data at rest and in transit, the key management model, and the options available to Enterprise customers who wish to hold their own keys.

Three design principles underlie every decision in this document.

First: cryptography is a supporting control, not a primary one. Encryption closes specific residual-risk categories — physical theft of storage media, interception in transit, misconfigured bucket policies — but it does not substitute for access controls, network segmentation, identity hygiene, or operational security. Every decision below assumes the other controls are working.

Second: use well-reviewed primitives, standard protocols, and managed services wherever possible. Writing new cryptography is a known failure mode. Every algorithm and key length referenced in this document follows the recommendations of NIST SP 800-131A Revision 2 and the IETF at the time of writing.

Third: key management is the hard part. The cost of an encryption program is dominated by key lifecycle — generation, rotation, access control, audit, and recovery. Decisions about where keys live, who can access them, and how their use is logged matter more than the choice of algorithm.

## Data-at-rest encryption

All customer data stored in the platform is encrypted at rest using AES-256 in Galois/Counter Mode (AES-256-GCM), via AWS Key Management Service (KMS). AES-256-GCM is an authenticated encryption mode; it protects both the confidentiality and the integrity of the ciphertext, such that tampering with the ciphertext at rest will be detected on decryption.

Data is encrypted at the storage layer. For relational tenant data, encryption is applied by the database engine, using a data key derived from a KMS customer master key (CMK). For object storage, encryption is applied server-side by Amazon S3 (`x-amz-server-side-encryption: aws:kms`) using the same class of CMK. For block storage used by application compute, encryption is applied at the Amazon EBS layer.

Keys are never stored or cached in plaintext outside AWS KMS. Data keys generated from the CMK are used in-memory for the duration of a single encryption or decryption operation and then discarded. This design means that to decrypt any customer data, an attacker would need both access to the ciphertext and the ability to invoke `kms:Decrypt` on the controlling CMK — the latter gated by IAM policy, the KMS key policy, and the AWS CloudTrail audit log.

## Data-in-transit encryption

All data in transit is encrypted using TLS 1.2 or TLS 1.3. TLS 1.0 and TLS 1.1 are disabled at every entrypoint. The cipher suite allowlist requires Perfect Forward Secrecy, realized through ECDHE-based key exchange — which means that compromise of a long-term server private key does not retroactively compromise the session keys of past recorded traffic.

A short, intentionally conservative list of suites is accepted: TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256, TLS_AES_128_GCM_SHA256 (for TLS 1.3), and ECDHE-ECDSA/ECDHE-RSA with AES-GCM for TLS 1.2. All other suites are rejected at the load balancer.

HTTP Strict Transport Security (HSTS) is advertised on all public HTTPS endpoints with a one-year `max-age` and the `includeSubDomains` directive. The apex domain and relevant subdomains are preloaded into the major browsers' HSTS preload lists. Mixed content is not served; an unencrypted request to any customer-facing endpoint is met with a 301 redirect to HTTPS followed by HSTS enforcement on subsequent requests.

Between internal services within the AWS network boundary, mTLS is used where the service-to-service communication crosses a trust boundary (for example, between the data plane and the control plane). Service identity is established through short-lived certificates issued from an internal private CA and rotated on the order of hours.

## Customer-managed keys (BYOK / HYOK)

Enterprise customers may opt to hold the root key controlling the encryption of their tenant's data. Two options are supported.

Option 1 — customer CMK in the customer's AWS account. The customer creates a KMS CMK in their own AWS account, grants the platform's service principal a key policy permitting `kms:Encrypt`, `kms:Decrypt`, `kms:ReEncrypt*`, `kms:GenerateDataKey*`, and `kms:DescribeKey`, and provides the CMK ARN during tenant provisioning. The platform uses the customer's CMK to wrap tenant data keys. Because the key lives in the customer's account, the customer has full visibility of every use via CloudTrail, and can revoke access at any time by revoking the key policy statement. Revoking key access has an immediate effect: in-flight operations fail, and all subsequent reads of encrypted data return an authorization error from KMS until access is restored.

Option 2 — external key material via AWS KMS External Key Store (XKS). For customers with a regulatory or policy requirement that the key material never reside within AWS, the platform supports CMKs backed by an External Key Store, where the root key material lives in a customer-controlled HSM accessible to AWS KMS through a documented interface. All the same KMS operations apply, with the difference that the underlying cryptographic operations occur within the customer's HSM.

In both options, the customer retains the cryptographic lever: revoking access to the root key renders tenant data unreadable. This is by design.

## Key rotation

KMS CMKs under the platform's management are rotated annually by default. Rotation is automatic, transparent, and does not require re-encryption of existing ciphertext — KMS retains access to prior key versions for decryption purposes. Customers holding their own CMKs may rotate on any cadence permissible to their policy; rotation events in the customer's account are reflected automatically through the KMS data-key derivation protocol.

On-demand rotation is supported for all CMK types. A customer-initiated rotation request is processed within minutes and is visible in both the customer's audit log and the platform's audit log.

## Cryptographic hygiene at the edges

Beyond bulk encryption, the platform follows a short list of hygiene practices that close common failure modes. All password hashes are stored using Argon2id with parameters tuned to 500 ms of work on production hardware. All session tokens are opaque, 256-bit random values issued through a cryptographically secure RNG, transmitted only over TLS, and invalidated on logout. API keys for machine-to-machine access are scoped to the minimum set of operations required and are revocable individually from the customer's administrative console. Cryptographic configuration is reviewed at the start of each year against the then-current guidance from NIST and the IETF; algorithms or parameter choices that have fallen out of the recommended set are deprecated on a published schedule.

## Cryptographic agility

A recurring failure mode in long-lived systems is the inability to migrate away from an algorithm, a protocol version, or a key size once it has been deprecated by the cryptographic community. The platform's data-at-rest and data-in-transit stacks are built to support migration.

Data-at-rest keys are referenced by CMK identifier rather than by inlined key material. Replacing a CMK with one using a different algorithm requires only a re-encryption of the affected data keys — a background operation that does not require downtime or re-transmission of customer data. Re-encryption operations are rate-limited to respect KMS request budgets, instrumented with progress reporting, and resumable from partial-failure states.

Data-in-transit protocol versions and cipher suite lists are surfaced as configuration, not code. A change to the supported protocol versions propagates through the load-balancer fleet in under an hour and is reversible by a single configuration rollback. Protocol deprecation follows a published schedule announced to customers at least 90 days in advance.

## Post-quantum posture

The platform is actively tracking the transition to post-quantum cryptography. The current posture reflects three principles.

First: store-now-decrypt-later is a real risk for long-term confidential data. Traffic intercepted today may be decrypted in the future by an adversary that acquires a cryptographically-relevant quantum computer. The platform's TLS configuration is in the process of adding hybrid key exchange (combining a classical algorithm with a post-quantum key-encapsulation mechanism) for customer-facing endpoints, providing forward protection against store-now-decrypt-later adversaries.

Second: post-quantum primitives are younger than classical primitives. A conservative migration path pairs each post-quantum primitive with a classical primitive (the "hybrid" approach) so that a flaw in either primitive alone does not compromise the protected data. The industry is converging on this pattern for the transition period.

Third: symmetric primitives and hash functions are less affected by the quantum threat than public-key cryptography. AES-256 is considered secure against Grover's algorithm at the 128-bit post-quantum security level; SHA-256 is similarly considered secure. The platform's data-at-rest encryption does not require migration on the post-quantum transition timeline; the focus is on key exchange, digital signatures, and certificate infrastructure.

## Key separation by data class

Different classes of customer data use different CMKs, enabling per-class access control and per-class rotation. Relational tenant data, object-storage tenant data, backups, and audit logs each use a distinct CMK. This separation means that a policy or access-control event on one key — for example, revoking access to backups — does not affect the others. The separation also aligns with the different retention policies across classes: audit log CMKs are retained longer than tenant-data CMKs to support the extended retention of audit logs.

Cross-account key use, for the Enterprise BYOK path, follows the same separation pattern: a customer's own CMK controls one class of data. Customers may elect to bring their own key for one class (say, object storage) while relying on the platform's CMK for other classes. The tenant configuration records which classes are under customer-held keys, and the access-and-use story is consistent across classes.

## Key compromise and incident response

In the hypothetical event of a confirmed key compromise, the response playbook comprises: (1) rotate the affected CMK to a new key version and force re-derivation of all dependent data keys; (2) identify the window of potential exposure from KMS CloudTrail logs and incident evidence; (3) re-encrypt affected ciphertext under the new key version; (4) notify affected customers within the notification timelines specified in the relevant customer contract, no later than 72 hours from confirmation. The playbook is rehearsed in the regular tabletop exercise program and updated as the key-management architecture evolves.
