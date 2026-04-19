# External Tools — the Seismic + Gong mock Lambda, and what a real integration looks like

**Companion to:** `docs/architecture.md`, `docs/knowledgesources.md`
**Last updated:** 2026-04-17

This doc answers the question: *for the Seismic/Gong knowledge source — a Lambda that exposes two routes — what data does that Lambda actually access, and how would it look if we wired it to the real SaaS APIs?*

Short answer: **the Lambda doesn't access anything external**. The "data" is inline Python lists baked into the deployment package. The Lambda simulates the *shape* of a real integration (auth, latency, failure modes) without implementing any of the storage or network plumbing. Real Seismic/Gong integrations look completely different.

---

## 1. What's actually in the Lambda

Open `lambdas/mock_sources/handler.py` and you'll find two hardcoded Python lists:

**`_SEISMIC_CARDS`** — 5 dicts, each simulating one content card:
- `seismic-card-001` — "Enterprise Security Overview One-Pager" (SOC 2, ISO 27001, FedRAMP in progress, encryption specs)
- `seismic-card-002` — "FAQ — Reference Customer Stories (Financial Services)"
- `seismic-card-003` — "Data Residency Configuration Matrix"
- `seismic-card-004` — "SSDLC Summary Deck" (Semgrep, Snyk, Trivy, STRIDE, pentest)
- `seismic-card-005` — "SSO and MFA Integration Guide"

**`_GONG_CALLS`** — 4 dicts, each simulating one call transcript:
- `gong-call-001` — Discovery call, prospect asking about FedRAMP timeline
- `gong-call-002` — Technical deep-dive on encryption and key management
- `gong-call-003` — Procurement call, DPA and sub-processors
- `gong-call-004` — Security architecture call, incident response and pentest

Each entry has `id`, `title`, `excerpt`, `updated_at` (or `date`), and topic tags. The excerpts are short natural-sounding paraphrases of what a real card/transcript would contain.

When the Lambda gets `GET /seismic/content?query=encryption`, it does this:

1. Check for an `Authorization` header (any value works; 401 if missing). Simulates OAuth.
2. Roll a die: 5% chance → return `503` immediately (trips circuit breaker). Otherwise sleep 30–80ms (normal latency), with a 10% chance of sleeping 2000ms (tail latency).
3. Score each of the 5 Seismic cards against the query using the same keyword-overlap scorer the retriever uses.
4. Return the top-K matching cards (default 5) as JSON.

That's it. No S3 read. No DynamoDB read. No network call to anything real. **The Lambda deployment package *is* the data store.**

---

## 2. Why it's inline and not in S3

Three reasons, all pragmatic:

**1. Self-containment.** Makes the Lambda independently deployable and testable. You can unit-test `_handle_seismic_content` without mocking S3. You can invoke the deployed Lambda with a test event and it just works — no dependencies to bootstrap.

**2. Zero runtime cost per call.** S3 GET is cheap (~$0.0004 per 1,000 calls) but it's not free, and it adds ~10ms to the latency. For a mock that's supposed to simulate *a specific latency profile* (50ms normal, 2s tail), adding an unpredictable S3 read under the hood would contaminate the simulation.

**3. Demo discipline.** The mock is deliberately small (9 total items). Making it "more realistic" by storing 500 cards in S3 would push us toward actually building a real retrieval system against mock data — which is not the point. The point is to demonstrate *the shape* of an external SaaS integration: auth header, flakiness, rate limits, timeouts.

---

## 3. What a real Seismic integration would look like

Totally different shape. The Lambda would be a **thin proxy**, not a data store:

```
retriever Lambda
  ↓ HTTP GET with OAuth bearer token
https://api.seismic.com/v2/content/search?query=encryption&limit=5
  ↓
Seismic's infrastructure
  ↓ 200 OK with JSON results
retriever Lambda
```

The real Seismic REST API:

- **Auth:** OAuth 2.0 Client Credentials flow. You exchange `client_id` + `client_secret` for a short-lived bearer token. Token cached (Secrets Manager or DynamoDB with TTL) and refreshed before expiry.
- **Endpoints:** `GET /v2/content/search`, `GET /v2/libraries/{id}/contents`, `GET /v2/contents/{id}/download` — searchable library, then per-asset fetch.
- **Permissions:** each content item has an ACL. The API returns only what your token's scope permits — this is one of the reasons Kendra's "ACL inheritance" feature matters at scale.
- **Rate limits:** per-tenant, per-endpoint, burst + sustained.
- **Pagination:** cursor-based.
- **Payload:** JSON metadata + a download URL (actual files are typically S3-backed on Seismic's side, delivered via presigned URLs that expire).

**Where would the data live?** On Seismic's servers, not yours. You *never* bulk-download the corpus. Each query hits their API, respects their ACLs, and you cache the results for a short TTL (minutes, not days) so stale permissions don't leak.

A real Gong integration is similar shape:

- **Auth:** API key in `Authorization: Basic <base64(access_key:secret)>` header, or OAuth.
- **Endpoints:** `POST /v2/calls/extensive` (search/filter with a complex body), `GET /v2/calls/{id}/transcript`, `GET /v2/calls/{id}/detailed`.
- **Payload:** transcripts can be huge — full call text with speaker diarisation, timestamps, topic tags. You paginate and often stream.

---

## 4. Why the inline mock is still architecturally honest

Even though the data is fake and local, the Lambda simulates the *patterns* that matter:

| Production behavior | How the mock represents it |
|---|---|
| Auth required | 401 if no `Authorization` header |
| Variable latency | 50ms normal, 2000ms tail (10% of calls) |
| Transient failures | 5% random 503 |
| Rate limits | Not simulated (could be added easily) |
| Pagination | Not simulated (`limit` query param truncates) |
| ACL filtering | Not simulated |
| Keyword-based search | Simple overlap score on title + excerpt |

What the **retriever Lambda** does with the results is identical whether this data comes from an inline list or a real Seismic tenant: calls the endpoint, handles `503`s with circuit-breaker counting, timestamps the result for freshness scoring, and hands passages back to the generator. The retriever's code doesn't know or care that the data is fake.

This is the architectural lesson: **the integration surface is the contract, not the storage medium.** A real integration swaps the storage and network layer while leaving the retriever untouched.

---

## 5. Gradations of "more realistic" without a real SaaS account

If you wanted to move further toward production-realism without actually signing up for Seismic/Gong:

### Gradation 1 — move the data to S3

Create `s3://…-mock-sources/seismic/*.json` and `s3://…-mock-sources/gong/*.json`. Have the Lambda read and filter on each call.

- **You gain:** data editable without a Lambda redeploy; can simulate arbitrary corpus size.
- **You lose:** deterministic latency, self-contained testability.

### Gradation 2 — LocalStack or WireMock

Run a real-looking API server locally or in a container that serves OpenAPI-conformant Seismic/Gong responses.

- **You gain:** actual HTTP integration testing, closer to real error modes, OAuth flow you can exercise.
- **You lose:** the demo simplicity — now you have an extra service to deploy and maintain.

### Gradation 3 — real Seismic/Gong sandbox accounts

Both vendors offer developer/sandbox tenants. You'd get real OAuth, real rate limits, real ACLs, real pagination semantics.

- **You gain:** the integration is no longer a simulation.
- **You lose:** demo portability — the system now depends on external credentials.

Neither of these is needed for the demo scope. The inline lists are the right choice because they *illustrate* the integration pattern without *implementing* it.

---

## 6. The request flow, end to end

When the retriever calls `https://<api-gw>/seismic/content?query=encryption`, the request travels:

```
API Gateway (receives HTTPS GET)
  ↓ invokes with proxy-style event
mock_sources Lambda (Python runtime, container warm)
  ↓ reads
A Python list that was packaged into the Lambda .zip at deploy time
  ↓ scores + filters in-memory
  ↓ returns JSON
API Gateway returns to caller
```

No storage, no database, no external call. The Lambda deployment package itself is the "data store" — a few kilobytes of JSON-ish Python objects embedded in the handler file.

That's deliberately minimal. The architectural lesson is *how the retriever interacts with an external source* (auth, latency, failure modes, circuit breaker), not *what the source's storage looks like internally*. A real integration replaces the inline lists with actual HTTP calls; nothing else in the pipeline has to change.

---

## 7. The one-line takeaway

**The demo's Seismic/Gong Lambda is not a gateway to any data — it's a simulator of external-SaaS *behaviour*, with just enough content baked in to make the demo narrative land.** The architectural work lives in how the retriever *consumes* this mock, not in the mock itself. When you move to real integrations, you replace the Lambda's inline lists with proper HTTP clients, OAuth flows, and response caching — and the retriever doesn't need to change.
