# SME-Approved Q&A — lfb-0cabfd4b7dfa872f

**Approved:** sarah.chen@example.com on 2026-01-15  
**Topics:** encryption_at_rest, key_management  
**Corroborated by:** encryption_whitepaper

## Question

How is customer data encrypted at rest?

## Answer

All customer data is encrypted at rest using AES-256-GCM. Keys are managed by AWS KMS with automatic annual rotation. Enterprise customers may supply their own CMK (BYOK) via an AWS KMS key policy in their own account. [encryption_whitepaper]
