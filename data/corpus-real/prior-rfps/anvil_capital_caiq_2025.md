# Completed CAIQ v4.0.3 extract — Business Continuity and Security Incident Management

Submitted 6 November 2025. Reviewed and approved for outbound use by the
security assurance team.

## BCR-01 — Do you have a business continuity plan, and is it exercised?

Yes. Each residency zone runs active-active across two regions, both carrying
live traffic. Continuity arrangements are exercised at least twice a year. One
exercise is a full regional evacuation with real production traffic collapsed
onto a single region for a bounded window; the other is scenario-based and
rotates through loss of the key management plane, corruption of a replicated
data set, loss of a critical supplier, and loss of the primary operational
communication channel.

## BCR-05 — What are your recovery objectives?

The encryption gateway and delivery path carry a recovery time objective of 2
hours and a recovery point objective of 5 minutes. The recipient portal carries
4 hours and 15 minutes. The key management plane carries 1 hour and a zero
recovery point objective, because its state replicates synchronously. These are
commitments, not observations.

## BCR-11 — How are backups protected and tested?

Full backups daily with continuous point-in-time recovery over a rolling
thirty-five day window. Copies are written to an account separate from
production under credentials production workloads do not hold, with object lock
preventing deletion inside the retention window. Restores are tested monthly
into an isolated environment with an integrity check; a failed restore test is
handled as a priority incident.

## SEF-02 — Describe your security incident response process.

Detection, triage, containment, eradication, recovery and review, with a named
incident commander for any event above the lowest severity and a 24x7
operations function performing triage. The procedure is exercised twice per
report period, once as a tabletop and once as a live scenario.

## SEF-05 — What are your customer notification timelines?

Where an event has been confirmed to have affected the confidentiality,
integrity or availability of a tenant's data, that tenant's registered security
contacts are notified within 72 hours of confirmation. The initial notification
states what is known, what is not yet known, the containment position and the
point of contact. A written post-incident review follows within thirty days of
closure. Events prevented by controls before impact, and events confined to
internal systems without effect on tenant isolation, are recorded and reviewed
internally but are not notifiable.

## SEF-08 — Are incident response obligations covered contractually?

Yes, the 72-hour notification commitment is in the master agreement and the
Data Processing Agreement, and the control is within the scope of the SOC 2
Type II report.

## Reviewer note

Approved against the continuity and incident documentation current at the date
of submission. Recovery objectives have since been restated per component;
confirm against the current documentation before reuse.
