#!/usr/bin/env python3
"""PreToolUse gate guard for the PoC Factory.

Blocks `cdk deploy` (and equivalents) unless the G4 Deploy Approval row in the
Notion Gates database carries Verdict = Approved for this engagement.

Fails CLOSED. If the token is missing, Notion is unreachable, or the row cannot
be found, the command is blocked. A deploy gate that fails open is not a gate.

RECONSTRUCTED 2026-08-27 from docs/factory/gates.md and docs/factory/notion-schema.md;
the original .claude/hooks/ was lost in a skill sync. Diff before trusting.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

GATES_DATA_SOURCE_ID = "a81313be-ea9c-4adc-906f-52edf72461ff"
NOTION_VERSION = "2022-06-28"
GUARDED = re.compile(r"\bcdk\s+(deploy|destroy)\b|\bcloudformation\s+(deploy|create-stack|update-stack)\b")
GATE = "G4 Deploy Approval"


def load_dotenv(path: str = ".env") -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except OSError:
        pass


def block(reason: str) -> None:
    print(f"BLOCKED by gate_guard: {reason}", file=sys.stderr)
    sys.exit(2)


def query_gate(token: str, engagement_id: str) -> list[dict]:
    body = json.dumps(
        {
            "filter": {
                "and": [
                    {"property": "Engagement ID", "rich_text": {"equals": engagement_id}},
                    {"property": "Gate", "select": {"equals": GATE}},
                ]
            }
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/data_sources/{GATES_DATA_SOURCE_ID}/query",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp).get("results", [])


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if event.get("tool_name") != "Bash":
        sys.exit(0)
    command = (event.get("tool_input") or {}).get("command", "")
    if not GUARDED.search(command):
        sys.exit(0)

    load_dotenv()
    engagement_id = os.environ.get("POC_ENGAGEMENT_ID", "").strip()
    token = os.environ.get("NOTION_TOKEN", "").strip()

    if not engagement_id:
        block("POC_ENGAGEMENT_ID is not set in .env — cannot identify the engagement.")
    if not token:
        block(
            f"NOTION_TOKEN is not set, so {GATE} for {engagement_id} cannot be verified. "
            "Deploy runs from the pipeline after a human approves the gate."
        )

    try:
        rows = query_gate(token, engagement_id)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        block(f"could not reach Notion to verify {GATE} for {engagement_id}: {exc}")

    if not rows:
        block(f"no {GATE} row found for {engagement_id}. The gate has not been requested.")

    verdict = ""
    try:
        verdict = rows[0]["properties"]["Verdict"]["select"]["name"]
    except (KeyError, TypeError):
        block(f"{GATE} row for {engagement_id} has no readable Verdict.")

    if verdict != "Approved":
        block(
            f"{GATE} for {engagement_id} is '{verdict}', not 'Approved'. "
            "Only a human sets the verdict. Attach evidence and request review."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
