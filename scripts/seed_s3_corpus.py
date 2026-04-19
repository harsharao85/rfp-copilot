#!/usr/bin/env python3
"""Upload reference corpus JSON sidecars to S3.

Reads from data/corpus/ (produced by generate_synthetic_data.py) and uploads
each sidecar to the correct S3 prefix:
  compliance/    →  compliance_store source
  product-docs/  →  product_docs source
  prior-rfps/    →  prior_rfps source

Usage:
  python scripts/seed_s3_corpus.py [--bucket BUCKET_NAME]

If --bucket is omitted, the script resolves the bucket name from the CDK
CloudFormation output 'ReferenceCorpusBucketName'. Requires aws CLI and
the stack to be deployed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import boto3

DATA_CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"
PREFIXES = ["compliance", "product-docs", "prior-rfps"]


def _resolve_bucket() -> str:
    """Pull bucket name from CloudFormation outputs."""
    result = subprocess.run(
        [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", "rfp-copilot-dev-storage",
            "--query", "Stacks[0].Outputs[?OutputKey=='ReferenceCorpusBucketName'].OutputValue",
            "--output", "text",
        ],
        capture_output=True, text=True,
    )
    name = result.stdout.strip()
    if not name or name == "None":
        print("ERROR: Could not resolve bucket from CloudFormation. Pass --bucket explicitly.", file=sys.stderr)
        sys.exit(1)
    return name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", help="S3 bucket name (resolved from CloudFormation if omitted)")
    args = parser.parse_args()

    bucket = args.bucket or _resolve_bucket()
    s3 = boto3.client("s3")
    uploaded = 0

    print(f"Seeding reference corpus into s3://{bucket}/")
    for prefix in PREFIXES:
        src_dir = DATA_CORPUS / prefix
        if not src_dir.exists():
            print(f"  SKIP {prefix}/ — run generate_synthetic_data.py first")
            continue
        for json_file in sorted(src_dir.glob("*.json")):
            key = f"{prefix}/{json_file.name}"
            body = json_file.read_bytes()
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
            doc = json.loads(body)
            marker = ""
            if "approved_at" in doc:
                marker = f"  approved_at={doc['approved_at']}"
                # Flag stale priors for visibility
                corroborated_by = doc.get("corroborated_by", [])
                if corroborated_by:
                    marker += f"  corroborated_by={corroborated_by}"
            elif "updated_at" in doc:
                marker = f"  updated_at={doc['updated_at']}"
            print(f"  uploaded {key}{marker}")
            uploaded += 1

    print(f"\nDone. {uploaded} sidecars uploaded.")
    print("\nStale-prior summary (for staleness demo):")
    print("  Priors with 'STALE' in title were approved BEFORE their corroborating source updated.")
    print("  Run POST /admin/staleness/trigger after seeding LibraryFeedback to see the daemon fire.")


if __name__ == "__main__":
    main()
