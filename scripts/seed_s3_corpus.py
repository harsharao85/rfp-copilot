#!/usr/bin/env python3
"""Seed the reference corpus bucket and trigger Bedrock KB ingestion.

Reads from data/corpus-real/ (produced by generate_corpus_documents.py):
  compliance/    →  *.pdf + *.pdf.metadata.json    (source_type=compliance_cert)
  product-docs/  →  *.md  + *.md.metadata.json     (source_type=product_doc)
  prior-rfps/    →  *.md  + *.md.metadata.json     (source_type=prior_rfp)

After upload, resolves KnowledgeBaseId + DataSourceId from the deployed
knowledge-base stack outputs and starts a Bedrock KB ingestion job, polling
to completion.

Usage:
  python scripts/seed_s3_corpus.py [--bucket BUCKET] [--stack-prefix PREFIX]
                                   [--skip-ingest]

Flags default to the dev stage. --skip-ingest uploads without triggering
ingestion (useful when debugging sidecar shape).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "corpus-real"
PREFIXES = ["compliance", "product-docs", "prior-rfps", "sme-approved"]

CONTENT_TYPES: dict[str, str] = {
    ".pdf":           "application/pdf",
    ".md":            "text/markdown",
    ".metadata.json": "application/json",
}

POLL_INTERVAL_S = 10
POLL_CAP_S      = 600  # 10 min


def _cfn_output(stack_name: str, output_key: str) -> str:
    result = subprocess.run(
        [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", stack_name,
            "--query", f"Stacks[0].Outputs[?OutputKey=='{output_key}'].OutputValue",
            "--output", "text",
        ],
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    if not value or value == "None":
        print(f"ERROR: {output_key} missing from stack {stack_name}", file=sys.stderr)
        sys.exit(1)
    return value


def _content_type(path: Path) -> str:
    # .pdf.metadata.json matches ".metadata.json" before ".pdf" if we order right.
    for suffix in (".metadata.json", ".pdf", ".md"):
        if path.name.endswith(suffix):
            return CONTENT_TYPES[suffix]
    return "application/octet-stream"


def _upload_corpus(bucket: str) -> int:
    s3 = boto3.client("s3")
    uploaded = 0
    print(f"Uploading reference corpus into s3://{bucket}/")
    for prefix in PREFIXES:
        src_dir = DATA_ROOT / prefix
        if not src_dir.exists():
            print(f"  SKIP {prefix}/ — run scripts/generate_corpus_documents.py first")
            continue
        for path in sorted(src_dir.iterdir()):
            if not path.is_file():
                continue
            key = f"{prefix}/{path.name}"
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=path.read_bytes(),
                ContentType=_content_type(path),
            )
            marker = ""
            if path.name.endswith(".metadata.json"):
                attrs = json.loads(path.read_text()).get("metadataAttributes", {})
                if attrs.get("updated_at"):
                    marker = f"  updated_at={attrs['updated_at']}"
                elif attrs.get("approved_at"):
                    marker = f"  approved_at={attrs['approved_at']}"
            print(f"  uploaded {key}{marker}")
            uploaded += 1
    return uploaded


def _ingest(kb_id: str, ds_id: str) -> None:
    client = boto3.client("bedrock-agent")
    print(f"\nStarting Bedrock KB ingestion (kb={kb_id}, ds={ds_id})")
    resp = client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    job_id = resp["ingestionJob"]["ingestionJobId"]
    print(f"  ingestionJobId={job_id}")

    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > POLL_CAP_S:
            print(f"\nERROR: ingestion still running after {POLL_CAP_S}s — check console", file=sys.stderr)
            sys.exit(2)

        job = client.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id,
        )["ingestionJob"]
        status = job["status"]
        stats = job.get("statistics", {}) or {}
        scanned = stats.get("numberOfDocumentsScanned", 0)
        indexed = stats.get("numberOfNewDocumentsIndexed", 0)
        failed  = stats.get("numberOfDocumentsFailed", 0)
        print(f"  [{int(elapsed):3d}s] status={status}  scanned={scanned}  indexed={indexed}  failed={failed}")

        if status == "COMPLETE":
            print("\nIngestion complete.")
            return
        if status == "FAILED":
            reasons = job.get("failureReasons") or []
            print(f"\nERROR: ingestion FAILED — {reasons}", file=sys.stderr)
            sys.exit(2)
        time.sleep(POLL_INTERVAL_S)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", help="Reference corpus bucket (resolved from CFN if omitted)")
    parser.add_argument("--stack-prefix", default="rfp-copilot-dev",
                        help="CFN stack name prefix (default: rfp-copilot-dev)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Upload only; do not trigger KB ingestion")
    args = parser.parse_args()

    storage_stack = f"{args.stack_prefix}-storage"
    kb_stack      = f"{args.stack_prefix}-knowledge-base"

    bucket = args.bucket or _cfn_output(storage_stack, "ReferenceCorpusBucketName")
    uploaded = _upload_corpus(bucket)
    print(f"\n{uploaded} files uploaded.")

    if args.skip_ingest:
        print("\n--skip-ingest set; not triggering KB ingestion.")
        return

    if uploaded == 0:
        print("\nNothing uploaded; skipping ingestion.")
        return

    kb_id = _cfn_output(kb_stack, "KnowledgeBaseId")
    ds_id = _cfn_output(kb_stack, "DataSourceId")
    _ingest(kb_id, ds_id)

    print("\nStale-prior summary (freshness-suppression demo):")
    print("  acme_financial_soc2_answer (approved 2025-07-01) is stale vs. soc2_cert_2025 (updated 2025-10-01).")
    print("  bluebird_encryption_answer (approved 2025-08-15) is stale vs. encryption_whitepaper (updated 2025-09-05).")
    print("  globex_fedramp_answer      (approved 2026-02-01) is stale vs. fedramp_status_2026   (updated 2026-03-01).")
    print("  northwind_sso_answer       (approved 2026-01-15) is FRESH (post-dates sso_mfa_guide 2025-10-20).")


if __name__ == "__main__":
    main()
