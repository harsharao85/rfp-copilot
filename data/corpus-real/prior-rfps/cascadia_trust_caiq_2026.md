# Completed CAIQ v4.0.3 extract — Identity and Access Management

Submitted 2 April 2026. Reviewed and approved for outbound use by the security
assurance team.

## IAM-03 — Is a single sign-on solution supported for tenant users?

Yes. Workforce sign-in federates over SAML 2.0 or OpenID Connect against the
customer's identity provider. Signed assertions are mandatory and encrypted
assertions are optional. Configuration is self-service, and supplying identity
provider metadata by URL is recommended so that a signing certificate rollover
propagates without manual intervention.

## IAM-04 — Do you support multi-factor authentication for all tenant users?

Yes. WebAuthn and FIDO2, time-based one-time passwords, push approval with
number matching, and one-time codes by short message service are supported.
Tenant policy determines who must present a second factor and which factors are
acceptable. Roles that can read message content or change delivery policy
require a phishing-resistant factor, and that requirement cannot be relaxed by
tenant configuration.

## IAM-09 — Is automated provisioning and deprovisioning supported?

Yes, through a per-tenant SCIM 2.0 endpoint supporting create, update,
deactivate and reactivate for users and create, update, delete and membership
patch for groups. Deactivation is immediate: live sessions end, issued tokens
are revoked, and new sessions are refused until reactivation. A just-in-time
alternative exists for tenants that do not operate provisioning, and is
documented as materially weaker for exactly this reason.

## IAM-12 — How are privileged sessions controlled?

Privileged operations require a factor presented within the last five minutes
regardless of session age. This covers credential creation and revocation,
federation configuration changes, delivery policy changes for a partner domain,
key management settings, and event exports spanning more than thirty days. The
requirement applies to every role including the highest.

## IAM-14 — Are access rights reviewed periodically?

Yes, quarterly for standing access to production, with the review recorded and
the record retained. The review was tested without exception in the most recent
independent assessment.

## Reviewer note

Approved against the identity documentation current at the date of submission.
