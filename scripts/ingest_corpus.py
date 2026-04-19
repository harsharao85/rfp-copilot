#!/usr/bin/env python3
"""
Upload the synthetic reference corpus to S3 and trigger the Kendra data source sync.

Layout produced in S3:
  historical/      -- Excel RFPs (Kendra MS_EXCEL)
  whitepapers/     -- Markdown docs (Kendra MD)
  seismic/         -- Per-card .txt files converted from content_cards.json (Kendra PLAIN_TEXT)
  gong/            -- Per-call .txt files converted from transcripts.json (Kendra PLAIN_TEXT)
  metadata/        -- Kendra .metadata.json sidecars (one per document)

Hard rule: seismic-card-005 ("Pricing — Internal Only") is excluded by design.
"""

import json
import os
import sys
import boto3

BUCKET       = "rfp-copilot-dev-storage-referencecorpusbucketeced4-bw4yb6fzyp3c"
INDEX_ID     = "edecb417-85e4-41e4-a31f-57d77fe9910a"
DATASOURCE_ID = "c90c5334-f240-4e74-baa5-60b1b36a62bf"
DATA_ROOT    = os.path.join(os.path.dirname(__file__), "..", "data")

s3      = boto3.client("s3", region_name="us-east-1")
kendra  = boto3.client("kendra", region_name="us-east-1")


def put(key: str, body, content_type: str = "application/octet-stream") -> None:
    if isinstance(body, str):
        body = body.encode()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=content_type)
    print(f"  uploaded {key}")


def sidecar(doc_key: str, title: str, kendra_content_type: str, attrs: dict) -> None:
    meta = {"DocumentId": doc_key, "Attributes": attrs, "Title": title,
            "ContentType": kendra_content_type}
    put(f"metadata/{doc_key}.metadata.json", json.dumps(meta, indent=2), "application/json")


# ── Historical RFPs ────────────────────────────────────────────────────────────

rfps = [
    {
        "file": "won_rfp_bluebird_insurance_2026.xlsx",
        "title": "Won RFP — Bluebird Insurance 2026",
        "win_loss": "win",
        "industry": "insurance",
        "sme_approved": "true",
        "approved_at": "2026-02-01T00:00:00Z",
        "expires_on": "2028-02-01T00:00:00Z",
        "deal_size": 480000,
    },
    {
        "file": "won_rfp_acme_financial_2025.xlsx",
        "title": "Won RFP — Acme Financial 2025",
        "win_loss": "win",
        "industry": "financial_services",
        "sme_approved": "true",
        "approved_at": "2025-09-01T00:00:00Z",
        "expires_on": "2027-09-01T00:00:00Z",
        "deal_size": 320000,
    },
    {
        "file": "lost_rfp_globex_2025.xlsx",
        "title": "Lost RFP — Globex 2025",
        "win_loss": "loss",
        "industry": "manufacturing",
        "sme_approved": "false",
        "approved_at": "2025-11-01T00:00:00Z",
        "expires_on": "2027-11-01T00:00:00Z",
        "deal_size": 210000,
    },
]

print("Uploading historical RFPs...")
for rfp in rfps:
    key = f"historical/{rfp['file']}"
    with open(os.path.join(DATA_ROOT, "historical", rfp["file"]), "rb") as f:
        put(key, f.read())
    sidecar(key, rfp["title"], "MS_EXCEL", {
        "_source_uri": f"s3://{BUCKET}/{key}",
        "source_system": "historical_rfp",
        "win_loss": rfp["win_loss"],
        "industry": rfp["industry"],
        "sme_approved": rfp["sme_approved"],
        "approved_at": rfp["approved_at"],
        "expires_on": rfp["expires_on"],
        "deal_size": rfp["deal_size"],
    })

# ── Whitepapers ────────────────────────────────────────────────────────────────

whitepapers = [
    ("security_overview.md",          "Security Overview",           "security"),
    ("data_protection_policy.md",     "Data Protection Policy",      "security"),
    ("incident_response_overview.md", "Incident Response Overview",  "security"),
    ("soc2_bridge_letter.md",         "SOC 2 Bridge Letter",         "compliance"),
]

print("Uploading whitepapers...")
for fname, title, industry in whitepapers:
    key = f"whitepapers/{fname}"
    with open(os.path.join(DATA_ROOT, "whitepapers", fname), "rb") as f:
        put(key, f.read(), "text/markdown")
    sidecar(key, title, "MD", {
        "_source_uri": f"s3://{BUCKET}/{key}",
        "source_system": "whitepaper",
        "win_loss": "n/a",
        "industry": industry,
        "sme_approved": "true",
        "approved_at": "2026-01-01T00:00:00Z",
        "expires_on": "2027-01-01T00:00:00Z",
    })

# ── Seismic content cards ──────────────────────────────────────────────────────
# card-005 is "Pricing — Internal Only (DO NOT SHARE)" — excluded by hard rule.

print("Uploading Seismic content cards (excluding pricing card)...")
with open(os.path.join(DATA_ROOT, "seismic", "content_cards.json")) as f:
    cards = json.load(f)

for card in cards:
    if card["card_id"] == "seismic-card-005":
        print(f"  SKIPPED {card['card_id']} (pricing — hard rule)")
        continue
    text = f"{card['title']}\n\n{card['body']}\n\nOwner: {card['owner']}\nUpdated: {card['updated_at']}"
    key = f"seismic/{card['card_id']}.txt"
    put(key, text, "text/plain")
    sidecar(key, card["title"], "PLAIN_TEXT", {
        "_source_uri": f"s3://{BUCKET}/{key}",
        "source_system": "seismic",
        "win_loss": "n/a",
        "industry": "general",
        "sme_approved": "true",
        "approved_at": f"{card['updated_at']}T00:00:00Z",
        "expires_on": "2027-12-31T00:00:00Z",
    })

# ── Gong call snippets ─────────────────────────────────────────────────────────

print("Uploading Gong call snippets...")
with open(os.path.join(DATA_ROOT, "gong", "transcripts.json")) as f:
    calls = json.load(f)

for call in calls:
    snippets_text = "\n\n".join(f"- {s}" for s in call["snippets"])
    participants = ", ".join(call["participants"])
    text = (f"{call['title']}\n\nDate: {call['date']}\nParticipants: {participants}"
            f"\n\nKey snippets:\n{snippets_text}")
    key = f"gong/{call['call_id']}.txt"
    put(key, text, "text/plain")
    sidecar(key, call["title"], "PLAIN_TEXT", {
        "_source_uri": f"s3://{BUCKET}/{key}",
        "source_system": "gong",
        "win_loss": "n/a",
        "industry": "general",
        "sme_approved": "true",
        "approved_at": f"{call['date']}T00:00:00Z",
        "expires_on": "2027-12-31T00:00:00Z",
    })

# ── Trigger Kendra sync ────────────────────────────────────────────────────────

print("\nStarting Kendra data source sync...")
resp = kendra.start_data_source_sync_job(Id=DATASOURCE_ID, IndexId=INDEX_ID)
execution_id = resp["ExecutionId"]
print(f"  sync job started: {execution_id}")
print(f"\nDone. Monitor sync progress:")
print(f"  aws kendra describe-data-source-sync-job \\")
print(f"    --id {DATASOURCE_ID} --index-id {INDEX_ID} \\")
print(f"    --query 'HistoryItems[0].{{Status:Status,ErrorMessage:ErrorMessage,DocumentsAdded:Metrics.DocumentsAdded}}'")
