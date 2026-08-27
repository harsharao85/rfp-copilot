# Disaster Recovery and Business Continuity — RTO and RPO

## Scope of this document

This document sets out the resilience posture of the cloud email encryption
service: the topology, what is replicated and how, the published recovery
objectives per component, the exercise programme, the backup regime, and the
dependencies on which continuity rests.

## Topology

Each residency zone is served from two regions operating active-active. Both
regions carry live traffic under normal conditions; neither is a cold standby
that nobody has exercised. Traffic is steered by latency-based routing that can
be collapsed onto a single region within about two minutes, which is the DNS
time-to-live plus propagation.

Within a region, every stateful component spans at least two availability
zones, and stateless components scale independently across three. The loss of
one availability zone is absorbed without a traffic shift and without a
customer-visible event.

Both regions in a zone are inside the residency boundary. A tenant pinned to
the European Union zone fails over between two European Union regions; it does
not fail over to another continent. This constrains the design and is
deliberate: a continuity arrangement that violates the residency commitment is
not a continuity arrangement a regulated customer can accept.

## Replication

Replication is chosen per data class.

Tenant configuration, delivery policy, identity state and key references
replicate synchronously between the two regions. A write is not acknowledged
until it is durable in both. The cost is write latency; the benefit is that
these classes have no data-loss window at all.

Message bodies, attachments and metadata replicate asynchronously with a target
lag under thirty seconds. Sudden loss of a region can therefore lose the most
recent writes within that window.

Event records are forwarded continuously to a durable store and are
acknowledged to the tenant only once durable, so an event that has been
acknowledged has not been lost.

## Published recovery objectives

| Component | Recovery time objective | Recovery point objective |
| --- | --- | --- |
| Encryption gateway and delivery path | 2 hours | 5 minutes |
| Recipient portal | 4 hours | 15 minutes |
| Tenant administration plane | 4 hours | 5 minutes |
| Key management plane | 1 hour | 0 (synchronous) |
| Event export | 4 hours | 0 for acknowledged records |

These are commitments against which the service is managed, not observations.
Measured behaviour in recent exercises has been well inside them; the numbers
above are the bound, and the bound is what belongs in a contract.

The service commitment for the Enterprise plan is 99.95% monthly uptime,
measured as a rolling monthly average, excluding maintenance announced at least
fourteen days in advance. Service credits for a missed month are set out in the
service level agreement and are not described here.

## Exercise programme

Continuity arrangements are exercised at least twice a year. One exercise is a
full regional evacuation: real production traffic for a residency zone is
collapsed onto a single region for a bounded window, held there, and then
ramped back in stages. This exercise is the one that matters, because it is the
only one that proves a single region can actually carry the load — capacity
planning on paper routinely says yes when the answer is no.

The second exercise is scenario-based and rotates: loss of the key management
plane, corruption of a replicated data set, loss of a critical supplier, and
loss of the primary operational communication channel have all been exercised.
Findings and remediation actions from every exercise are tracked to closure and
reviewed at the quarterly management review.

Enterprise customers may request a summary of the most recent exercise under
non-disclosure, and a customer whose own regulator requires evidence of
third-party continuity testing can arrange, through the account team, to
observe a scheduled exercise.

## Backups

Backups sit below replication as a defence against a different failure: not
losing the data, but corrupting or deleting it correctly-and-everywhere.
Replication faithfully copies a destructive mistake to both regions; a backup
does not.

Full backups of every tenant data store are taken daily, with continuous
point-in-time recovery over a rolling thirty-five-day window. Backup copies are
written to an account separate from production, under distinct credentials that
production workloads do not hold, with object lock preventing deletion inside
the retention window even by an administrator of that account.

Restore is tested monthly by restoring a randomly selected backup into an
isolated environment and running an integrity check. A failed restore test is
handled as a priority incident. Testing the restore rather than the backup is
the point: a backup that has never been restored is a hypothesis.

Extended retention beyond the default is available by contract for customers
with statutory retention obligations.

## Dependencies

Continuity rests on a small number of upstream services, mapped and reviewed
quarterly. Losing the infrastructure provider's regional control plane, the
authoritative DNS service, the public certification authority, or the managed
key service would each prevent normal operation for the duration of that
outage; each has a documented compensating arrangement where one is technically
and commercially available. Losing observability tooling, the ticketing system
or the status page provider would degrade operations without preventing
service.

## Communication during an event

During a declared event, the status page is updated first and continues to be
updated at stated intervals even when there is nothing new to say, because
silence is read as absence. Tenants with a configured webhook receive
programmatic notification. Where the event meets the incident notification
criteria, registered security contacts are notified directly within 72 hours of
confirmation, and a written post-incident review follows within thirty days.
