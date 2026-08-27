# SCIM 2.0 User and Group Provisioning

## Scope of this document

This document describes automated lifecycle management of tenant users and
groups through a System for Cross-domain Identity Management endpoint, the
alternative just-in-time path, and the reconciliation behaviour when the two
sides disagree.

## The endpoint

Each tenant exposes a SCIM 2.0 endpoint at a per-tenant base path. The endpoint
is authenticated with a bearer credential the administrator generates in the
tenant administration plane. Credentials are shown once, are individually
revocable, carry an optional expiry, and are scoped to provisioning operations
only — a provisioning credential cannot read message content.

The endpoint implements the standard resource types. For users: create, replace,
patch, deactivate and reactivate. For groups: create, replace, patch and
delete, with membership changes expressed as patch operations against the group
resource. Filtering, pagination and the ServiceProviderConfig, ResourceTypes and
Schemas discovery endpoints are implemented, which is what lets an identity
provider configure itself against the tenant without hand-written mappings.

## Attribute mapping

The core user schema is supported, plus the enterprise user extension. The
external identifier is the stable key; the user name carries the primary email
address, which must be in a domain verified against the tenant. Given name,
family name, display name, active status, title, department and manager are
mapped. Unrecognised attributes are accepted and ignored rather than rejected,
because an identity provider that sends a superset of what a target consumes is
the normal case and failing the whole request over it produces support tickets
rather than security.

Group membership drives role assignment through the same mapping used by
federated sign-in, so a tenant that uses both federation and provisioning
maintains one mapping rather than two.

## Deactivation semantics

Deactivation is immediate and total. Setting a user inactive ends every live
session for that user, revokes every issued token, and refuses new sessions
until the user is reactivated. There is no grace period and no scheduled sweep
in between.

This is the single most important property of the provisioning path and the
reason it is preferred over the just-in-time alternative. When a customer
terminates an employee, the identity provider issues one patch and the user is
out, in seconds, without anyone at the customer remembering that this service
exists.

Deactivation preserves the user record and everything attributed to it. Message
history, event records and role assignments remain intact and attributable. A
reactivated user resumes with the same identity rather than appearing as a new
one, which is what keeps historical records coherent across an employee's
leave, transfer or return.

Hard deletion of a user resource is supported and is distinct from
deactivation: it removes the identity, and it is refused where retention policy
requires the record to persist, with a clear error rather than a silent
partial effect.

## Just-in-time provisioning

Tenants that do not operate provisioning may instead enable just-in-time
creation on first federated sign-in. The service creates the user from the
asserted attributes, assigns roles from the group mapping, and establishes the
session. On each later sign-in the attributes and roles are refreshed from the
current assertion.

The two paths are mutually exclusive per tenant. Just-in-time is simpler to
turn on and materially weaker: a user removed at the identity provider is not
deactivated here until they next attempt to sign in and fail, or until the
lifecycle sweep runs. For a tenant handling regulated content that gap is
usually unacceptable, which is why provisioning is the recommended path and
just-in-time is documented as the fallback.

## Reconciliation and conflicts

Every operation is reconciled against current tenant membership. Conflicts
return the standard SCIM error responses rather than a generic failure: a
duplicate user name returns 409 with a uniqueness error, a malformed patch
returns 400 with the invalidSyntax type, an unknown resource returns 404. The
error body names the offending attribute, so an administrator reading their
identity provider's failure log can act without opening a ticket.

Conflicts are also written to the tenant's event stream, so a systematic
mismatch between the identity provider's view and the tenant's — the usual
symptom of a half-completed migration — is visible as a pattern rather than as
scattered individual failures.

## Rate limiting and bulk changes

The endpoint is rate limited per tenant, with limits set well above the rate a
normal identity provider generates and returning 429 with a Retry-After header
when exceeded. Standards-conforming provisioning clients honour it.

Bulk operations are supported for the initial load, where a tenant onboards
several thousand users at once. A bulk request is processed as a unit with
per-operation results, so a single bad record does not fail the load. Large
initial loads are best coordinated with the account team, not because the
service cannot absorb them, but because a load that half-succeeds against a
misconfigured mapping is easier to prevent than to unpick.
