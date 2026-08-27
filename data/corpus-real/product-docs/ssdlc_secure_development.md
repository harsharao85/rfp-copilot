# Secure Development Lifecycle — Design, Build and Release Controls

## Scope of this document

This document describes how security is built into the way software for the
cloud email encryption service is designed, written, reviewed, built and
released. It covers threat modelling, the controls that gate a change into the
main branch, the build and release path, and how the team handles the
inevitable case where something ships and turns out to be wrong.

## Threat modelling

Every service has a threat model, and the model is a living document rather
than an artefact produced once for an assessor. A model is produced when a
service is created and revisited whenever the trust boundary moves — a new
external interface, a new data class handled, a new dependency inside the
boundary, or a change to who can reach what.

Modelling uses a structured decomposition: what the component holds, who talks
to it, where the boundaries are, and what an attacker on each side of each
boundary could attempt. Each identified threat gets a disposition: mitigated by
a named control, accepted with a recorded rationale and owner, or transferred.
Undispositioned threats block the design from proceeding, which is the only
mechanism that reliably prevents a threat model from becoming decorative.

Models are stored with the service they describe, in the same repository, so
they are reviewed in the same change process as the code and go stale visibly
rather than quietly.

## What gates a change into the main branch

A change cannot merge until five things hold.

A peer code review is recorded from someone other than the author. Reviews are
mandatory, and the branch protection enforces it rather than relying on the
team's discipline. Changes touching cryptographic code, the policy engine, the
key management plane or the tenant isolation boundary require a second reviewer
drawn from a named group with the relevant depth.

Static analysis passes with no new findings above the configured threshold.
Existing findings are tracked with owners and dates rather than being
suppressed silently; a suppression requires a comment naming the reason, and
suppressions are reviewed in bulk quarterly.

Dependency composition analysis passes. A new dependency with a known
vulnerability above the threshold blocks the merge. An existing dependency that
develops a known vulnerability raises a tracked item with a remediation target
by severity, and blocks the next release if the target is missed.

Secret scanning passes on the diff and on the full history of the branch. A
detected credential fails the change and triggers revocation of the detected
credential before anything else happens, on the assumption that a credential in
a diff is already exposed.

The test suite passes, including the tests that specifically exercise tenant
isolation and delivery policy selection. Those tests are treated as security
controls rather than as quality checks, and weakening one requires the same
second review as changing cryptographic code.

## Build and release

Builds run in an isolated environment from a clean checkout, with no
interactive access and no ambient credentials. Build inputs are pinned by
digest rather than by tag, so a rebuilt artefact is the same artefact.

Every deployable artefact is signed at build time, and the deployment path
verifies the signature before an artefact may run. A software bill of materials
is produced for every artefact and retained for the supported life of the
release; the bill of materials policy document covers its contents and
disclosure.

Container images are scanned before promotion between environments. Images are
rebuilt on a schedule regardless of whether the application code changed, so
that base-layer fixes reach production without waiting for a feature.

Infrastructure is defined as code and applied through the same review path as
application changes. Manual change in production is prevented by policy rather
than discouraged by convention, and detected drift raises an alert.

## Environments and data

Development and pre-production environments never contain production message
content. Test data is synthetic and generated. This removes an entire class of
exposure — the pre-production database restored from production and left
reachable — rather than managing it.

Pre-production is configured to match production in structure, so that a
control that works in one works in the other, and so that the penetration test
programme can run against pre-production with confidence that the result
transfers.

## Vulnerability handling and disclosure

Findings arrive from static analysis, dependency analysis, image scanning, the
annual independent test, the bug bounty programme and customers. All are
triaged into one queue with a severity and an owner, because a finding's route
of arrival says nothing about its importance.

Remediation targets by severity are seven days for critical, thirty days for
high, ninety days for medium, and best effort for low, measured from triage to
verified fix. Targets that are going to be missed are escalated before they are
missed, not after.

A published disclosure policy invites external researchers to report findings,
commits to acknowledging within two business days and to keeping the researcher
informed, and undertakes not to pursue legal action against good-faith research
conducted within the stated scope.

## When something ships wrong

The team runs blameless reviews after any change that causes a customer-visible
failure or that introduces a security weakness later found by someone else. The
output is a set of changes to the system or the process, each with an owner and
a date, tracked in the same place as feature work. The review asks what about
the environment made the mistake likely, not who made it. That framing is what
keeps the reports honest, and an honest report is the only kind worth writing.
