# Encryption at Rest — AES-256-GCM Envelope Design

## Scope of this document

This document describes how message bodies, attachments, message metadata and
derived search indexes are protected while stored by the cloud email encryption
service. It covers the algorithm and mode, the envelope structure, where each
kind of key lives, and what an attacker who obtains stored ciphertext can and
cannot do with it. Key rotation, bring-your-own-key and hardware security
module options are described in the key management document; this document
covers the data path.

## Algorithm and mode

Data at rest is sealed with AES-256 in Galois/Counter Mode, written throughout
as AES-256-GCM. Three properties drive that choice.

The mode is authenticated. GCM produces an authentication tag alongside the
ciphertext, and decryption fails closed if the tag does not verify. Silent
corruption of stored ciphertext, whether from a storage fault or from
tampering, surfaces as a decryption failure rather than as plausible-looking
garbage delivered to a recipient.

The key length is 256 bits. This is not because 128-bit AES is considered
breakable, but because the symmetric margin is the cheapest margin available:
the performance difference is small on modern hardware with AES-NI, and a
256-bit symmetric key retains a 128-bit security level against the best known
quantum search technique.

The nonce is 96 bits, generated from a cryptographically secure random source
per encryption operation and stored alongside the ciphertext. Nonce reuse under
the same key is catastrophic in GCM, so the design never reuses a data key
across more messages than the birthday bound allows — in practice each message
gets its own data key, which makes the question moot.

## Envelope structure

Each stored object carries an envelope with four parts: the AES-256-GCM
ciphertext, the 96-bit nonce, the 128-bit authentication tag, and the wrapped
data key. The wrapped data key is the data key itself, encrypted under the
tenant key-encryption key held in the managed key service.

The envelope is versioned. A version byte precedes the structure so that the
algorithm, the mode or the envelope layout can be changed without a flag day:
readers dispatch on the version, writers emit the current version, and a
background re-wrapping process moves older envelopes forward. This matters more
than it sounds. The common reason an organisation cannot move off a
cryptographic primitive is not that the new primitive is hard, it is that the
stored format has no version field and every reader must be changed at once.

## Where keys live and where they do not

Data keys exist in plaintext only inside process memory, only for the duration
of a single encrypt or decrypt operation, and are zeroed after use. They are
never written to disk in the clear, never logged, and never included in a
diagnostic capture — the structured event pipeline redacts the field by schema
rather than by pattern match, so a new field name cannot accidentally leak one.

Tenant key-encryption keys never leave the managed key service. The service
holds a handle, not the key. To decrypt anything, a process must be able to
call the unwrap operation on the tenant's key-encryption key, and every such
call is recorded by the key service independently of the platform's own event
records.

The practical consequence: an attacker who obtains a complete copy of stored
objects — every ciphertext, every nonce, every tag, every wrapped data key —
has no path to plaintext without also obtaining the ability to call the unwrap
operation against the tenant's key-encryption key.

## Separation by data class

Four data classes are separated under distinct key-encryption keys within a
tenant: message bodies and attachments, message metadata, derived search
indexes, and event records. Separation costs little and buys two things. A
policy change that restricts access to one class does not disturb the others.
And the retention schedules differ per class, so the keys age out on different
schedules without entangling them.

Derived search indexes deserve a note. An index over encrypted content is a
classic place where confidentiality leaks back out, because index structures
reveal term frequency. The service does not build a plaintext inverted index
over message bodies. Recipient-visible search operates on the metadata class —
sender, recipients, subject where the tenant permits it, timestamps — and that
class is sealed with the same envelope as everything else.

## Storage-layer redundancy

Sealing happens before the object reaches storage, so every replica of every
object is sealed. Replication between regions inside a residency zone moves
ciphertext, not plaintext; the receiving region has no more access to the
plaintext than the network in between does. Backup images are taken of the
sealed objects. There is no point in the storage path where an object exists in
the clear.

## What this control does not do

Encryption at rest closes a specific set of exposures: theft of storage media,
recovery of decommissioned hardware, an infrastructure-level misconfiguration
that exposes an object store, and a backup image that ends up somewhere it
should not. It does not defend against an attacker who has obtained a valid
tenant session, a service process that is entitled to unwrap the data key and
has been compromised, or a recipient who forwards a decrypted message onward.
Those exposures are addressed by access control, workload isolation and
delivery policy, described in their own documents. Stating the boundary
plainly is more useful to a reviewer than implying that one control covers
everything.
