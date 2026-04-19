# Production RAG Accuracy — retrieval, hallucination, and how real pipelines defend against it

**Companion to:** `docs/architecture.md`, `docs/knowledgesources.md`, `docs/scoringmechanism.md`
**Last updated:** 2026-04-16

This doc answers the question: *if we replaced our demo's hand-summarised JSON excerpts with a real production pipeline over a compliance corpus — Textract, chunking, indexing, LLM generation — isn't the whole thing susceptible to hallucinations? Is this semantic matching? How do real production shops actually handle this?*

Short answer: yes it's susceptible, yes it's semantic matching (and more), and production defenses are layered — no single technique is sufficient on its own.

---

## 1. Yes, it's semantic matching — but real production is hybrid

In the demo we cheat: the retriever does keyword-overlap scoring against pre-summarised JSON excerpts. Good enough to demo the *architecture*. Hopeless as a real retrieval mechanism.

The real-world pipeline for a compliance corpus looks like this:

```
PDF (60 pages)
  ↓  Textract — OCR + layout extraction (tables, forms, headings preserved)
Structured text with positional metadata
  ↓  Semantic chunker — ~500-1000 tokens, respects section boundaries
[chunk_1, chunk_2, ..., chunk_N]
  ↓  Embedding model (Titan v2, Cohere embed-v3, text-embedding-3-large)
[vector_1, vector_2, ..., vector_N]  — each ~1024-3072 dimensions
  ↓  Index writer
Vector store (OpenSearch k-NN / Pinecone / Aurora pgvector / S3 Vectors / Kendra)
```

At query time:

```
User question → embed → vector similarity search (cosine/dot product) → top-K chunks
                      → BM25 keyword match → top-K chunks
                      → reciprocal rank fusion / reranker → final top-5
```

**This is called hybrid search**, and it's now the baseline for production RAG. Pure semantic (vectors only) misses exact matches on acronyms, version numbers, and proper nouns — exactly the kinds of tokens that matter in compliance ("SOC 2", "ISO 27001:2022", "control CC6.1"). Pure lexical (BM25) misses paraphrases. You need both.

**Then you add a reranker.** First-pass retrieval casts a wide net (top-100 chunks). A cross-encoder — Cohere Rerank, BGE reranker, ColBERT — then scores question-vs-chunk pairs more expensively and narrows to top-5. Precision at that final step matters more than anything else because it's what lands in the LLM's context window.

### AWS managed options for each stage

| Stage | DIY | Managed (AWS) |
|---|---|---|
| PDF → text | Tesseract + custom layout code | **Textract** |
| Chunking | LangChain recursive splitter, custom | Built into Bedrock Knowledge Bases |
| Embeddings | Self-hosted models on SageMaker | **Bedrock Titan Embeddings** |
| Vector store | OpenSearch + k-NN plugin, pgvector | **OpenSearch Serverless** (vector mode), **S3 Vectors** (new), **Aurora PostgreSQL + pgvector** |
| Retrieval + generation orchestration | Write it yourself | **Bedrock Knowledge Bases** (whole pipeline as a service) |
| End-to-end enterprise search | Build from parts | **Amazon Kendra** (native connectors, hybrid search, ACLs) |

For a production compliance corpus, the sensible starting point today is **Bedrock Knowledge Bases pointed at an S3 bucket** — it handles ingestion, chunking, embedding, and retrieval without you writing any of the glue. You pay per document and per query.

---

## 2. Yes, it's still susceptible to hallucinations

Retrieval gives the LLM the right *material*. It does not guarantee the LLM will *use* that material faithfully. Compliance-specific failure modes I've actually seen in production pipelines:

- **Specificity drift.** Doc says "SOC 2 Type II, Security and Availability." LLM writes "SOC 2 Type II across all five trust principles." The doc was present in context. The model paraphrased wrong.
- **Version confabulation.** Doc says "ISO 27001:2013." LLM writes "ISO 27001:2022." Looks right, is wrong.
- **Scope inflation.** Doc says "our US-East region is FedRAMP Moderate authorized." LLM writes "FedRAMP Moderate authorized" (dropping the region scope).
- **Fake citation.** LLM produces a citation like `[soc2_report_2024]` that wasn't in the retrieval results at all. The model invented a plausible-looking citation.
- **Date fabrication.** Doc has no expiry date for a cert; LLM confidently asserts one.

The naïve RAG pattern — "retrieve, stuff into prompt, ask LLM to answer" — does not prevent any of these.

---

## 3. How production compliance pipelines defend against it

No single defense is sufficient. You stack them.

### 3.1 Citation-forced generation + citation verification

Prompt the LLM to produce output with explicit citations: `"We are SOC 2 Type II certified for Security and Availability [soc2_cert_2025]."` Then **post-process**: check that every `[doc_id]` it cited actually appears in the retrieval results. If the LLM invented a citation, reject the answer and either regenerate or flag it for human review. This catches "fake citation" failures deterministically.

### 3.2 Grounding verification (self-critique loop)

After generation, run a second LLM call: *"Given this passage: [retrieved chunk]. Given this claim: [generated sentence]. Does the passage support the claim? Answer yes/no with reasoning."* This is a ~$0.001 extra call per answer and catches a large fraction of specificity-drift and confabulation failures.

Bedrock Guardrails has a built-in feature for exactly this, called **Contextual Grounding Check**. You pass the retrieved context and the generated text; it returns a grounding score and a relevance score. Below a threshold → block the answer.

### 3.3 Extractive before abstractive

For factual compliance questions, prefer **extractive QA** over **abstractive**:
- **Extractive:** return the verbatim passage that answers the question, with its citation. "According to the SOC 2 report: *Audit period 2024-10-01 to 2025-09-30. Trust principles: Security, Availability.*" No paraphrasing, no hallucination surface.
- **Abstractive:** let the LLM summarise/rephrase. More natural-sounding, more risk.

Production pipelines often try extractive first and fall back to abstractive only when extractive confidence is low (no single chunk has a confident answer span).

### 3.4 Structured extraction for key facts

Compliance content has *structured* facts hiding inside unstructured prose: cert names, date ranges, control IDs, trust principles, audit firms. Extract those once during ingestion using LLM function-calling, and store them in a structured table:

```json
{
  "document_id": "soc2_cert_2025",
  "cert_type": "SOC 2 Type II",
  "audit_firm": "[CPA firm name]",
  "period_start": "2024-10-01",
  "period_end": "2025-09-30",
  "trust_principles": ["Security", "Availability"],
  "qualified_opinion": false
}
```

At answer time, for questions like "what's the SOC 2 audit period?" you look up the structured record — no RAG, no LLM, no hallucination surface. **Facts that can be structured, should be.**

### 3.5 Temperature discipline + prompt hygiene

For factual extraction: temperature 0 (or very low). Higher temperature = more creative = more confabulation. Prompt template should include explicit grounding instruction:

> *"Based ONLY on the provided passages, answer the question. Cite your sources inline using [doc_id]. If the information is not in the passages, respond with 'Information not available in provided sources' — do NOT guess or infer."*

Obvious in retrospect, skipped in a depressing number of production systems.

### 3.6 Hard human-in-the-loop for compliance specifically

This is what our architecture does at its final layer. **Compliance claims are capped at amber regardless of LLM confidence.** The system treats "the LLM said this correctly" as necessary but not sufficient evidence — a human SME must confirm before a compliance answer ships green.

This isn't lazy. It's acknowledging that **LLM confidence ≠ factual correctness**, and that for regulatory claims, the cost of being wrong is high enough to justify the review friction. Hard-rule #2 ("compliance claim without citation → min amber") is specifically the backstop for all of the failure modes above.

---

## 4. How this maps back to our architecture

Our system uses a *subset* of these defenses:

| Defense | Our implementation | Status |
|---|---|---|
| Citation-forced generation | Prompt requires `[doc_id]` citations; we parse them into the `citations` field | ✅ Implemented |
| Citation verification | We trust the generator's output list | ❌ Production addition |
| Grounding verification | No — would add a Bedrock Contextual Grounding Check call | ❌ Production addition |
| Extractive before abstractive | Abstractive-only. Adding extractive would improve green rates for compliance | ❌ Production addition |
| Structured extraction for key facts | The compliance JSONs have facts pre-extracted by hand (the `excerpt` is really a summary). Production would extract from PDFs | ❌ Production addition |
| Temperature discipline | `temperature=0.2` in generator | ✅ Implemented |
| Hybrid retrieval (semantic + lexical + reranker) | Keyword-overlap only against pre-summarised JSON | ❌ Production addition |
| Hard human-in-the-loop | Compliance claims min-amber; SME review gate | ✅ Implemented |

**The hard human-in-the-loop is doing a *lot* of work in our current design.** It's explicit acknowledgment that we haven't fully solved the hallucination problem at the LLM layer, and that we're using *process* (SME review) to cover what the tech can't yet guarantee. That's a legitimate design choice — especially for compliance — but it's a cost center, and the point of adding defenses 3.1–3.5 is to shrink the amber pile so SMEs review fewer answers.

---

## 5. The production upgrade path for this project

If we were moving this to a real compliance corpus today, in order of ROI:

1. **Swap the demo retriever for Bedrock Knowledge Bases.** Point it at the Reference Corpus S3 bucket. Instantly get real chunking, embeddings, and hybrid retrieval without writing any of it. ~1 day of work.

2. **Add Bedrock Contextual Grounding Check post-generation.** One extra API call per answer, catches specificity drift and scope inflation. ~half a day of work, ~$0.001 per answer.

3. **Add citation verification.** Parse `[doc_id]` citations from the generated `answer_text`, check each appears in the retrieval results. Reject answers with fake citations. ~half a day, deterministic, zero cost.

4. **Structured extraction for the compliance corpus.** Run a one-time job with LLM function-calling to extract `{cert_type, period_start, period_end, trust_principles, ...}` per document. Store in a new DynamoDB table. Route "what's the audit period?" questions to the table, not to RAG. ~2 days, eliminates a whole class of hallucinations.

5. **Add a reranker step.** If using OpenSearch/pgvector directly, add Cohere Rerank between retrieval and generation. If using Bedrock Knowledge Bases, this may already be in the retrieval API. ~half a day.

6. **Prefer extractive QA for factual questions.** Add a pre-step that asks: "Does the question have a verbatim answer in the retrieved chunks?" If yes, return the verbatim span. Only fall back to generation for open-ended questions. ~2 days.

Each of these individually shrinks the amber pile. Together they can get compliance green rates from ~20% to ~60% (typical production numbers), with the remaining 40% being genuinely ambiguous or novel questions that *should* be reviewed.

---

## 6. The honest answer

**Is it susceptible to hallucinations?** Yes, and no amount of "better prompting" fully solves it at the LLM layer. You need retrieval quality, citation verification, grounding checks, and human review as layered defenses.

**Is this semantic matching?** Yes, and in production you want *hybrid* — semantic + lexical + reranking — because compliance content has proper nouns and version numbers that pure vector similarity misses.

**How is it done in the real world?** Bedrock Knowledge Bases or Kendra for the retrieval stack, Bedrock Contextual Grounding Check for the hallucination backstop, structured extraction for anything that can be structured, and a human review step for everything that touches regulatory claims.

The mature answer is never *"just trust the LLM."* It's *"defense in depth, and the LLM is one layer among five."*

Our demo-tier architecture uses the cheap defenses (temperature, prompting, human review) and skips the expensive ones (vector index, reranker, grounding check). The production path is to add them one at a time as green rates plateau.
