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
import subprocess
import sys
from pathlib import Path

import boto3

CUSTOMERS_FILE = Path(__file__).resolve().parents[1] / "data" / "graph" / "customers.json"


def _resolve_table() -> str:
    result = subprocess.run(
        [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", "rfp-copilot-dev-data",
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
                # DynamoDB FilterExpression uses eq("true") string comparison (see retriever)
                "public_reference": "true" if c["public_reference"] else "false",
                "approval_expires": c["approval_expires"] or "",
            }
            batch.put_item(Item=item)
            ref_status = "public" if c["public_reference"] else "NDA"
            expires = c["approval_expires"] or "n/a"
            print(f"  {c['customer_id']}  {c['name']:<35}  {ref_status:<7}  expires={expires}")

    print(f"\nDone. {len(customers)} customers written.")
    print("\nNote: cust-004 (Kestrel Logistics) has an expired approval — the retriever will exclude it.")
    print("      cust-003 (Aurora Federal) is NDA — will not appear as a public reference.")


if __name__ == "__main__":
    main()
