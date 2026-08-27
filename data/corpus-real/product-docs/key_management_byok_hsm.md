# Key Management — Hierarchy, Rotation, BYOK and HSM Options

## Scope of this document

Key management is where most of the real work in an encryption programme sits.
The algorithm choice is settled and uncontroversial; what varies between
providers, and what a security reviewer should press on, is where key material
lives, who can cause it to be used, how its use is recorded, and what happens
when the customer wants it destroyed. This document answers those questions.

## The key hierarchy

There are three levels.

A per-message data key seals one message envelope with AES-256-GCM. It is
generated per message from a cryptographically secure random source, exists in
plaintext only in process memory for the duration of a single operation, and is
stored only in wrapped form alongside the ciphertext it sealed.

A per-tenant key-encryption key wraps that tenant's data keys. It lives in a
managed key service and is never exported. Every wrap and unwrap operation
against it is recorded by the key service.

A root key protects the key-encryption keys. Where the tenant elects the
default, the root key is held by the service in a managed key service. Where
the tenant elects to bring its own, the root key is held by the tenant, and the
following sections describe what that changes.

Four key-encryption keys exist per tenant, one for each data class: message
bodies and attachments, message metadata, derived indexes, and event records. A
tenant may bring its own key for one class and leave the others under the
default arrangement.

## Rotation

The default rotation period for a key-encryption key is 365 days. Rotation
generates a new key version, marks it current for new wrap operations, and
retains prior versions for unwrap. Existing envelopes are not rewritten at
rotation time — a stored envelope names the key version that wrapped it, and
unwrapping an older envelope uses the version it names. This is what makes
rotation cheap enough to actually happen on schedule.

A tenant administrator may trigger rotation on demand from the tenant
administration plane, or through the administration API. On-demand rotation
takes effect within minutes and appears in the tenant's event stream. There is
no limit on how often a tenant may rotate, and no charge attached to doing so.

A background re-wrapping process moves envelopes forward onto the current key
version over time, so that retiring an old version eventually becomes possible.
Re-wrapping is rate-limited, resumable, and reports progress per tenant. A
tenant that needs every envelope moved onto a new version within a bounded
window — after a suspected compromise, for example — can request that the
re-wrap be prioritised, and progress is visible while it runs.

Data keys are not rotated, because each one is used exactly once.

## Bring your own key

Two arrangements are offered, and they differ in where the root key material
physically resides.

The first arrangement places a customer-managed key in the customer's own
account with the managed key service. The customer creates the key, grants the
service's workload identity permission to perform wrap, unwrap and describe
operations against it, and supplies the key identifier at tenant provisioning
or later through the administration plane. Two consequences follow. The
customer sees every use of its key in its own key service records, independent
of anything the platform reports. And the customer can revoke the grant
unilaterally at any moment, which takes effect immediately: in-flight
operations fail, and every subsequent read of sealed content returns an
authorisation error from the key service until the grant is restored.

The second arrangement uses an external key store, where root key material
resides in a hardware security module the customer operates, and the managed
key service reaches it over a documented interface. The cryptographic
operations occur inside the customer's hardware security module. This suits
organisations whose own policy or regulator requires that root key material
never reside in a cloud provider's custody at all.

## Hardware security module options

Three postures are available. Under the default, key-encryption keys are held
in the managed key service's shared, validated hardware backing. Under the
dedicated posture, the tenant is assigned an isolated hardware partition, not
shared with other tenants. Under the external posture described above, the
hardware belongs to the customer and sits outside the service boundary
entirely.

The dedicated and external postures are elected at provisioning and can be
changed later, though changing them requires a re-wrap of the affected key
hierarchy and is coordinated with the account team rather than being a
self-service toggle.

## Revocation, destruction and the shape of the guarantee

The customer holding root key material holds a real lever, and it is worth
stating exactly what that lever does and does not do.

Revoking the grant makes sealed content unreadable for as long as the
revocation stands. It is reversible. Nothing is destroyed.

Scheduling destruction of the root key is not reversible once the scheduled
window elapses. After destruction, sealed content protected under that key is
unrecoverable by the customer and by the service alike. This is the property
the arrangement exists to provide, and it is also the reason destruction runs
on a mandatory waiting period rather than taking effect on the button press.

Neither operation reaches backward through copies the customer has already
exported, forwarded messages already delivered in the clear to a recipient
under a tenant policy that permitted it, or plaintext a recipient has saved
elsewhere. A reviewer should read the guarantee as covering content held by the
service, which is what it covers.

## Recording of key use

Every wrap and unwrap operation is recorded twice: once by the key service, and
once in the tenant's own exportable event stream. The two records are produced
by different systems, which is the point — a discrepancy between them is
detectable. Records name the workload identity that performed the operation,
the key version, the tenant, the data class and the outcome. They do not
contain key material or plaintext.
