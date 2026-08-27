# Single Sign-On — SAML 2.0 and OpenID Connect

## Scope of this document

The service federates sign-in for two distinct populations, and reviewers
frequently conflate them. Tenant administrators and internal senders are the
customer's own workforce, and they federate against the customer's identity
provider. Message recipients are, by definition, outside the customer's
identity provider, and their authentication is covered separately in the
recipient portal documentation. This document covers workforce federation.

## SAML 2.0

SAML 2.0 is the default protocol for workforce federation. The service acts as
a service provider; the customer's identity provider issues assertions.

Configuration is self-service from the tenant administration plane. The
administrator downloads the service provider metadata, which contains the
entity identifier, the assertion consumer service endpoint and the signing and
encryption certificates. The administrator then supplies identity provider
metadata, either as an uploaded document or as a metadata URL the service
refreshes periodically. Supplying a metadata URL is strongly preferred: it lets
a signing certificate rollover at the identity provider propagate without
anyone remembering to update the tenant, which is the single most common cause
of a federation outage.

Assertions must be signed. Assertion encryption is optional and, when enabled,
uses the published encryption certificate. Signature validation, audience
restriction, recipient and destination checks, and the NotBefore and
NotOnOrAfter conditions are all enforced before any session is established.
Clock skew tolerance is sixty seconds and is not configurable upward.

Required attributes are a name identifier carrying the user's primary email
address in a domain verified against the tenant, a given name and a family
name. Group membership claims are optional and drive role assignment.

Both identity-provider-initiated and service-provider-initiated flows are
supported. RelayState is validated against an allowlist of tenant-owned
destinations so that a federated sign-in cannot be used to redirect a user to
an arbitrary location.

## OpenID Connect

OpenID Connect is supported as an alternative for workforce federation and is
the protocol used by the desktop and mobile clients, which run the
authorisation code flow with PKCE.

Supported flows are authorisation code and authorisation code with PKCE, plus
refresh. The implicit flow is not supported for new configurations. Identity
tokens are validated against the identity provider's published key set, with
issuer, audience, nonce and expiry all checked, and sixty seconds of clock skew
tolerance. Key sets are refreshed on a schedule and on an unknown key
identifier, which lets a provider rotate signing keys without coordination.

Access tokens issued by the service carry a one-hour lifetime by default,
reducible to five minutes by tenant policy. Refresh tokens carry a fourteen-day
lifetime, are rotated on every use, and a replayed refresh token invalidates
the whole chain — which turns a stolen refresh token into a detectable event
rather than a silent persistent foothold.

## Group to role mapping

Roles inside the tenant derive from group claims asserted by the identity
provider. The administrator defines the mapping, which is evaluated at every
session establishment, so a change of group membership at the identity provider
takes effect on the user's next sign-in rather than at some later
reconciliation.

Direct mapping — one claimed group to one tenant role — is the recommended
style and covers most tenants. Hierarchical mapping lets one role inherit
another's permissions, with circular inheritance rejected when the mapping is
saved rather than at evaluation time. A user in several mapped groups receives
the union of the permissions, less any deny rule configured at the tenant
level. A user in no mapped group is denied and shown an administrator-supplied
message rather than a generic error.

## Break-glass access

Every tenant keeps one break-glass account that does not depend on the
federation configuration, so that an identity provider outage or a broken
federation configuration does not lock the customer out of its own tenant. The
break-glass account uses a strong password and a hardware-backed second factor,
is issued to a named individual, and every sign-in with it raises an immediate
notification to the tenant's registered security contacts.

The service reminds the tenant quarterly if the break-glass account has not
been exercised in ninety days, because a break-glass credential that has never
been tested tends not to work on the day it is needed.

## Migration between identity providers

Moving from one identity provider to another runs as a dual configuration. The
new configuration is added alongside the existing one; both are live for a
transition window the administrator sets, typically thirty to ninety days;
users are admitted through either; and the old configuration is removed once
the event records show no sessions being established through it. Each session
record names the configuration that established it, so migration progress is
observable rather than guessed at.

## Diagnosing a broken configuration

The administration plane includes a dry-run tool that validates an assertion or
an identity token against the tenant's configuration without creating a
session, and reports precisely which check failed. Configuration drift — an
expired signing certificate, a changed attribute name, a renamed group — causes
the large majority of federation failures. The service emits notifications
sixty, thirty and seven days before a configured certificate expires, and
records every change to the federation configuration with the identity of the
administrator who made it and the time it took effect.
