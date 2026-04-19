# Single Sign-On and Multi-Factor Authentication Integration Guide

## Overview

This guide describes how to integrate a customer's identity provider (IdP) with the platform for workforce single sign-on and multi-factor authentication, and how automated user provisioning and deprovisioning is handled via SCIM.

The integration story rests on three standard protocols — SAML 2.0, OpenID Connect (OIDC), and SCIM 2.0 — and a small number of platform-specific conventions that align with common enterprise IdPs such as Okta, Microsoft Entra ID, Ping Identity, OneLogin, and Google Workspace.

## SAML 2.0 integration

SAML 2.0 is the default protocol for customer-initiated workforce SSO into the platform. The platform acts as a SAML Service Provider (SP); the customer's IdP is the Identity Provider.

To configure SAML SSO, an administrator in the customer's tenant generates the SP metadata from the Administrative Console, providing an Entity ID, Assertion Consumer Service URL, and signing certificate. The administrator then uploads the corresponding IdP metadata (typically as an XML document or via a metadata URL), maps attributes, and enables the integration.

The platform requires the following attributes on every SAML assertion: a NameID in the form of the user's primary email address (the IdP's email domain must match the verified domain registered against the tenant), a GivenName, a FamilyName, and optionally a set of Group memberships that will drive role mapping on the platform.

Assertion signing is required. Assertion encryption is supported and may be enabled by policy; when enabled, the IdP encrypts the assertion to the SP's published encryption certificate. Signed and encrypted assertions are validated before any application session is created.

## OIDC integration

OpenID Connect is supported for customer-initiated SSO as an alternative to SAML, and is required for the platform's native integrations (desktop and mobile clients) where OAuth 2.0 Authorization Code flow with PKCE is used.

The platform supports the Authorization Code, Implicit, and Refresh Token flows. The Authorization Code flow with PKCE is the recommended pattern for new integrations; the Implicit flow is supported only for legacy single-page applications and will be deprecated in a future release.

Tokens are validated against the IdP's JSON Web Key Set (JWKS), with a maximum clock skew of 60 seconds. Access tokens carry a 1-hour lifetime by default, configurable down to 5 minutes for high-assurance contexts; refresh tokens carry a 14-day lifetime and are subject to rotation and revocation on every use.

## SCIM 2.0 provisioning

SCIM 2.0 is supported for automated user and group lifecycle management. The platform exposes a SCIM endpoint per tenant, authenticated via a long-lived bearer token issued to the customer's IdP.

Supported operations include create, update, deactivate, and reactivate for users, as well as create, update, and delete for groups (with membership changes reflected through `PATCH` operations on the group resource). Deactivation has an immediate effect: the user's active sessions are terminated, outstanding access tokens are revoked, and no new sessions may be established until reactivation.

The platform reconciles SCIM data against its own tenant membership at each operation. Conflicts (for example, a duplicate email address across two SCIM-provisioned tenants) are reported via the standard SCIM error responses and logged for the customer's review.

## Multi-factor authentication

Multi-factor authentication is supported for all user tiers and can be enforced by administrator policy. A tenant administrator can require MFA for all users, for a subset of users based on role, or for users with specific privileges (for example, administrators or users with access to exportable audit data).

The following factors are supported:

- Time-based one-time passwords (TOTP), compatible with RFC 6238 authenticator applications such as Google Authenticator, Microsoft Authenticator, 1Password, and Authy.
- WebAuthn / FIDO2 security keys, including roaming authenticators (for example, YubiKey) and platform authenticators (Windows Hello, Touch ID, Android biometric sensors).
- SMS-delivered one-time codes — supported for compatibility and as a fallback option, but not recommended for high-assurance contexts due to well-documented susceptibility to SIM-swap and SS7-interception attacks.

For administrative roles in the Enterprise tier, hardware-backed MFA (WebAuthn or FIDO2) is required; TOTP and SMS are not accepted for these roles regardless of user preference. This reflects the expectation that administrative actions represent the highest impact in a tenant's security posture.

## Session management

Session lifetimes are governed by tenant policy with platform-enforced upper bounds. The default session lifetime for workforce users is 8 hours, configurable to as short as 15 minutes. Idle timeout defaults to 30 minutes, configurable down to 5 minutes.

Administrative sessions carry a reduced maximum lifetime — 4 hours by default, regardless of policy setting — and require step-up reauthentication for high-impact operations such as creating an API key, modifying an SSO configuration, or exporting audit data.

## Troubleshooting

The SSO configuration page in the Administrative Console includes a signed-assertion test tool that allows an administrator to dry-run a SAML assertion from the IdP against the platform's SP configuration without creating a user session. OIDC configurations may be tested via the corresponding OIDC dry-run tool. Both tools produce a detailed error report when validation fails, without exposing the contents of valid assertions for inspection.

Configuration drift — for example, a signing certificate that has expired, or an attribute mapping that has silently changed — is the most common cause of SSO failure observed across tenants. Customers are advised to monitor the platform's configuration change log, which records every modification to the SSO configuration with the identity of the administrator and the timestamp, and to review expiring-certificate notifications that the platform emits 60, 30, and 7 days in advance of the expiry date.

## Group-to-role mapping

Platform roles are derived from group memberships asserted by the IdP. A tenant administrator defines the mapping from IdP groups to platform roles in the Administrative Console; the mapping is evaluated on every session establishment, so changes in group membership at the IdP are reflected on the user's next login.

Three mapping styles are supported. Direct mapping — one IdP group maps to one platform role — is the simplest and is recommended for most tenants. Hierarchical mapping allows a group to inherit the permissions of another group, with circular inheritance detected and rejected at configuration time. Expression-based mapping supports a constrained expression language that allows conditions on the user's attributes (for example, "map to the `finance_analyst` role if the IdP asserts department=Finance and costCenter=1200"). Expression-based mapping trades flexibility for inspectability; we recommend direct mapping wherever the IdP's group model can support it.

Users who belong to multiple groups receive the union of the permissions conferred by each mapped role, subject to any deny rules configured at the tenant level. A user who belongs to no mapped group at the time of session establishment is denied access and is redirected to an administrator-configured error page, typically pointing the user at the tenant administrator for remediation.

## Just-in-time provisioning

For tenants that prefer not to operate SCIM, the platform supports just-in-time (JIT) user provisioning on the first successful SSO. On JIT, the platform creates a user record from the asserted attributes, assigns the roles implied by the group-to-role mapping, and establishes the application session. On subsequent logins, the attributes and group memberships are refreshed from the latest assertion.

JIT provisioning and SCIM are mutually exclusive on a per-tenant basis: a tenant elects one or the other at integration time. JIT is simpler to configure but provides weaker guarantees — for example, deactivation in the IdP does not propagate to the platform until the user next attempts to log in (and is denied because they are no longer in a mapped group), or until a lifecycle sweep runs on the scheduled cadence. SCIM provides immediate propagation of lifecycle events through the IdP's provisioning engine, at the cost of configuring and maintaining the SCIM connection.

## Break-glass access

Every tenant maintains a break-glass account, independent of the primary SSO configuration, to provide continuity in the event of an IdP outage or misconfiguration. The break-glass account authenticates with a strong password and a hardware-backed MFA factor, is issued to a named administrator, and is subject to heightened monitoring — every authentication with the break-glass account generates a real-time notification to the tenant's security contact list.

Break-glass accounts are not intended for routine use. The platform emits a reminder notification quarterly if a tenant's break-glass account has not been exercised in the preceding 90 days, prompting the administrator to complete a short recovery drill (log in, verify access, log out) so that the credentials remain usable when needed.

## Step-up authentication for sensitive operations

Certain operations — creating or revoking an API key, modifying the SSO configuration itself, exporting audit data for a date range exceeding 30 days — require step-up authentication even within an existing session. Step-up authentication prompts the user to re-present an MFA factor within the last 5 minutes of wall-clock time. The requirement holds regardless of the user's role; a tenant administrator who has been signed in for the last six hours will be prompted for MFA before creating a new API key.

Step-up is implemented through the IdP where the IdP supports the `acr_values` parameter of OIDC or the `AuthnContextClassRef` element of SAML. For IdPs that do not support explicit step-up, the platform enforces the requirement through its own secondary MFA challenge.

## Migration scenarios

Migrating SSO from one IdP to another — for example, because of a corporate acquisition — follows a dual-provider pattern. A new SAML or OIDC configuration is added alongside the existing one; users are granted access through either configuration for a defined transition window; the old configuration is disabled after verification that all active users have been migrated. The transition window is bounded by the tenant administrator's policy, typically between 30 and 90 days.

During the transition, audit logs record the configuration under which each session was established, so that the administrator has full visibility into migration progress. After the transition, the configuration change log retains the history of the migration for the audit retention period.
