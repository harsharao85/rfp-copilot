# Completed CAIQ v4.0.3 extract — Audit Assurance and Supply Chain

Submitted 22 September 2025. Reviewed and approved for outbound use by the
security assurance team.

## A&A-01 — What independent assurance reports do you hold?

A SOC 2 Type II report covering Security, Availability and Confidentiality, and
ISO/IEC 27001:2022 certification with a Statement of Applicability available
under non-disclosure. Conformance to ISO/IEC 27018:2019 for the processing of
personally identifiable information in public cloud is attested alongside the
ISO/IEC 27001 surveillance activity.

## A&A-02 — How often are these renewed?

The SOC 2 Type II report is produced annually against a twelve-month period.
ISO/IEC 27001 follows the standard three-year cycle with annual surveillance.
Internal audit covers the whole management system annually, independently of
the external cycle.

## A&A-05 — Do you commission independent penetration testing?

Yes, annually, by an independent testing firm, covering the internet-facing
gateway and its SMTP listeners, the recipient portal, the administration plane
and API, and the externally reachable interfaces of the key management plane.
An executive summary is available on request; the full technical report is
released under a mutual non-disclosure agreement.

## STA-05 — Do you produce a software bill of materials?

Yes. A signed CycloneDX 1.5 bill of materials is generated at build time for
every deployable artefact from the resolved dependency graph, and retained for
the supported life of the release plus twelve months. Customers under a mutual
non-disclosure agreement may obtain the bill of materials for any release they
are running, in CycloneDX or SPDX.

## STA-09 — How are sub-processors and suppliers assessed?

Before onboarding, against the criteria in the supplier management procedure,
and annually thereafter. Contractual security terms are no less protective than
those we owe our own customers. Sub-processors are disclosed in the published
register with thirty days' notice before any change takes effect.

## STA-14 — What are your vulnerability remediation targets?

Seven days for critical, thirty days for high, ninety days for medium, best
effort for low, measured from triage to verified fix. Targets expected to be
missed are escalated before the deadline rather than after.

## Reviewer note

Approved against the assurance evidence current at the date of submission. The
SOC 2 report period cited here has since been superseded — always cite the
current report.
