# Disaster Recovery and Business Continuity Overview

## Scope

This document describes the disaster recovery (DR) and business continuity (BC) posture for the multi-tenant SaaS platform, including the deployment topology, replication strategy, recovery objectives, exercise cadence, and backup policy. It is intended for customer procurement, compliance, and risk teams who need to assess the platform's resilience against planned unavailability, regional failures, and data loss scenarios.

## Deployment topology

The platform is deployed active-active across two AWS regions, us-east-1 (Northern Virginia) and us-west-2 (Oregon). Each region is a full deployment, able to serve customer traffic independently. Customer traffic is routed by a globally-scoped DNS service with latency-based routing policy, with the ability to shift traffic to a single region on demand in under two minutes for any reason — planned maintenance, elevated error rates from a region, or a formally declared regional event.

Within each region, the platform runs across three Availability Zones (AZs) with every stateful service replicated across a minimum of two AZs. Stateless services autoscale independently within each AZ. Load balancing within a region is handled by Application Load Balancers with cross-zone load balancing enabled.

## Replication strategy

Replication is chosen per data class to balance recovery-point objective (RPO) against operational cost and complexity.

Critical metadata — tenant configuration, identity, authorization state, encryption key references — is replicated synchronously across regions. Writes to this data class do not acknowledge success until the write has been committed in both regions. The tradeoff is higher per-write latency; the benefit is a zero-data-loss RPO for the most sensitive state.

Bulk customer data — application data, uploaded content, generated artifacts — is replicated asynchronously across regions with a target lag under 60 seconds. Asynchronous replication means that in a sudden regional loss, up to approximately 60 seconds of the most recent writes may be unrecoverable; the architecture accepts this tradeoff for the cost and latency benefit.

Logs and audit trails are forwarded in near-real-time to a centralized long-term storage tier and are retained according to the platform's logging retention policy (13 months for security-relevant logs, longer for specific audit categories).

## Recovery objectives

The platform publishes the following recovery objectives for Enterprise tier customers:

- Recovery Time Objective (RTO): 4 hours for a full regional loss, measured from the declaration of a regional event to the restoration of normal service in the unaffected region.
- Recovery Point Objective (RPO): 15 minutes for bulk customer data; zero for critical metadata; zero for completed and acknowledged audit events.
- Planned maintenance: scheduled maintenance windows are announced at least 14 days in advance and are limited to a maximum of two hours per quarter per tenant, exclusive of emergency maintenance.

These objectives are commitments, not historical observations. In practice, the observed behavior during recent DR exercises has been within a fraction of these targets, but the objectives represent the bounds against which the platform is managed.

## DR exercise cadence

DR exercises are conducted at least twice annually. Each exercise covers a defined scenario (for example, loss of one region for a simulated four-hour period) and validates the traffic-shift mechanism, the replication lag, and the operational runbooks. Exercise outcomes, including any discovered gaps and remediation actions, are documented and reviewed at the management review cycle.

At least one live-fire exercise per year involves shifting real traffic to a single region for a bounded window, to confirm that the platform's capacity planning and regional topology can in fact support full load from a single region. The results of the most recent live-fire exercise are available to Enterprise customers under non-disclosure through their Account Executive.

## Backup policy

Backups are a defense-in-depth layer below replication. Full backups of the primary customer-data stores are taken daily, with continuous point-in-time recovery available for a rolling 35-day window for relational data. Backup copies are stored in a separate AWS account from production, with distinct identity and access controls — to protect against the scenarios where replication is operating correctly but the data itself has been corrupted, deleted, or encrypted maliciously.

Backup integrity is tested monthly by performing an actual restore from a randomly-selected backup into a pre-production environment and running an integrity check against the restored data. Backups that fail this test are investigated as a priority incident.

Backups are retained for 90 days by default. Extended retention (up to seven years) is available on request for customers with specific regulatory obligations, subject to contractual amendment.

## Customer-initiated deletion and the deletion-replication interaction

When a customer initiates a deletion — either of specific data via the platform's APIs or of their entire tenant under contract termination — the deletion is propagated to both regions' replicas and recorded in the audit log. Cryptographic erasure of backups containing the deleted data occurs as those backups age out of the retention window; data is not recoverable from backups beyond the documented backup retention period regardless of the deletion request. Customers with extended retention should be aware that the extended retention window also applies to backups containing data that has subsequently been requested for deletion.

## Incident communication

In any regional event or security incident meeting the platform's published notification criteria, customers affected are communicated with through the channels configured on their tenant (email to designated contacts, platform status page, programmatic webhook if configured). The initial communication follows within 72 hours of confirmation of a qualifying event, with follow-up communications at documented milestones and a post-incident review delivered within 30 days.

## Failover runbook summary

The platform's failover runbook is the operational procedure executed when traffic must be shifted away from a region, whether due to a regional AWS event, a platform-level issue localized to one region, or a planned exercise. The runbook is reviewed quarterly and exercised at the documented cadence.

The high-level sequence is: (1) an on-call engineer receives an alert or a declaration of a qualifying event and opens an incident channel following the documented severity criteria; (2) the incident commander verifies the current replication lag, the health of the unaffected region, and the capacity headroom in the unaffected region; (3) the traffic-shift decision is made by the incident commander, supported by a written assessment; (4) the DNS routing policy is updated to direct all customer traffic to the unaffected region, a change that propagates within the DNS TTL plus a small buffer (in practice, under two minutes); (5) once traffic has shifted, the team monitors the unaffected region for saturation, latency, or error-rate anomalies and takes any load-balancing or scaling actions required; (6) the status page is updated and, where warranted, direct customer notifications are dispatched.

Recovery — the return of traffic to both regions once the affected region is healthy — follows a deliberate sequence rather than a sudden flip. Traffic is ramped back to the recovered region in stages (5%, 25%, 50%, 100%), with health checks at each stage and the ability to pause or roll back if any metric deviates from expectation.

## Dependency mapping

Business continuity depends on the continued operation of a small set of upstream dependencies. The dependency map is maintained by the platform operations team and reviewed at each management review.

Tier-1 dependencies — those whose outage would prevent customer access to the platform for the duration of the upstream outage — comprise the AWS regional control planes for the services composing the data plane, the identity provider used for workforce SSO, the DNS service used for customer traffic routing, and the certificate authority underlying public TLS. Each tier-1 dependency has a documented alternate or compensating arrangement where commercially and technically feasible.

Tier-2 dependencies — those whose outage would degrade but not prevent platform operation — comprise third-party observability and alerting systems, the ticketing system used by the operations function, and other supporting SaaS. Degradation scenarios are documented in the operational runbooks.

Customer-facing dependencies — the platform's third-party integrations where the customer tenant configures an external system to receive data from the platform or provide data to the platform — are not part of the platform's continuity scope, but the platform provides durable retry and backoff for outbound calls to reduce the visibility of short customer-side outages.

## Planned maintenance

Scheduled maintenance windows are announced via the platform's status page and, where customer-facing impact is expected, via email to the designated tenant contacts. The announcement provides at least 14 days' notice, specifies the impacted services and the expected duration, and links to further detail.

Most maintenance work is conducted without customer-visible impact, by using the active-active topology to drain traffic from the region being maintained while work proceeds. The published maintenance windows represent the bounds within which the maintenance may cause observable impact; in the typical case, no observable impact occurs.

## Regulatory and contractual considerations

Specific customer tenants are subject to regulatory or contractual requirements that exceed the platform's default continuity posture — for example, financial-services customers subject to DORA, or healthcare customers subject to HIPAA contingency-planning requirements. The platform's Enterprise tier accommodates contract-level commitments such as longer backup retention, additional region pairings for cross-region replication, and more frequent DR exercise participation.

Customers whose regulatory obligations require participation in the DR exercise program, for example to satisfy an examiner's requirement that the customer has verified the continuity arrangements of a material third-party service provider, may coordinate with their Account Executive to be granted access to summarized results and, in some cases, live participation in a scheduled exercise.
