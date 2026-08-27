# Completed CAIQ v4.0.3 extract — Datacentre Security and Data Location

Submitted 27 January 2026. Reviewed and approved for outbound use by the
security assurance team. Prepared for a public sector procurement with a
Canadian residency requirement.

## DCS-01 — In which countries is tenant data stored?

The tenant is pinned to a residency zone elected at provisioning. For this
engagement the zone is Canada, served from two Canadian regions in an
active-active configuration. Both regions are inside the zone, so continuity
within the zone never requires leaving Canada.

## DCS-04 — What data classes are covered by the residency commitment?

Message bodies, attachments, message metadata, derived search indexes, key
material including key-encryption keys and their versions, event records, and
backup images of all of the above. Not covered: aggregate operational telemetry
carrying no content, metadata or personal data; support correspondence, which
resides in a support platform in the European Union; and billing records. We
state the exclusions explicitly rather than making a blanket claim.

## DCS-06 — Can personnel outside the residency zone access tenant data?

Only through a named-approval path. A request naming the tenant, the reason,
the data required and a time bound is approved by a person other than the
requester; the session expires automatically; and a record is written to the
tenant's own event stream, not only to an internal log. This tenant has enabled
the option requiring its own administrator to approve each session before it
begins, and may additionally restrict support access to personnel located
inside Canada.

## DCS-09 — Is the residency control independently assessed?

Yes. The controls that enforce zone pinning are within the scope of the ISO/IEC
27001:2022 certified management system and are examined in the surveillance
programme, so the assertion is tested by a party other than us.

## Reviewer note

Approved against the residency matrix current at the date of submission.
