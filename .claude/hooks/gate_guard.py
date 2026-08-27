#!/usr/bin/env python3
"""PreToolUse hook: refuse dangerous commands unless the matching Notion Gate is Approved.

Claude Code passes the tool call as JSON on stdin. Exit 2 blocks the call and returns the
message to the agent. Exit 0 allows it. This is defence in depth on top of settings.json deny
rules and the pipeline-only deploy path; it is not the only control.
"""
import json, os, sys, urllib.request

GUARDED = {
    "cdk deploy": "G4 Deploy Approval",
    "git push origin main": "G3 Code Review",
    "gh pr merge": "G3 Code Review",
    "terraform apply": "G4 Deploy Approval",
}

def gate_approved(gate_name: str) -> bool:
    token = os.environ.get("NOTION_TOKEN")
    ds = os.environ.get("NOTION_GATES_DS")
    eng = os.environ.get("POC_ENGAGEMENT_ID")
    if not (token and ds and eng):
        return False  # fail closed
    body = json.dumps({
        "filter": {"and": [
            {"property": "Gate", "select": {"equals": gate_name}},
            {"property": "Engagement ID", "rich_text": {"equals": eng}},
            {"property": "Verdict", "select": {"equals": "Approved"}},
        ]},
        "page_size": 1,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/data_sources/{ds}/query", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return len(json.load(r).get("results", [])) > 0
    except Exception:
        return False  # fail closed

def main():
    payload = json.load(sys.stdin)
    cmd = (payload.get("tool_input") or {}).get("command", "")
    for needle, gate in GUARDED.items():
        if needle in cmd:
            if gate_approved(gate):
                return 0
            print(f"BLOCKED: '{needle}' requires Notion gate '{gate}' = Approved for "
                  f"{os.environ.get('POC_ENGAGEMENT_ID','<unset>')}. Ask the human reviewer.",
                  file=sys.stderr)
            return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
