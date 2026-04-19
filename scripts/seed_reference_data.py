"""Thin orchestrator — runs the two remaining seed steps end-to-end.

After Phase F (SME-approved Q&A moved from DynamoDB LibraryFeedback into the
Bedrock KB under the `sme-approved/` prefix), the seed steps are:

  1. CustomerRefs DDB table          →  scripts/seed_customer_refs.py
  2. Reference corpus → KB ingestion →  scripts/seed_s3_corpus.py
     (uploads compliance/, product-docs/, prior-rfps/, sme-approved/ from
      data/corpus-real/ and triggers a Bedrock KB ingestion job)

Usage:
  python3.13 scripts/seed_reference_data.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PYTHON = sys.executable


def run(script: str) -> None:
    print(f"\n=== Running {script} ===")
    subprocess.run([PYTHON, str(SCRIPTS / script)], check=True)


def main() -> None:
    run("seed_customer_refs.py")
    run("seed_s3_corpus.py")
    print("\nDone.")


if __name__ == "__main__":
    main()
