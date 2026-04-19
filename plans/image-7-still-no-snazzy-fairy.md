# Plan: Re-architect retrieval to Bedrock Knowledge Bases + S3 Vectors

## Context

The demo currently fakes the retrieval layer: the Reference Corpus bucket contains hand-written JSON summaries (one `excerpt` field per "document"), and the retriever Lambda does keyword-overlap scoring across those excerpts. This works for a demo narrative but misrepresents what production retrieval actually looks like and limits the sophistication of the story being told.

The rework replaces this layer with real document storage (synthetic PDFs and markdown) indexed through **Amazon Bedrock Knowledge Bases**, backed by **S3 Vectors** as the vector store. The rest of the architecture — dispatch table, freshness suppression, corroboration rules, scoring, hard rules, review gate — stays identical. The retriever Lambda swaps its S3-keyword adapter for a Bedrock KB `Retrieve` call.

Why this matters:
- Makes the demo honest: real RAG, real chunking, real embeddings, real semantic retrieval
- Demonstrates architect-level judgment: picking S3 Vectors over OpenSearch Serverless is a cost/workload-proportion decision (~$5/mo vs ~$350/mo)
- Preserves every differentiator of the current architecture (source authority, freshness suppression, enterprise-legal hard rules)
- Clean scale-up narrative: S3 Vectors → OpenSearch Serverless when sustained QPS or corpus size justifies it

## Decisions recorded

1. **Vector store: S3 Vectors.** Cheap, serverless, proportional to workload. OpenSearch Serverless reserved as a documented scale-up trigger.
2. **Keep `corroboration_required`.** It enforces source-coverage policy ("if no Primary source hit, cap at amber"), not retrieval quality. Better semantic retrieval doesn't remove the need to flag "compliance document not in corpus."
3. **Use Bedrock KB `Retrieve` API, not `RetrieveAndGenerate`.** Keep the existing Generator Lambda. We need control over prompt structure, output-only guardrail, prompt caching, and JSON output contract. `RetrieveAndGenerate` bypasses all of this.
4. **Document authoring: synthetic PDFs and markdown expanded from existing JSON summaries.** Fastest path to realistic-looking multi-page documents without licensing questions.

---

## Scope summary

### What changes
- S3 Reference Corpus bucket: stores real PDFs/markdown + `.metadata.json` sidecars (instead of pre-summarized JSON)
- New CDK construct: `bedrock.CfnKnowledgeBase` + `bedrock.CfnDataSource` + S3 Vectors bucket
- `lambdas/retriever/handler.py`: replace `_s3_keyword_passages()` with a `bedrock-agent-runtime:Retrieve` call
- `scripts/seed_reference_data.py`: upload PDFs/markdown + metadata sidecars; trigger KB ingestion job
- `lambdas/retriever/handler.py`: Lambda timeout 60s → 90s to account for KB retrieval latency
- IAM: retriever Lambda gains `bedrock:Retrieve` on the KB ARN

### What stays identical
- All DynamoDB tables (Jobs, Questions, Reviews, LibraryFeedback, CustomerRefs)
- Dispatch table + classifier (regex-based; no change)
- Generator Lambda (still Claude Sonnet directly)
- Scorer (composite H/R/C/F/G)
- Hard rules engine
- Review gate + Step Functions flow
- Freshness suppression logic (reads `metadata["updated_at"]` — now populated from KB metadata)
- CustomerRefs and LibraryFeedback DynamoDB access
- Mock Sources Lambda (Seismic/Gong) — untouched; tertiary source, not part of the KB

---

## Implementation plan

### Phase 1 — Generate the corpus artifacts (local, offline)

**New script: `scripts/generate_corpus_documents.py`**
- Reads existing `data/corpus/compliance/*.json` and `data/corpus/product-docs/*.json`
- Expands each JSON's short `excerpt` into a multi-page realistic document:
  - Compliance docs → PDFs (use `reportlab`): SOC 2 Type II report (~8 pages), ISO 27001 certificate package (~4 pages), FedRAMP status memo (~3 pages)
  - Product docs → Markdown: encryption whitepaper, SSO/MFA integration guide, DR/BCP overview (each ~1500-3000 words)
- Writes files to `data/corpus-real/compliance/*.pdf` and `data/corpus-real/product-docs/*.md`
- Generates accompanying `*.metadata.json` sidecars matching Bedrock KB's metadata schema:
  ```json
  {
    "metadataAttributes": {
      "document_id": "soc2_cert_2025",
      "source_type": "compliance_cert",
      "content_validity_date": "2025-09-30",
      "topic_ids": ["soc2"]
    }
  }
  ```
- The sidecar `content_validity_date` is what the retriever reads as `updated_at` (the freshness-suppression comparison field)

**Deliverables:**
- `data/corpus-real/compliance/` — 3-4 PDFs + sidecars
- `data/corpus-real/product-docs/` — 3-4 markdown files + sidecars

### Phase 2 — Add Bedrock KB infrastructure

**Modify `infra/lib/storage-stack.ts`:**
- Reuse existing `ReferenceCorpusBucket` as the KB data source (no new bucket needed)
- Add a new S3 Vectors bucket:
  ```typescript
  this.vectorStoreBucket = new s3.Bucket(this, 'VectorStoreBucket', {
    bucketName: `${prefix}-vectors-${accountId}`,
    encryption: s3.BucketEncryption.S3_MANAGED,
    blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    enforceSSL: true,
    // vector configuration attached via CfnVectorBucket — see note
  });
  ```
- Note: S3 Vectors uses a dedicated `AWS::S3Vectors::VectorBucket` CloudFormation resource (not the standard S3 bucket). Use `CfnVectorBucket` and `CfnVectorIndex` constructs.

**Create `infra/lib/knowledge-base-stack.ts` (new stack):**
- `bedrock.CfnKnowledgeBase` configured with:
  - Storage configuration → S3 Vectors vector bucket ARN
  - Embedding model: `amazon.titan-embed-text-v2:0` (1024 dims; best cost/quality for English at demo scale)
  - Service role with permissions: read from Reference Corpus bucket, write to S3 Vectors, invoke Titan embed model
- `bedrock.CfnDataSource` configured with:
  - Points at `s3://ReferenceCorpusBucket/` (full bucket)
  - Metadata parsing: read `*.metadata.json` sidecar pattern
  - Chunking strategy: default (300 tokens with 20% overlap — good starting point for compliance/product docs)
- CfnOutput: `KnowledgeBaseId`, `DataSourceId`

**Modify `infra/bin/app.ts`:**
- Add `KnowledgeBaseStack` instantiation; depends on `StorageStack`
- Pass `knowledgeBaseId` to `OrchestrationStack`

**Modify `infra/lib/orchestration-stack.ts`:**
- Accept new prop `knowledgeBaseId: string`
- Add env var to retriever Lambda: `KNOWLEDGE_BASE_ID: props.knowledgeBaseId`
- Grant IAM: `retrieverFn.addToRolePolicy(new iam.PolicyStatement({ actions: ['bedrock:Retrieve'], resources: [knowledgeBaseArn] }))`
- Bump retriever timeout: `makeLambda('RetrieverFn', 'retriever', 90)` (was 60s)

### Phase 3 — Rewrite the retriever adapter

**Modify `lambdas/retriever/handler.py`:**

Replace the `_s3_keyword_passages()` function with `_knowledge_base_retrieve()`:

```python
_kb_client = None

def _kb():
    global _kb_client
    if _kb_client is None:
        _kb_client = boto3.client("bedrock-agent-runtime")
    return _kb_client

def _knowledge_base_retrieve(
    query_text: str,
    source_type: str,       # "compliance_cert" | "product_doc"
    source_system: str,     # RetrievedPassage.source_system value
    top_k: int = 6,
) -> list[RetrievedPassage]:
    """Query Bedrock KB with metadata filter for source_type."""
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]
    try:
        resp = _kb().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query_text},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": top_k,
                    "filter": {
                        "equals": {"key": "source_type", "value": source_type}
                    }
                }
            }
        )
    except ClientError as e:
        logger.warning("retriever.kb_error", error=str(e))
        return []

    passages = []
    for r in resp.get("retrievalResults", []):
        meta = r.get("metadata", {})
        passages.append(RetrievedPassage(
            source_system=source_system,
            document_id=meta.get("document_id", r.get("location", {}).get("s3Location", {}).get("uri", "unknown")),
            excerpt=r.get("content", {}).get("text", "")[:2000],
            score=r.get("score", 0.0),
            uri=r.get("location", {}).get("s3Location", {}).get("uri"),
            metadata={
                "updated_at": meta.get("content_validity_date", ""),
                "source_type": meta.get("source_type", ""),
                "topic_ids": meta.get("topic_ids", []),
            }
        ))
    return passages
```

Update the `_retrieve_for_sources()` caller and source routing:
- `compliance_store` → call with `source_type="compliance_cert"`, `source_system="compliance"`
- `product_docs` → call with `source_type="product_doc"`, `source_system="whitepaper"`
- `prior_rfps` → unchanged (still DynamoDB via `_dynamo_prior_matches`)
- Tertiary sources (seismic, gong) → unchanged (still mock_sources Lambda HTTP calls)

**No change to:**
- `_apply_freshness_suppression()` (reads `metadata["updated_at"]` — now populated from KB's `content_validity_date`)
- `_dynamo_prior_matches()` (DynamoDB, untouched)
- `_dynamo_reference_customers()` (DynamoDB, untouched)
- `lambda_handler()` event/return contract (unchanged — downstream stages see identical `Retrieval` object shape)

### Phase 4 — Rewrite the seeder

**Modify `scripts/seed_reference_data.py`:**

1. Remove existing JSON-file upload path
2. Upload PDFs + markdown + metadata sidecars from `data/corpus-real/*` to the Reference Corpus bucket
3. Trigger KB ingestion job:
   ```python
   def ingest_knowledge_base():
       kb_id = get_stack_output("rfp-copilot-dev-knowledge-base", "KnowledgeBaseId")
       ds_id = get_stack_output("rfp-copilot-dev-knowledge-base", "DataSourceId")
       resp = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
       job_id = resp["ingestionJob"]["ingestionJobId"]
       # Poll until COMPLETE or FAILED
       while True:
           status = bedrock_agent.get_ingestion_job(...)["ingestionJob"]["status"]
           if status == "COMPLETE": break
           if status == "FAILED": raise RuntimeError(...)
           time.sleep(10)
   ```
4. Leave CustomerRefs and LibraryFeedback seeding untouched

### Phase 5 — Deploy, ingest, verify

```bash
# 1. Generate corpus artifacts
python3.13 scripts/generate_corpus_documents.py

# 2. Deploy new stacks (storage change, new KB stack, updated orchestration)
cd infra
npx cdk deploy rfp-copilot-dev-storage rfp-copilot-dev-knowledge-base rfp-copilot-dev-orchestration --require-approval never

# 3. Seed the corpus and run KB ingestion
python3.13 scripts/seed_reference_data.py

# 4. Verify ingestion from the CLI:
aws bedrock-agent list-ingestion-jobs --knowledge-base-id <KB_ID> --data-source-id <DS_ID>
```

---

## Files to modify / create

| File | Action |
|---|---|
| `scripts/generate_corpus_documents.py` | CREATE — synthetic PDF/markdown generation |
| `data/corpus-real/compliance/*.pdf` + `*.metadata.json` | CREATE (output of Phase 1) |
| `data/corpus-real/product-docs/*.md` + `*.metadata.json` | CREATE (output of Phase 1) |
| `infra/lib/knowledge-base-stack.ts` | CREATE — new stack for KB + S3 Vectors |
| `infra/lib/storage-stack.ts` | MODIFY — no change to ReferenceCorpusBucket; S3 Vectors bucket may live in KB stack instead |
| `infra/bin/app.ts` | MODIFY — wire in new stack |
| `infra/lib/orchestration-stack.ts` | MODIFY — new env var, IAM grant, timeout bump |
| `lambdas/retriever/handler.py` | MODIFY — replace `_s3_keyword_passages` with `_knowledge_base_retrieve`; update source routing |
| `scripts/seed_reference_data.py` | MODIFY — upload new artifacts + trigger KB ingestion |
| `docs/architecture.md` | UPDATE — reflect KB + S3 Vectors decision, scale-up trigger for OpenSearch |
| `docs/knowledgesources.md` | UPDATE — no longer "pre-summarized JSON"; now "real docs indexed via Bedrock KB" |

---

## Reused functions / existing patterns

From earlier exploration:
- `RetrievedPassage` Pydantic model in `lambdas/shared/models.py:41-49` — unchanged, just populated differently
- `_apply_freshness_suppression()` in `lambdas/retriever/handler.py:258-294` — reused verbatim
- `_dynamo_prior_matches()` in `lambdas/retriever/handler.py:301-346` — reused verbatim
- `_dynamo_reference_customers()` in `lambdas/retriever/handler.py:349-369` — reused verbatim
- `makeLambda()` factory in `infra/lib/orchestration-stack.ts:140-170` — reused for retriever update
- Stack output wiring pattern from existing `rfp-copilot-dev-storage` → consumers in `infra/bin/app.ts`

---

## Verification

1. **Ingestion success:** `aws bedrock-agent list-ingestion-jobs` shows `status=COMPLETE` for the data source
2. **Metadata propagation:** `aws bedrock-agent-runtime retrieve` with a test query returns passages where `metadata.content_validity_date` matches the sidecar value
3. **Freshness suppression still works:**
   - Before: seed a LibraryFeedback record with `approved_at=2025-01-01` referencing `soc2_cert_2025`
   - Upload a new `soc2_cert_2026.pdf` with `content_validity_date=2026-09-30`
   - Re-run KB ingestion
   - Upload a demo RFP with a SOC 2 question
   - Verify Questions table row shows `suppressed_prior_count=1` and the suppressed document's ID
4. **Source-type filtering works:** Classify a SOC 2 question; confirm the retriever only returns chunks where `metadata.source_type=compliance_cert` (not from product_docs)
5. **End-to-end demo:** Upload `demo_rfp_techops.xlsx` and confirm tier distribution is roughly the same as before (mostly green/amber, one red) — the retrieval is better but scoring weights haven't changed, so outcomes should stabilize in a similar range
6. **Cost verification:** After 1 week of usage, confirm S3 Vectors + KB bill is < $10/month via Cost Explorer

---

## Effort + risk

**Estimated effort:** 3-4 focused days.
- Day 1: Generate corpus docs + metadata sidecars (Phase 1). Straightforward.
- Day 2: CDK constructs for KB + S3 Vectors + data source (Phase 2). Risk: S3 Vectors + KB integration is newer; expect some CloudFormation trial-and-error.
- Day 3: Retriever rewrite + seeder rewrite (Phases 3-4). Straightforward given the existing structure.
- Day 4: End-to-end verify + docs update. Buffer for integration issues.

**Main risks:**
- S3 Vectors is newer; CloudFormation support and Bedrock KB integration may have undocumented quirks → mitigate by prototyping one document end-to-end before full migration
- KB ingestion latency on first run (5-10 min for a small corpus) — needs to be built into the seeder as a polling loop, not a fire-and-forget
- Metadata sidecar format requires exact KB-compliant schema; one wrong field name fails silently — validate with CLI `retrieve` test before trusting in retriever code
