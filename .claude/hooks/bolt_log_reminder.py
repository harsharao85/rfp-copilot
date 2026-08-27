#!/usr/bin/env python3
"""Stop hook: nudge the agent to record its bolt outcome in Notion before ending the turn."""
import json, sys
payload = json.load(sys.stdin)
if payload.get("stop_hook_active"):
    sys.exit(0)  # avoid loops
print(json.dumps({"decision": "block",
  "reason": "Before finishing: confirm you updated your owned Notion records (status, links, "
            "Owning Agent, Bolt) and reported to the Conductor. If already done, say so and stop."}))
