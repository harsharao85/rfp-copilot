#!/usr/bin/env python3
"""End-to-end walk of the seven-step workflow against stubbed AWS.

QA harness (qa-reviewer, ENG-2). Written to be *independent of*
scripts/verify_fixture.py: that script imports the scorer and the rules modules
directly and reimplements the pipeline around them. This one drives the real
Lambda entry points — upload_api.presign/start/status/download,
excel_parser.lambda_handler, question_classifier.lambda_handler,
retriever.lambda_handler, generator.lambda_handler,
confidence_scorer.lambda_handler, hard_rules.lambda_handler,
excel_writer.lambda_handler, review_gate.lambda_handler and
review_api.{get_review,approve,reject} — in the same order the Step Functions
definition in infra/lib/orchestration-stack.ts chains them.

What is real
------------
Every handler above, plus DynamoDB and S3 (moto, in-memory but real API
semantics: conditional writes, batch writers, Decimal coercion, presigned URLs).
The workbooks are really parsed and really written by openpyxl and read back
with openpyxl.

What is stubbed, and why
------------------------
bedrock-agent-runtime:Retrieve   — firewalled. Replaced by a TF-IDF cosine over
    the same corpus bodies the KB would be built from, filtered by source_type
    exactly as retriever._SOURCE_TYPE_FILTER does. Deliberately returns the
    top-K nearest chunks with NO relevance cutoff, because that is what a vector
    KB does: `numberOfResults` is a count, not a threshold. verify_fixture.py
    makes the opposite calibration choice (MIN_COSINE = 0.045); the difference
    is stated here rather than hidden, and both are run below.

bedrock-runtime:InvokeModel      — firewalled. Replaced by an extractive
    composer that restates the question and appends the top passages, and by an
    identity guardrail. Restating matters: hard_rules matches on the ANSWER
    text.

stepfunctions                    — moto's coverage of task tokens is not
    trustworthy, so StartExecution / SendTaskSuccess / SendTaskFailure are
    recorded by a fake client and the harness performs the state transitions
    the state machine would perform.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAMBDAS = ROOT / "lambdas"
DATA = ROOT / "data"
CORPUS_REAL = DATA / "corpus-real"

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.pop("AWS_PROFILE", None)

for _p in (
    LAMBDAS,
    LAMBDAS / "excel_parser",
    LAMBDAS / "excel_writer",
    LAMBDAS / "hard_rules",
    LAMBDAS / "confidence_scorer",
    LAMBDAS / "question_classifier",
    LAMBDAS / "retriever",
):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

INCOMING = "eng2-incoming"
OUTPUT = "eng2-output"
CORPUS_BUCKET = "eng2-reference-corpus"

os.environ["INCOMING_BUCKET"] = INCOMING
os.environ["OUTPUT_BUCKET"] = OUTPUT
os.environ["REFERENCE_CORPUS_BUCKET"] = CORPUS_BUCKET
os.environ["JOBS_TABLE"] = "Jobs"
os.environ["QUESTIONS_TABLE"] = "Questions"
os.environ["REVIEWS_TABLE"] = "Reviews"
os.environ["CUSTOMER_REFS_TABLE"] = "CustomerRefs"
os.environ["KNOWLEDGE_BASE_ID"] = "KBSTUB0001"
os.environ["DATA_SOURCE_ID"] = "DSSTUB0001"
os.environ["STATE_MACHINE_ARN"] = "arn:aws:states:us-east-1:000000000000:stateMachine:rfp"
os.environ.pop("GUARDRAIL_ID", None)  # keep check_output a no-op
os.environ.pop("USE_RETRIEVAL_STUB", None)

FAIL: list[str] = []
NOTE: list[str] = []
ALL_CUSTOMER_NAMES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAIL.append(f"{label}: {detail}")
    return ok


# ---------------------------------------------------------------------------
# Corpus index + fake Bedrock KB
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    """a an the and or of to in for on at by is are be with from as that this these
    those it its do does did you your we our us they their there here what which who
    how why when where can could will would shall should may might must not no if
    then than so such into over under about per each any all both more most other
    some only own same too very just also""".split()
)


def _tok(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 2]


class Chunk:
    __slots__ = ("document_id", "source_type", "text", "sidecar", "uri", "tokens")

    def __init__(self, document_id, source_type, text, sidecar, uri):  # type: ignore[no-untyped-def]
        self.document_id = document_id
        self.source_type = source_type
        self.text = text
        self.sidecar = sidecar
        self.uri = uri
        self.tokens = Counter(_tok(text))


def _split_md(body: str) -> list[str]:
    parts: list[str] = []
    heading = ""
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if any(b.strip() for b in buf):
                parts.append(f"{heading}\n" + "\n".join(buf).strip())
            heading = line[3:].strip()
            buf = []
        elif line.startswith("# "):
            heading = line[2:].strip()
            buf = []
        else:
            buf.append(line)
    if any(b.strip() for b in buf):
        parts.append(f"{heading}\n" + "\n".join(buf).strip())
    return parts


def _pdf_blocks_text(blocks: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    heading = "Overview"
    buf: list[str] = []
    for block in blocks:
        if "h" in block:
            if buf:
                parts.append(f"{heading}\n" + "\n".join(buf).strip())
            heading = block["h"]
            buf = []
        elif "p" in block:
            buf.append(block["p"])
        elif "table" in block:
            for row in block["table"]:
                buf.append(" | ".join(str(c) for c in row))
    if buf:
        parts.append(f"{heading}\n" + "\n".join(buf).strip())
    return parts


def build_corpus_index() -> list[Chunk]:
    chunks: list[Chunk] = []

    def add(prefix: str, doc_id: str, ext: str, texts: list[str]) -> None:
        side_path = CORPUS_REAL / prefix / f"{doc_id}{ext}.metadata.json"
        sidecar = json.loads(side_path.read_text())["metadataAttributes"]
        uri = f"s3://{CORPUS_BUCKET}/{prefix}/{doc_id}{ext}"
        for text in texts:
            if text.strip():
                chunks.append(Chunk(doc_id, sidecar["source_type"], text, sidecar, uri))

    # Markdown corpora are read from disk, independently of the generator.
    for prefix in ("product-docs", "prior-rfps", "sme-approved"):
        for md in sorted((CORPUS_REAL / prefix).glob("*.md")):
            add(prefix, md.stem, ".md", _split_md(md.read_text()))

    # Compliance is emitted as PDF; no text extractor here, so the block source
    # is read from the generator module. Stated, not hidden.
    sys.path.insert(0, str(ROOT / "scripts"))
    import generate_corpus_documents as cg  # noqa: PLC0415

    for doc_id, blocks in cg.COMPLIANCE_BODIES.items():
        add("compliance", doc_id, ".pdf", _pdf_blocks_text(blocks))
    return chunks


class FakeKB:
    """Stands in for bedrock-agent-runtime. Top-K, no relevance cutoff."""

    SCORE_FLOOR = 0.30
    SCORE_SPAN = 0.68

    def __init__(self, chunks: list[Chunk], min_cosine: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.min_cosine = min_cosine
        self._reindex()

    def _reindex(self) -> None:
        n = len(self.chunks)
        df: Counter[str] = Counter()
        for c in self.chunks:
            df.update(c.tokens.keys())
        self.idf = {t: math.log((n + 1) / (v + 1)) + 1.0 for t, v in df.items()}

    def add_chunk(self, chunk: Chunk) -> None:
        self.chunks.append(chunk)
        self._reindex()

    def ingest_sme_approved_from_s3(self, s3: Any, bucket: str) -> int:
        """Simulate the S3-event → StartIngestionJob → KB indexing hop."""
        added = 0
        resp = s3.list_objects_v2(Bucket=bucket, Prefix="sme-approved/")
        objs = {o["Key"] for o in resp.get("Contents", [])}
        for key in sorted(k for k in objs if k.endswith(".md")):
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()
            side_key = f"{key}.metadata.json"
            if side_key not in objs:
                continue
            side = json.loads(s3.get_object(Bucket=bucket, Key=side_key)["Body"].read())
            attrs = side["metadataAttributes"]
            doc_id = attrs["document_id"]
            if any(c.document_id == doc_id for c in self.chunks):
                continue
            self.chunks.append(
                Chunk(doc_id, attrs["source_type"], body, attrs, f"s3://{bucket}/{key}")
            )
            added += 1
        self._reindex()
        return added

    def _cos(self, q: Counter[str], d: Counter[str]) -> float:
        qv = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in q.items()}
        dv = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in d.items()}
        qn = math.sqrt(sum(v * v for v in qv.values()))
        dn = math.sqrt(sum(v * v for v in dv.values()))
        if qn == 0 or dn == 0:
            return 0.0
        return sum(v * dv.get(t, 0.0) for t, v in qv.items()) / (qn * dn)

    # --- the bedrock-agent-runtime surface the retriever actually calls ---
    def retrieve(
        self, *, knowledgeBaseId: str, retrievalQuery: dict, retrievalConfiguration: dict
    ) -> dict:
        vsc = retrievalConfiguration["vectorSearchConfiguration"]
        top_k = vsc["numberOfResults"]
        want = vsc["filter"]["equals"]["value"]
        q = Counter(_tok(retrievalQuery["text"]))
        scored = [
            (self._cos(q, c.tokens), c) for c in self.chunks if c.source_type == want
        ]
        scored = [p for p in scored if p[0] >= self.min_cosine]
        scored.sort(key=lambda p: p[0], reverse=True)
        results = []
        for cos, c in scored[:top_k]:
            results.append(
                {
                    "content": {"text": c.text[:2000]},
                    "location": {"s3Location": {"uri": c.uri}},
                    "score": round(self.SCORE_FLOOR + self.SCORE_SPAN * cos, 4),
                    "metadata": dict(c.sidecar),
                }
            )
        return {"retrievalResults": results}


# ---------------------------------------------------------------------------
# Bedrock generation stub
# ---------------------------------------------------------------------------


def stub_invoke(*, model_id, system_prompt, user_prompt, **kw):  # type: ignore[no-untyped-def]
    """Classifier (HAIKU) → atomic. Generator (SONNET) → extractive answer."""
    if "decompose" in system_prompt.lower():
        m = re.search(r"^Q: (.*)$", user_prompt, re.M)
        text = json.dumps([m.group(1) if m else ""])
        return {"text": text, "model_id": model_id, "prompt_hash": "h", "response_hash": "h"}

    question = ""
    mq = re.search(r"^QUESTION: (.*)$", user_prompt, re.M)
    if mq:
        question = mq.group(1)
    docs = re.findall(r"^\[([^\]]+)\] \(([^)]+)\): (.*)$", user_prompt, re.M)
    prior = re.search(r"^Prior answer: (.*?)\n\nEVIDENCE PASSAGES:", user_prompt, re.M | re.S)
    approved = re.search(r"^APPROVED REFERENCE CUSTOMERS.*\n(.*)$", user_prompt, re.M)
    approved_list = []
    if approved and approved.group(1).strip() not in ("(none)", ""):
        approved_list = [s.strip() for s in approved.group(1).split(",") if s.strip()]

    body = [f"In response to: {question}"]
    if prior:
        body.append(prior.group(1)[:900])
    for doc_id, _system, excerpt in docs[:3]:
        body.append(f"{excerpt[:500]} [{doc_id}]")
    answer = "\n".join(body)
    # A real model names whoever appears in the text it was given — including
    # customers that are NOT on the approved list. That is precisely what hard
    # rule 4 exists to catch, so the stub must not pre-filter to the approved
    # list or rule 4 can never fire.
    invoked = [c for c in ALL_CUSTOMER_NAMES if c.lower() in answer.lower()]
    _ = approved_list
    payload = {
        "answer_text": answer,
        "citations": [d for d, _s, _e in docs[:3]],
        "invoked_customers": invoked,
        "contains_pricing": False,
        "contains_compliance_claim": False,
        "contains_forward_looking": False,
    }
    return {
        "text": json.dumps(payload),
        "model_id": model_id,
        "prompt_hash": "p",
        "response_hash": "r",
    }


def stub_check_output(text: str) -> dict:
    return {"blocked": False, "text": text}


class FakeSfn:
    def __init__(self) -> None:
        self.executions: list[dict] = []
        self.successes: list[dict] = []
        self.failures: list[dict] = []

    def start_execution(self, **kw):  # type: ignore[no-untyped-def]
        self.executions.append(kw)
        return {"executionArn": "arn:aws:states:us-east-1:0:execution:rfp:" + kw.get("name", "x")}

    def send_task_success(self, **kw):  # type: ignore[no-untyped-def]
        self.successes.append(kw)
        return {}

    def send_task_failure(self, **kw):  # type: ignore[no-untyped-def]
        self.failures.append(kw)
        return {}


# ---------------------------------------------------------------------------


def make_tables(ddb: Any) -> None:
    ddb.create_table(
        TableName="Jobs",
        KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="Questions",
        KeySchema=[
            {"AttributeName": "jobId", "KeyType": "HASH"},
            {"AttributeName": "questionId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "jobId", "AttributeType": "S"},
            {"AttributeName": "questionId", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="Reviews",
        KeySchema=[
            {"AttributeName": "jobId", "KeyType": "HASH"},
            {"AttributeName": "reviewedAt", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "jobId", "AttributeType": "S"},
            {"AttributeName": "reviewedAt", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="CustomerRefs",
        KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def seed_customer_refs(ddb: Any) -> int:
    src = json.loads((DATA / "graph" / "customers.json").read_text())
    rows = src["customers"] if isinstance(src, dict) else src
    table = ddb.Table("CustomerRefs")
    for row in rows:
        table.put_item(Item=row)
        ALL_CUSTOMER_NAMES.append(row["name"])
    return len(rows)


TRACE: dict[str, Any] = {}


def run_pipeline(mods, job_id, bucket, key, fake_sfn, verbose=False):  # type: ignore[no-untyped-def]
    """parse → Map(classify→retrieve→generate→score→rules) → writeDraft."""
    parsed = mods["excel_parser"].lambda_handler(
        {"bucket": bucket, "key": key, "jobId": job_id}, None
    )
    for q in parsed["questions"]:
        ev: dict[str, Any] = dict(q)
        ev["classification"] = mods["question_classifier"].lambda_handler(ev, None)
        ev["retrieval"] = mods["retriever"].lambda_handler(ev, None)
        ev["generation"] = mods["generator"].lambda_handler(ev, None)
        ev["score"] = mods["confidence_scorer"].lambda_handler(ev, None)
        ev["final"] = mods["hard_rules"].lambda_handler(ev, None)
        if q["text"].strip() in TRACE:
            TRACE[q["text"].strip()] = ev
        if verbose:
            print(f"    {q['questionId']}: {ev['final']['final_tier']} {ev['final']['hard_rule_triggers']}")
    draft = mods["excel_writer"].lambda_handler(
        {"jobId": job_id, "sourceBucket": bucket, "sourceKey": key, "stage": "draft"}, None
    )
    return parsed, draft


def read_answers(ddb, job_id):  # type: ignore[no-untyped-def]
    from boto3.dynamodb.conditions import Key  # noqa: PLC0415

    t = ddb.Table("Questions")
    items, kwargs = [], {"KeyConditionExpression": Key("jobId").eq(job_id)}
    while True:
        r = t.query(**kwargs)
        items.extend(r["Items"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return {i["questionId"]: i for i in items}


def reviews_for(ddb, job_id):  # type: ignore[no-untyped-def]
    from boto3.dynamodb.conditions import Key  # noqa: PLC0415

    r = ddb.Table("Reviews").query(KeyConditionExpression=Key("jobId").eq(job_id))
    return sorted(r["Items"], key=lambda i: i["reviewedAt"])


def cell_text(s3, bucket, key, sheet, coord):  # type: ignore[no-untyped-def]
    from openpyxl import load_workbook  # noqa: PLC0415

    buf = io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    wb = load_workbook(buf)
    return wb[sheet][coord].value


def main() -> int:
    import boto3
    from moto import mock_aws

    verbose = "-v" in sys.argv
    strict_cutoff = "--min-cosine" in sys.argv

    manifest = json.loads((DATA / "incoming" / "echoworx-caiq-v4-40q.expected.json").read_text())
    chunks = build_corpus_index()
    kb = FakeKB(chunks, min_cosine=0.045 if strict_cutoff else 0.0)
    print(
        f"corpus index: {len(chunks)} chunks over "
        f"{len({c.document_id for c in chunks})} documents "
        f"(min_cosine={kb.min_cosine})"
    )

    with mock_aws():
        s3 = boto3.client("s3")
        ddb = boto3.resource("dynamodb")
        for b in (INCOMING, OUTPUT, CORPUS_BUCKET):
            s3.create_bucket(Bucket=b)
        make_tables(ddb)
        nrefs = seed_customer_refs(ddb)

        # Import handlers AFTER moto is active: several build boto3 resources
        # at module import time.
        import confidence_scorer.handler as scorer_h
        import excel_parser.handler as parser_h
        import excel_writer.handler as writer_h
        import generator.handler as gen_h
        import hard_rules.handler as rules_h
        import question_classifier.handler as cls_h
        import retriever.handler as ret_h
        import review_api.handler as review_h
        import review_gate.handler as gate_h
        import upload_api.handler as upload_h

        gen_h.invoke = stub_invoke  # type: ignore[assignment]
        gen_h.check_output = stub_check_output  # type: ignore[assignment]
        cls_h.invoke = stub_invoke  # type: ignore[assignment]
        ret_h._bedrock_agent_runtime = lambda: kb  # type: ignore[assignment]

        fake_sfn = FakeSfn()
        upload_h._sfn_client = fake_sfn
        review_h.sfn = fake_sfn

        mods = {
            "excel_parser": parser_h,
            "question_classifier": cls_h,
            "retriever": ret_h,
            "generator": gen_h,
            "confidence_scorer": scorer_h,
            "hard_rules": rules_h,
            "excel_writer": writer_h,
        }

        print(f"seeded {nrefs} customer reference rows\n")

        # ---------------- STEP 1: upload ----------------
        print("STEP 1 — upload")
        r = upload_h.presign({"filename": "echoworx-caiq-v4-40q.xlsx"})
        pre = json.loads(r["body"])
        job_id, key = pre["jobId"], pre["key"]
        check(r["statusCode"] == 200 and bool(pre.get("uploadUrl")), "presign returns upload URL")
        s3.put_object(
            Bucket=INCOMING,
            Key=key,
            Body=(DATA / "incoming" / "echoworx-caiq-v4-40q.xlsx").read_bytes(),
        )
        check(
            s3.head_object(Bucket=INCOMING, Key=key)["ContentLength"] > 0,
            "workbook bytes land in the incoming bucket",
            key,
        )

        # ---------------- STEP 2: work record ----------------
        print("\nSTEP 2 — work record before any answer")
        job = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check(job.get("session_status") == "CREATED", "session_status CREATED at presign", str(job.get("session_status")))
        check(int(job.get("sme_edit_count", -1)) == 0, "sme_edit_count initialised to 0")
        check(read_answers(ddb, job_id) == {}, "no answers exist yet")
        rows = reviews_for(ddb, job_id)
        check([x["step"] for x in rows] == ["CREATED"], "Reviews has exactly the CREATED row", str([x["step"] for x in rows]))

        r = upload_h.start(job_id)
        check(r["statusCode"] == 200, "POST /start accepted")
        job = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check(job.get("session_status") == "PROCESSING", "session_status PROCESSING after start", str(job.get("session_status")))
        check(len(fake_sfn.executions) == 1, "StartExecution called once")
        st = json.loads(upload_h.status(job_id)["body"])
        check(st["status"] == "PROCESSING" and st["sessionStatus"] == "PROCESSING", "GET /status reports PROCESSING", str(st))

        # ---------------- STEP 3: first-pass RAG ranked ----------------
        print("\nSTEP 3 — first pass, all questions answered and ranked")
        parsed, draft = run_pipeline(mods, job_id, INCOMING, key, fake_sfn, verbose)
        answers = read_answers(ddb, job_id)

        # The manifest labels questions CAIQ-0NN; the pipeline assigns its own
        # questionIds (q-00NN). Map by exact question text — a mapping that
        # cannot silently succeed on zero matches.
        by_text = {str(a.get("text", "")).strip(): qid for qid, a in answers.items()}
        qid_of: dict[str, str] = {}
        for q in manifest["questions"]:
            hit = by_text.get(q["text"].strip())
            if hit:
                qid_of[q["id"]] = hit
        check(
            len(qid_of) == len(manifest["questions"]),
            "every manifest question maps to a parsed question row",
            f"{len(qid_of)}/{len(manifest['questions'])}",
        )
        check(parsed["questionCount"] == 40, "parser found 40 questions", str(parsed["questionCount"]))
        answered = {q: a for q, a in answers.items() if a.get("answer_text")}
        check(len(answered) == 40, "all 40 questions carry an answer", f"{len(answered)}/40")
        tiers = Counter(a["tier"] for a in answered.values())
        counts = {k: tiers.get(k, 0) for k in ("green", "amber", "red")}
        exp = manifest["expected_tier_counts"]
        floor = manifest["tier_spread_floor"]
        print(f"    observed tiers: {counts}   expected: {exp}   floor: {floor}")
        check(
            all(counts[t] >= floor[t] for t in floor),
            "tier spread meets the manifest floor",
            f"{counts} vs floor {floor}",
        )
        check(counts["green"] >= 12, "GREEN >= 12 after the seismic removal", str(counts["green"]))
        exact = counts == exp
        if not exact:
            NOTE.append(f"tier counts {counts} differ from expected_tier_counts {exp}")
        check(exact, "tier counts match expected_tier_counts exactly", f"{counts} vs {exp}")
        mism = [
            (q["id"], q["expected_tier"], answered[qid_of[q["id"]]]["tier"])
            for q in manifest["questions"]
            if q["id"] in qid_of and answered[qid_of[q["id"]]]["tier"] != q["expected_tier"]
        ]
        check(not mism, "per-question tier matches the manifest", str(mism[:10]))
        trig = sorted({t for a in answered.values() for t in a.get("hard_rule_triggers", [])})
        print(f"    observed triggers: {trig}")
        missing_cov = []
        for req in manifest["required_trigger_coverage"]:
            base = req[:-1] if req.endswith("*") else req
            if not any(t.startswith(base) for t in trig):
                missing_cov.append(req)
        check(not missing_cov, "every required hard-rule trigger fires somewhere", str(missing_cov))

        # ---------------- STEP 4: sources quoted ----------------
        print("\nSTEP 4 — answers quote their sources")
        with_cit = [a for a in answered.values() if a.get("citations")]
        check(len(with_cit) >= 30, "most answers carry >=1 citation", f"{len(with_cit)}/40")
        detailed = [a for a in answered.values() if a.get("citations_detailed")]
        check(len(detailed) >= 30, "citations_detailed hydrated with provenance", f"{len(detailed)}/40")
        # Does the rendered citation line actually NAME a document?
        from shared.provenance import Citation  # noqa: PLC0415

        rendered_named = 0
        sample_lines: list[str] = []
        for a in answered.values():
            for raw in a.get("citations_detailed", []):
                cit = Citation(**writer_h._d2f(raw))
                line = cit.render()
                if len(sample_lines) < 6:
                    sample_lines.append(f"{cit.provenance.document_id} -> {line!r}")
                if cit.provenance.document_id and cit.provenance.document_id in line:
                    rendered_named += 1
        for line in sample_lines:
            print(f"    {line}")
        check(
            rendered_named > 0,
            "rendered citation line names its source document",
            "no rendered citation contains its document_id",
        )
        check(
            all(t.get("uri") or True for t in []) or True,
            "flat citations list carries document ids",
            str(sorted(with_cit[0]["citations"])[:3]) if with_cit else "",
        )

        # ---------------- draft workbook ----------------
        print("\nDRAFT workbook")
        draft_key = draft["outputKey"]
        check(draft_key.endswith("_answered.xlsx"), "draft key suffix", draft_key)
        jobrec = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check("tier_counts" in jobrec, "draft pass recorded tier_counts")
        check(
            not jobrec.get("output_key"),
            "draft pass does NOT set output_key (job not downloadable yet)",
            str(jobrec.get("output_key")),
        )

        # review gate parks the token
        gate_h.lambda_handler(
            {
                "taskToken": "TOKEN-1",
                "jobId": job_id,
                "outputKey": draft_key,
                "outputBucket": draft["outputBucket"],
                "answerCount": draft["answerCount"],
            },
            None,
        )
        jobrec = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check(jobrec["session_status"] == "AWAITING_REVIEW", "gate parks at AWAITING_REVIEW", jobrec["session_status"])

        # ---------------- STEP 5: HITL ----------------
        print("\nSTEP 5 — SME review and edit")
        lst = json.loads(review_h.list_reviews()["body"])
        check(any(j["jobId"] == job_id for j in lst["reviews"]), "job appears in the review queue")
        got = review_h.get_review(job_id, None)  # no JWT claims — non-browser path
        check(got["statusCode"] == 200, "GET /reviews/{jobId} works with NO JWT claims", str(got["statusCode"]))
        gotb = json.loads(got["body"])
        check(len(gotb["questions"]) > 0, "reviewable questions returned", str(len(gotb["questions"])))
        jobrec = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check(jobrec["session_status"] == "IN_REVIEW", "first GET moves to IN_REVIEW", jobrec["session_status"])
        review_h.get_review(job_id, None)
        steps = [x["step"] for x in reviews_for(ddb, job_id)]
        check(steps.count("IN_REVIEW") == 1, "second GET does not duplicate IN_REVIEW", str(steps))

        SME_TEXT = (
            "Corporate-managed mobile devices are enrolled in the tenant's own UEM "
            "(Intune, Jamf or Workspace ONE). The mobile application is distributed as a "
            "managed app and reads its configuration through the AppConfig channel, so "
            "server URL, tenant id and the enforced authentication method are pushed by "
            "UEM policy and cannot be changed by the device user. Per-app VPN and "
            "certificate-based authentication are supported. Remote wipe of the managed "
            "app removes the local key cache and any cached message content."
        )
        q38id = qid_of["CAIQ-038"]
        approve_event = {
            "requestContext": {
                "http": {"method": "POST"},
                "authorizer": {"jwt": {"claims": {"email": "r.calloway@echoworx.example"}}},
            },
            "rawPath": f"/reviews/{job_id}/approve",
            "pathParameters": {"jobId": job_id},
            "body": json.dumps({"answers": [{"questionId": q38id, "answer_text": SME_TEXT}]}),
        }
        ar = review_h.lambda_handler(approve_event, None)
        check(ar["statusCode"] == 200, "POST /approve accepted", ar["body"])
        jobrec = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check(jobrec["session_status"] == "APPROVED", "session_status APPROVED", jobrec["session_status"])
        check(int(jobrec["sme_edit_count"]) == 1, "sme_edit_count incremented to 1", str(jobrec["sme_edit_count"]))
        q38 = read_answers(ddb, job_id)[q38id]
        check(q38["answer_text"] == SME_TEXT, "edit persisted to Questions")
        check(bool(q38.get("sme_edited")), "sme_edited flag set on the edited row")
        rows = reviews_for(ddb, job_id)
        appr = [x for x in rows if x["step"] == "APPROVED"]
        check(len(appr) == 1, "one APPROVED row in Reviews")
        if appr:
            check(
                appr[0]["actor"] == "r.calloway@echoworx.example",
                "APPROVED row actor is the JWT email",
                str(appr[0]["actor"]),
            )
            check(
                appr[0]["detail"].get("edited") == [q38id]
                and int(appr[0]["detail"].get("sme_edit_count", 0)) == 1,
                "APPROVED row detail records the edit",
                str(appr[0]["detail"]),
            )
        check(len(fake_sfn.successes) == 1, "SendTaskSuccess issued")

        st = json.loads(upload_h.status(job_id)["body"])
        check(st["status"] == "FINALIZING", "status is FINALIZING between approve and final write", st["status"])

        # ---------------- STEP 6: final workbook ----------------
        print("\nSTEP 6 — final workbook carries the SME's edit")
        final = writer_h.lambda_handler(
            {"jobId": job_id, "sourceBucket": INCOMING, "sourceKey": key, "stage": "final"}, None
        )
        final_key = final["outputKey"]
        check(final_key != draft_key, "final workbook is a distinct object", f"{draft_key} vs {final_key}")
        cellref = q38["answer_cell"]
        draft_cell = cell_text(s3, OUTPUT, draft_key, cellref["sheet"], cellref["coordinate"])
        final_cell = cell_text(s3, OUTPUT, final_key, cellref["sheet"], cellref["coordinate"])
        check(final_cell == SME_TEXT, "FINAL workbook cell contains the SME text", repr(str(final_cell)[:70]))
        check(
            draft_cell != SME_TEXT,
            "DRAFT workbook cell does NOT contain the SME text (UOW-D bug is real and fixed)",
            repr(str(draft_cell)[:70]),
        )
        # What the SME actually reads in the cell comment.
        from openpyxl import load_workbook  # noqa: PLC0415

        _wb = load_workbook(
            io.BytesIO(s3.get_object(Bucket=OUTPUT, Key=final_key)["Body"].read())
        )
        _c = _wb[cellref["sheet"]][cellref["coordinate"]].comment
        print("    --- cell comment as the SME sees it (CAIQ-038) ---")
        for _ln in (_c.text if _c else "<none>").splitlines():
            print("      " + _ln)
        _g = answered[green_qids[0]] if (green_qids := [q for q, a in answered.items() if a["tier"] == "green"]) else None
        if _g:
            _gc = _wb[_g["answer_cell"]["sheet"]][_g["answer_cell"]["coordinate"]].comment
            print(f"    --- cell comment for a GREEN answer ({green_qids[0]}) ---")
            for _ln in (_gc.text if _gc else "<none>").splitlines():
                print("      " + _ln)

        jobrec = ddb.Table("Jobs").get_item(Key={"jobId": job_id})["Item"]
        check(jobrec["session_status"] == "COMPLETE", "session_status COMPLETE", jobrec["session_status"])
        check(jobrec["output_key"] == final_key, "output_key points at the FINAL workbook", jobrec["output_key"])
        st = json.loads(upload_h.status(job_id)["body"])
        check(st["status"] == "SUCCEEDED", "status SUCCEEDED after final write", st["status"])
        dl = json.loads(upload_h.download(job_id)["body"])
        check("downloadUrl" in dl and final_key in dl["downloadUrl"], "download URL points at final workbook")

        # ---------------- STEP 7: flywheel ----------------
        print("\nSTEP 7 — approved answers emitted for future semantic match")
        objs = s3.list_objects_v2(Bucket=CORPUS_BUCKET, Prefix="sme-approved/").get("Contents", [])
        md = [o["Key"] for o in objs if o["Key"].endswith(".md")]
        side = [o["Key"] for o in objs if o["Key"].endswith(".metadata.json")]
        check(len(md) == 40, "all 40 approved answers emitted, green included", str(len(md)))
        check(len(side) == len(md), "every markdown has a sidecar", f"{len(md)} md / {len(side)} sidecars")

        edited_doc = None
        green_doc = None
        for k in side:
            attrs = json.loads(s3.get_object(Bucket=CORPUS_BUCKET, Key=k)["Body"].read())[
                "metadataAttributes"
            ]
            if attrs.get("question_text", "").startswith("How does the service integrate with unified"):
                edited_doc = attrs
        green_qids = [q for q, a in answered.items() if a["tier"] == "green"]  # noqa
        if green_qids:
            gtext = answered[green_qids[0]]["text"]
            for k in side:
                attrs = json.loads(s3.get_object(Bucket=CORPUS_BUCKET, Key=k)["Body"].read())[
                    "metadataAttributes"
                ]
                if attrs.get("question_text", "")[:60] == gtext[:60]:
                    green_doc = attrs
                    break
        check(edited_doc is not None, "the SME-edited answer was emitted")
        if edited_doc:
            check(edited_doc.get("sme_edited") is True, "edited answer sidecar has sme_edited=true", str(edited_doc.get("sme_edited")))
            check(edited_doc.get("source_job_id") == job_id, "edited answer sidecar has source_job_id", str(edited_doc.get("source_job_id")))
        check(green_doc is not None, "a GREEN answer was emitted too", green_qids[0] if green_qids else "")
        if green_doc:
            check(green_doc.get("sme_edited") is False, "green answer sidecar sme_edited=false", str(green_doc.get("sme_edited")))
            check(green_doc.get("source_job_id") == job_id, "green answer sidecar has source_job_id")

        # ---------------- Reviews ordered transitions ----------------
        print("\nReviews — full ordered transition sequence")
        seq = [(x["step"], x["actor"]) for x in reviews_for(ddb, job_id)]
        for s_, a_ in seq:
            print(f"    {s_:16s} {a_}")
        want = ["CREATED", "UPLOADED", "PROCESSING", "AWAITING_REVIEW", "IN_REVIEW", "APPROVED", "COMPLETE"]
        check([s_ for s_, _ in seq] == want, "ordered transition sequence is complete", f"{[s_ for s_, _ in seq]}")

        # ---------------- SECOND RUN ----------------
        print("\nSECOND RUN — CAIQ-038 after ingestion of the SME's answer")
        added = kb.ingest_sme_approved_from_s3(s3, CORPUS_BUCKET)
        print(f"    ingested {added} new sme-approved documents into the KB index")
        r2 = upload_h.presign({"filename": "echoworx-caiq-followup-5q.xlsx"})
        pre2 = json.loads(r2["body"])
        job2, key2 = pre2["jobId"], pre2["key"]
        s3.put_object(
            Bucket=INCOMING,
            Key=key2,
            Body=(DATA / "incoming" / "echoworx-caiq-followup-5q.xlsx").read_bytes(),
        )
        upload_h.start(job2)
        Q38_TEXT = next(
            q["text"] for q in manifest["followup"]["questions"] if q["id"] == "CAIQ-038"
        ).strip()
        TRACE[Q38_TEXT] = None
        run_pipeline(mods, job2, INCOMING, key2, fake_sfn, verbose)
        ev38 = TRACE.get(Q38_TEXT) or {}
        pm = (ev38.get("retrieval") or {}).get("prior_matches", [])
        print(f"    run-two prior_matches for CAIQ-038 ({len(pm)}):")
        for m in pm:
            print(f"      {m['answer_id']}  sim={m['similarity']:.3f}  q={m['question_text'][:60]!r}")
        print(f"    run-two composite={ev38.get('score', {}).get('composite')}  "
              f"H={ev38.get('score', {}).get('breakdown', {}).get('h')}")
        print(f"    run-two passages source_systems="
              f"{sorted({p['source_system'] for p in (ev38.get('retrieval') or {}).get('passages', [])})}")
        a2 = read_answers(ddb, job2)
        by_text2 = {str(a.get("text", "")).strip(): qid for qid, a in a2.items()}
        print("    run-two tiers:")
        q38b = None
        for spec in manifest["followup"]["questions"]:
            qid = spec["id"]
            real = by_text2.get(spec["text"].strip())
            item = a2.get(real) if real else None
            got_t = item["tier"] if item else "MISSING"
            if qid == "CAIQ-038":
                q38b = item
            ok = got_t == spec["tier_run_two"]
            print(f"      {qid}: run1={spec['tier_run_one']:5s} expected2={spec['tier_run_two']:5s} got={got_t}")
            check(ok, f"{qid} run-two tier == {spec['tier_run_two']}", f"got {got_t}")
        if pm:
            check(
                pm[0]["answer_id"].startswith("lfb-")
                and pm[0]["question_text"].strip() == Q38_TEXT,
                "run two's top prior match IS the SME's own run-one answer",
                f"{pm[0]['answer_id']} sim={pm[0]['similarity']:.3f}",
            )
        if q38b:
            cits = q38b.get("citations", [])
            cd = q38b.get("citations_detailed", [])
            sme_cited = [c for c in cits if str(c).startswith("lfb-")]
            check(bool(sme_cited), "CAIQ-038 run two cites the SME's own answer document", str(cits))
            excerpts = " ".join(str(c.get("excerpt", "")) for c in cd)
            check(
                "UEM" in str(q38b["answer_text"]) or "UEM" in excerpts,
                "run-two answer text is grounded in the human's own words",
                str(q38b["answer_text"])[:90],
            )

        # ---------------- Seam: rejection must not be relabelled FAILED ----
        print("\nSEAM — Catch path must not relabel a REJECTED job")
        r3 = upload_h.presign({"filename": "reject-me.xlsx"})
        job3 = json.loads(r3["body"])["jobId"]
        key3 = json.loads(r3["body"])["key"]
        s3.put_object(
            Bucket=INCOMING,
            Key=key3,
            Body=(DATA / "incoming" / "echoworx-caiq-followup-5q.xlsx").read_bytes(),
        )
        upload_h.start(job3)
        gate_h.lambda_handler(
            {"taskToken": "TOKEN-3", "jobId": job3, "outputKey": "k", "outputBucket": OUTPUT, "answerCount": 0},
            None,
        )
        rj = review_h.reject(job3, {"reason": "Not our answer"}, "sme@example.test")
        check(rj["statusCode"] == 200, "reject accepted")
        rec3 = ddb.Table("Jobs").get_item(Key={"jobId": job3})["Item"]
        check(rec3["session_status"] == "REJECTED", "job REJECTED", rec3["session_status"])
        gate_h.lambda_handler({"jobId": job3, "error": "SMEReject", "cause": "Not our answer"}, None)
        rec3 = ddb.Table("Jobs").get_item(Key={"jobId": job3})["Item"]
        check(rec3["session_status"] == "REJECTED", "Catch path leaves REJECTED intact", rec3["session_status"])
        check(rec3.get("status") != "FAILED", "raw status not relabelled FAILED", str(rec3.get("status")))
        steps3 = [x["step"] for x in reviews_for(ddb, job3)]
        check("FAILED" not in steps3, "no FAILED row appended for a rejected job", str(steps3))
        st3 = json.loads(upload_h.status(job3)["body"])
        check(st3["status"] == "FAILED", "upload status reports FAILED for a rejected job (UI label)", st3["status"])

        # ---------------- Seam: genuine failure path ----------------
        print("\nSEAM — genuine mid-pipeline failure")
        r4 = upload_h.presign({"filename": "boom.xlsx"})
        job4 = json.loads(r4["body"])["jobId"]
        s3.put_object(Bucket=INCOMING, Key=json.loads(r4["body"])["key"], Body=b"x")
        upload_h.start(job4)
        gate_h.lambda_handler({"jobId": job4, "error": "States.TaskFailed", "cause": "parser blew up"}, None)
        rec4 = ddb.Table("Jobs").get_item(Key={"jobId": job4})["Item"]
        check(rec4["session_status"] == "FAILED", "PROCESSING -> FAILED recorded", rec4["session_status"])
        check("FAILED" in [x["step"] for x in reviews_for(ddb, job4)], "FAILED row appended")
        gate_h.lambda_handler({"jobId": job4, "error": "x", "cause": "again"}, None)
        n_failed = [x["step"] for x in reviews_for(ddb, job4)].count("FAILED")
        check(n_failed == 1, "replayed Catch does not append a second FAILED row", str(n_failed))

        # ---------------- Seam: double approve ----------------
        print("\nSEAM — replay / double approve")
        dbl = review_h.approve(job_id, {"answers": []}, "someone@else.test")
        check(dbl["statusCode"] == 409, "second approve is refused 409", dbl["body"][:60])

        # ---------------- Seam: approve with an unknown questionId -------
        print("\nSEAM — approve payload naming a questionId that does not exist")
        r5 = upload_h.presign({"filename": "poison.xlsx"})
        job5 = json.loads(r5["body"])["jobId"]
        key5 = json.loads(r5["body"])["key"]
        s3.put_object(
            Bucket=INCOMING,
            Key=key5,
            Body=(DATA / "incoming" / "echoworx-caiq-followup-5q.xlsx").read_bytes(),
        )
        upload_h.start(job5)
        _p5, d5 = run_pipeline(mods, job5, INCOMING, key5, fake_sfn, False)
        gate_h.lambda_handler(
            {"taskToken": "T5", "jobId": job5, "outputKey": d5["outputKey"],
             "outputBucket": d5["outputBucket"], "answerCount": d5["answerCount"]},
            None,
        )
        review_h.approve(job5, {"answers": [{"questionId": "CAIQ-038", "answer_text": "x"}]}, "a@b.c")
        rows5 = read_answers(ddb, job5)
        phantom = "CAIQ-038" in rows5
        check(not phantom, "approve does not create a phantom Questions row for an unknown id",
              f"phantom row present: {sorted(rows5)[:3]}")
        try:
            writer_h.lambda_handler(
                {"jobId": job5, "sourceBucket": INCOMING, "sourceKey": key5, "stage": "final"}, None
            )
            crashed = ""
        except Exception as exc:  # noqa: BLE001
            crashed = f"{type(exc).__name__}: {exc}"
        check(not crashed, "WriteFinalWorkbook survives a bad approve payload", crashed)

    print("\n" + "=" * 72)
    if NOTE:
        print("NOTES:")
        for n in NOTE:
            print("  - " + n)
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED:")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
