"""
gmail_email.py  —  Gmail actions via pre-fetched JSON file
----------------------------------------------------------
briefing.py reads gmail_actions.json which is written by running
the Gmail analysis in a Claude.ai chat session (where Gmail MCP
is available).

To update Gmail actions:
  1. Open a Claude.ai chat
  2. Say: "Analyse my Gmail from the last 24 hours for work actions
     and save the results to gmail_actions.json in my morning briefing
     project folder"
  3. Claude reads Gmail via MCP, writes the JSON, briefing picks it up

Or use the launcher button: "Fetch Gmail Actions" which opens a
pre-filled Claude.ai chat prompt.

The gmail_actions.json format:
{
  "generated_at": "2026-04-27T08:00:00",
  "actions": [
    {
      "action": "Reply to John re contract",
      "context": "John needs sign-off by Friday on the Santos heads of agreement",
      "priority": "high",
      "deadline": "Friday",
      "from_email": "john@example.com"
    }
  ]
}
"""

import os
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ACTIONS_FILE = Path(__file__).parent / "gmail_actions.json"
MAX_AGE_HOURS = 26  # treat file as stale after this many hours


def get_gmail_analysis(client=None) -> dict:
    """
    Read gmail_actions.json if it exists and is recent.
    Returns {"actions": [...]} or {"actions": [], "error": str}
    """
    if not ACTIONS_FILE.exists():
        return {
            "actions": [],
            "error": "No gmail_actions.json found — fetch Gmail actions via Claude.ai chat"
        }

    try:
        data = json.loads(ACTIONS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"actions": [], "error": f"Could not read gmail_actions.json: {e}"}

    # Check freshness
    generated_at = data.get("generated_at", "")
    if generated_at:
        try:
            gen_dt = datetime.datetime.fromisoformat(generated_at)
            age_hours = (datetime.datetime.now() - gen_dt).total_seconds() / 3600
            if age_hours > MAX_AGE_HOURS:
                return {
                    "actions": [],
                    "error": f"gmail_actions.json is {age_hours:.0f}h old — please refresh"
                }
        except Exception:
            pass

    actions = data.get("actions", [])
    return {"actions": actions}


def save_gmail_actions(actions: list, generated_at: str = None) -> bool:
    """Write gmail_actions.json — called from outside (Claude.ai session or test)."""
    try:
        ACTIONS_FILE.write_text(json.dumps({
            "generated_at": generated_at or datetime.datetime.now().isoformat(),
            "actions": actions,
        }, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"Could not save gmail_actions.json: {e}")
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        result = get_gmail_analysis()
        if result.get("error"):
            print(f"⚠️  {result['error']}")
        else:
            print(f"✓ {len(result['actions'])} Gmail actions loaded")
            for a in result["actions"]:
                print(f"  [{a.get('priority','normal'):6}] {a.get('action','')[:60]}")
    else:
        print("Usage: py gmail_email.py status")
        print("To fetch: ask Claude in claude.ai to analyse your Gmail and save gmail_actions.json")
