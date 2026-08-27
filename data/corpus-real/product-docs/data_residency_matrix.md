# Data Residency Matrix — Regions, Pinning and Cross-Border Transfer

## Scope of this document

This document states where tenant data is held, what "held" covers, how a
tenant is pinned to a residency zone, the narrow circumstances in which data or
access crosses a zone boundary, and the transfer safeguards that apply when it
does.

## Residency zones

| Zone | Regions | Typical driver |
| --- | --- | --- |
| Canada | Two Canadian regions | Federal and provincial public sector, Canadian financial services |
| United States | Two United States regions | United States regulated industries |
| European Union | Two European Union regions | GDPR, sectoral supervision |
| United Kingdom | Two United Kingdom regions | UK GDPR, Financial Conduct Authority expectations |
| Australia | Two Australian regions | Australian government and financial services |

A zone is served from two regions in an active-active configuration, and both
sit inside the zone. Continuity within a zone therefore never requires leaving
it. This is a constraint on the resilience design rather than a consequence of
it.

## What "pinned to a zone" covers

A tenant elects a zone at provisioning. The election covers message bodies,
attachments, message metadata, derived search indexes, key material including
key-encryption keys and their versions, event records, and backup images of all
of the above.

It does not cover three things, and saying so plainly is more useful to a
reviewer than a blanket claim that nothing ever leaves.

Aggregate operational telemetry — request rates, error rates, latency
distributions, capacity metrics — is collected centrally. It carries no message
content, no message metadata, no personal data of senders or recipients, and no
tenant-identifying content beyond an opaque tenant identifier.

Support correspondence lives in the support platform, which is located in the
European Union. A customer that raises a ticket and pastes content into it has
placed that content there; the support workflow asks customers not to, and
redacts what it detects, but the correspondence itself is not zone-pinned.

Billing and contract records are held centrally and contain no message content.

## Changing zones

A tenant's zone can be changed after provisioning through a coordinated
migration, not a configuration toggle. Migration copies data to the new zone,
verifies it, re-wraps key material under the new zone's hierarchy, cuts traffic
over, and then erases the source. The source erasure is verified and confirmed
in writing, because a migration that leaves a readable copy behind has
defeated its own purpose.

Migration is scheduled with the account team. Duration depends on volume; a
typical enterprise tenant completes inside a maintenance window.

## Support access from outside the zone

Engineering and support personnel are distributed. Where an issue requires a
member of staff outside the tenant's zone to access tenant data, the access
runs through a named-approval path: a request naming the tenant, the reason,
the data needed and the time bound; approval by a person other than the
requester; a session that expires automatically; and a record written to the
tenant's own event stream, not only to an internal log.

A tenant may require that its own administrator approve each such session
before it can begin. Tenants in the Canadian and European Union zones commonly
enable this, and public sector tenants generally require it. A tenant may also
restrict support access to personnel located inside its zone, at the cost of a
slower response outside that zone's business hours — the trade-off is real and
is stated during onboarding rather than discovered later.

## Transfer safeguards

Where personal data does cross a zone boundary — support correspondence, an
approved support session, or a cross-zone feature the tenant has explicitly
enabled — the transfer relies on the 2021 Standard Contractual Clauses for
transfers out of the European Economic Area, supplemented by a transfer impact
assessment available on request, and on the International Data Transfer
Addendum for transfers out of the United Kingdom. The GDPR position and the
sub-processor register are set out in the Data Processing Agreement.

The tenant's ability to hold its own root key material is relevant to a
transfer assessment. Where the tenant holds the root key, content that crosses
a boundary in sealed form remains unreadable without a call to the tenant's own
key, and that call is recorded in the tenant's own key service.

## Verifying the position

A reviewer can verify the residency position rather than take it on trust. The
tenant administration plane displays the elected zone and the regions serving
it. The event stream records every support session against the tenant,
including the location from which it was established. The sub-processor
register names the processing location of each sub-processor. And the annual
independent assessment covers the controls that enforce pinning, so the
assertion is tested by someone other than the party making it.
