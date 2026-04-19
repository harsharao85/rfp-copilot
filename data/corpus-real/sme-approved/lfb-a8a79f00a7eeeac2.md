# SME-Approved Q&A — lfb-a8a79f00a7eeeac2

**Approved:** sarah.chen@example.com on 2026-01-15  
**Topics:** encryption_in_transit  
**Corroborated by:** encryption_whitepaper

## Question

How is data encrypted in transit?

## Answer

All data in transit is protected by TLS 1.2 or 1.3. We require PFS (ECDHE cipher suites), disable TLS 1.0/1.1, and publish HSTS headers with a one-year max-age. [encryption_whitepaper]
