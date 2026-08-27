#!/usr/bin/env python3
"""Load customer reference data into the CustomerRefs DynamoDB table.

Reads from data/graph/customers.json (produced by generate_synthetic_data.py).
DynamoDB schema: PK customerId (STRING), attributes: name, public_reference (stored
as string "true"/"false" for DynamoDB condition compatibility), approval_expires, industry.

Usage:
  python scripts/seed_customer_refs.py [--table TABLE_NAME]

If --table is omitted, resolves from CloudFormation output 'CustomerRefsTableName'.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import boto3

CUSTOMERS_FILE = Path(__file__).resolve().parents[1] / "data" / "graph" / "customers.json"


def _resolve_table() -> str:
    result = subprocess.run(
        [
            "aws", "cloudformation", "describe-stacks",
            # STAGE must match the `stage` CDK context the stacks were deployed with.
            "--stack-name", f"rfp-copilot-{os.environ.get('STAGE', 'poc')}-data",
            "--query", "Stacks[0].Outputs[?OutputKey=='CustomerRefsTableName'].OutputValue",
            "--output", "text",
        ],
        capture_output=True, text=True,
    )
    name = result.stdout.strip()
    if not name or name == "None":
        print("ERROR: Could not resolve table from CloudFormation. Pass --table explicitly.", file=sys.stderr)
        sys.exit(1)
    return name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", help="DynamoDB table name (resolved from CloudFormation if omitted)")
    args = parser.parse_args()

    table_name = args.table or _resolve_table()
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)

    if not CUSTOMERS_FILE.exists():
        print(f"ERROR: {CUSTOMERS_FILE} not found. Run generate_synthetic_data.py first.", file=sys.stderr)
        sys.exit(1)

    customers = json.loads(CUSTOMERS_FILE.read_text())
    print(f"Seeding {len(customers)} customers into {table_name}")

    with table.batch_writer() as batch:
        for c in customers:
            item = {
                "customerId": c["customer_id"],
                "name": c["name"],
                "industry": c["industry"],
                # Stored as BOOLEAN — retriever's FilterExpression uses eq(True).
                # Prior versions of this seeder wrote strings; the retriever was
                # fixed in Phase F to match the boolean storage.
                "public_reference": bool(c["public_reference"]),
                "approval_expires": c["approval_expires"] or "",
            }
            batch.put_item(Item=item)
            ref_status = "public" if c["public_reference"] else "NDA"
            expires = c["approval_expires"] or "n/a"
            print(f"  {c['customer_id']}  {c['name']:<35}  {ref_status:<7}  expires={expires}")

    print(f"\nDone. {len(customers)} customers written.")

    # The asymmetry below is what makes hard rule 3 observable. The retriever
    # scans this table for public_reference == true AND approval_expires >= today
    # and hands the result to hard_rules.apply_rules() as the approved list; any
    # customer named in an answer but absent from that list demotes the answer to
    # amber and appends unapproved_reference:<name>.
    approved = [c["name"] for c in customers if c["public_reference"]]
    print(f"\nApproved public references ({len(approved)}): {', '.join(approved)}")
    print("Not approved for public naming:")
    for c in customers:
        if c["public_reference"]:
            continue
        why = (
            f"approval lapsed {c['approval_expires']}"
            if c["approval_expires"]
            else "never approved"
        )
        print(f"  {c['customer_id']}  {c['name']:<40}  {why}")


if __name__ == "__main__":
    main()
