# Prior RFP answer — Bluebird Insurance: encryption at rest

Approved for outbound use on 2025-08-15.

**Question:** How is customer data encrypted at rest? Specify algorithm, key length, and key management approach.

**Approved answer:** Customer data at rest is encrypted with AES-256-GCM. Keys are managed in AWS KMS under the service organization's account by default. Enterprise customers may bring their own CMK, held in their own AWS account. Keys are rotated annually by default, with on-demand rotation available.

**Note:** This answer was approved against the encryption architecture documentation current at the time of approval.
