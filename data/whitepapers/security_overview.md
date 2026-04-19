# Security Overview

Our platform is designed around a defense-in-depth architecture with
mandatory controls at the network, compute, data, identity, and
application layers.

## Compliance
- SOC 2 Type II — annual audit, trust principles Security, Availability, Confidentiality
- ISO 27001 — certified
- FedRAMP Moderate — ATO in progress (no committed date)
- GDPR — EU SCCs incorporated into standard DPA

## Encryption
- At rest: AES-256-GCM, AWS KMS managed; BYOK available on Enterprise tier
- In transit: TLS 1.2+ with Perfect Forward Secrecy, HSTS, TLS 1.0/1.1 disabled
