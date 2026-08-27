# Encryption in Transit — TLS Profile and SMTP Delivery

## Scope of this document

Two distinct transport problems live under the heading "encryption in transit"
for an email encryption service, and conflating them produces confused
answers. The first is the ordinary web and API transport between a client and
the service. The second is message delivery over SMTP to a recipient's mail
infrastructure, which the service does not control. This document covers both,
in that order.

## Web and API transport

Every externally reachable listener requires TLS 1.2 or TLS 1.3. TLS 1.0 and
TLS 1.1 are refused, not merely deprioritised: a client offering only those
versions receives a handshake failure. SSL 3.0 and earlier have not been
accepted for many years.

The accepted cipher suite list is short and forward-secret only. Under TLS 1.3
the service accepts TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256 and
TLS_AES_128_GCM_SHA256. Under TLS 1.2 it accepts ECDHE key exchange with ECDSA
or RSA authentication and an AEAD bulk cipher — the AES-GCM and
ChaCha20-Poly1305 families. Static RSA key exchange, CBC-mode suites and
anything without forward secrecy are absent from the list. The consequence of
requiring forward secrecy is that recorded traffic does not become readable
later if a long-term server private key is compromised.

HTTP Strict Transport Security is asserted on every public web endpoint with a
one-year max-age and the includeSubDomains directive, and the relevant domains
are on the browser preload lists. A plaintext request to a customer-facing
endpoint receives a redirect, and every subsequent request from that browser is
upgraded before it leaves the client.

Certificates are issued from a public certification authority with certificate
transparency logging, use ECDSA P-256 keys, and are renewed automatically well
ahead of expiry. Renewal failure is alerted on days before it could become
customer-visible, because an expired certificate is one of the few failures
that takes an otherwise healthy service completely offline.

## Service-to-service transport

Inside the service boundary, connections between components use mutual TLS.
Workload identity is established by short-lived certificates issued from an
internal private certification authority, with lifetimes measured in hours and
automatic renewal. A component presents its own certificate and validates its
peer's, so a process that has somehow obtained network reachability to an
internal endpoint still cannot talk to it without a valid workload identity.

## SMTP delivery to recipient infrastructure

This is where the honest answer is more complicated than the one a
questionnaire usually invites.

Ordinary SMTP negotiates TLS opportunistically. A sending server offers
STARTTLS, and if the receiving server does not support it, the message is
delivered in the clear. Opportunistic TLS is also downgradable by an active
network attacker who strips the STARTTLS advertisement. So "we use TLS for
email" is, on its own, a weak statement about email.

The service therefore supports three stronger positions, selectable by the
tenant per partner domain.

Enforced TLS requires a successful TLS negotiation with a validated
certificate for the named partner domain. If negotiation fails or validation
fails, the message is not delivered in the clear — it is queued, and if the
condition persists it falls back to the tenant's configured alternative method
rather than to plaintext.

MTA-STS publishes and consumes policy over HTTPS so that a sending server
learns, out of band, that a receiving domain requires TLS. This closes the
STARTTLS-stripping downgrade for domains that publish a policy. The service
publishes its own MTA-STS policy in enforce mode and honours published policies
on outbound delivery.

DANE uses DNSSEC-signed TLSA records to bind the expected certificate to the
receiving host, which closes the same downgrade for domains that have deployed
DNSSEC. The service validates TLSA records on outbound delivery where they are
present and DNSSEC validates.

## When transport security is not enough

Transport protects the hop. It does not protect the message once it lands. A
message delivered over enforced TLS into a recipient mailbox sits in that
mailbox in whatever state the recipient's own infrastructure keeps it.

Where the tenant's policy requires protection beyond the hop, the policy engine
selects a message-level method instead: S/MIME or OpenPGP where the recipient
publishes a usable certificate or key, or delivery into the recipient portal
where no interoperable path exists. The selection is a per-domain policy
decision the tenant administrator makes, not a per-message guess, and the
decision taken for each message is recorded in the delivery event stream.

## Reporting and verification

The service publishes SMTP TLS Reporting endpoints and consumes reports for its
own domains, so a partner's failure to negotiate is visible rather than silent.
Tenant administrators see, per partner domain, the negotiated version, the
selected suite and the validation outcome for delivery attempts. A reviewer who
wants to verify the external position independently can inspect the published
MTA-STS policy and the TLSA records directly.
