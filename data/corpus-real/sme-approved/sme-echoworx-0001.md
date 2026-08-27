# SME-Approved Q&A — sme-echoworx-0001

**Approved:** r.calloway@echoworx.example on 2026-07-14  
**Topics:** —  
**Corroborated by:** encryption_in_transit_tls

## Question

Can recipients open encrypted messages using a third-party secure email gateway from another vendor, without enrolling in our recipient portal?

## Answer

Yes. Where the recipient's organisation operates its own secure email gateway, delivery is negotiated gateway-to-gateway over enforced TLS with MTA-STS, and the message is delivered into the recipient's ordinary mailbox with no enrolment step and no portal account. S/MIME and OpenPGP delivery are also available where the recipient publishes a usable certificate or key. The recipient portal is the fallback for recipients whose organisation offers no interoperable path, not the default. Interoperability with another vendor's gateway is a delivery-policy setting per partner domain, configured by the tenant administrator.
