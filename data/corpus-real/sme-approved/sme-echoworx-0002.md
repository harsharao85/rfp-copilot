# SME-Approved Q&A — sme-echoworx-0002

**Approved:** d.mensah@echoworx.example on 2026-07-14  
**Topics:** —  
**Corroborated by:** scim_provisioning

## Question

What administrative and delivery event records can a tenant export to its own SIEM, and in what format?

## Answer

Tenant administrators can export three event streams: administrative actions in the tenant plane, message delivery and disposition events, and recipient authentication events. Records are emitted as newline-delimited JSON conforming to a published schema, and can be pulled from a per-tenant export endpoint or pushed to a customer-owned object store bucket on a five-minute cadence. Field-level documentation and a mapping to the Elastic Common Schema are supplied so the stream can be onboarded into a customer SIEM without bespoke parsing. Records are retained in the platform for 400 days independently of what the tenant exports.
