# Multi-Factor Authentication — Supported Factors and Policy

## Scope of this document

This document covers the second factor for workforce sign-in to the tenant:
which factors are available, how policy selects between them, how step-up works
for sensitive operations, and how enrolment and recovery are handled. Where the
tenant federates to its own identity provider, the second factor may be
enforced there instead, and the interaction between the two is described below.

## Supported factors

Four factor types are available.

WebAuthn and FIDO2, covering both roaming authenticators such as security keys
and platform authenticators such as a laptop's biometric sensor. This is the
recommended factor and the only one that is phishing-resistant, because the
assertion is bound to the origin and a credential presented to a lookalike
domain simply does not verify.

Time-based one-time passwords per RFC 6238, compatible with any standard
authenticator application. Widely supported and easy to enrol, but
phishing-relayable: a user can be persuaded to read a code into an attacker's
prompt.

Push approval through the service's mobile client, with number matching. The
number-matching requirement exists to defeat approval fatigue — a user cannot
approve by reflex, because approving requires reading a number from the screen
they initiated the sign-in on.

One-time codes delivered by short message service. Available for compatibility
and as a recovery path. This factor is the weakest of the four and is
documented as such: subscriber identity module transfer attacks and signalling
network interception are both well established. Tenant policy may disable it
entirely, and many tenants do.

## Policy

Tenant policy determines who must present a second factor and which factors are
acceptable. Policy can require a second factor for every sign-in, for
administrative roles only, or only for step-up on sensitive operations. It can
restrict the acceptable factor set — for example, permitting only WebAuthn for
users holding administrative roles while allowing the broader set for ordinary
senders.

Where a role can read message content or change delivery policy, the service
requires a phishing-resistant factor and does not permit the tenant to relax
that. This is one of a small number of settings the tenant cannot configure
downward, and it is stated plainly rather than buried, because a reviewer is
entitled to know which controls are floors and which are defaults.

## Interaction with federated sign-in

Where the tenant federates, the second factor is normally enforced at the
customer's identity provider, and the service consumes the result. The service
reads the authentication context class reference in a SAML assertion, or the
corresponding claim in an OpenID Connect identity token, and applies tenant
policy to what it finds. A tenant can require that the assertion demonstrate a
phishing-resistant factor and reject sessions that do not.

Where the identity provider does not express the context, or where the tenant
prefers belt and braces, the service can challenge for its own second factor on
top of the federated sign-in. Doing both is a supported configuration, and some
regulated tenants choose it.

## Step-up for sensitive operations

Certain operations require a fresh factor presentation regardless of how long
the session has been open: creating or revoking an API credential, changing the
federation configuration, changing delivery policy for a partner domain,
changing key management settings, and exporting event records over a range
longer than thirty days.

Fresh means presented within the last five minutes of wall-clock time. An
administrator signed in for six hours is challenged before creating a
credential. The requirement applies to every role, including the highest, and
cannot be configured away.

## Enrolment and recovery

A user enrols a factor on first sign-in, or when policy changes to require one.
The service encourages enrolling two factors of different types, because the
common failure is a single lost device rather than a compromise.

Recovery is deliberately not self-service for administrative roles. A user who
has lost every enrolled factor is recovered by a tenant administrator, and a
tenant administrator who has lost every enrolled factor is recovered through
the break-glass account or, failing that, through a verification procedure with
the account team that involves a named contact and a callback to a number on
record. Self-service recovery by email link is not offered, because it collapses
the second factor into the first.

Every enrolment, every removal and every recovery is written to the tenant's
event stream with the acting identity, the affected user and the time.
