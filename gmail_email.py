"""
gmail_email.py  —  Gmail analysis via Anthropic API + Gmail MCP server
-----------------------------------------------------------------------
Uses the Gmail MCP server (gmailmcp.googleapis.com) via the Anthropic
API's mcp_servers parameter. This works from GitHub Actions because it
uses a stored refresh token to get a fresh access token non-interactively.

SETUP (one time):
  1. You need a Google Cloud OAuth client (Web application type)
     with redirect URI: https://claude.ai/api/mcp/auth_callback
     (This is likely already set up from the earlier Google auth attempts)
  2. Set in .env:
       GMAIL_CLIENT_ID=...
       GMAIL_CLIENT_SECRET=...
       GMAIL_REFRESH_TOKEN=...  (see setup below to get this)
  3. Set the same three values as GitHub secrets

TO GET YOUR REFRESH TOKEN (one time):
  Run: py gmail_email.py setup
  This opens a browser, you log in, and it saves the refresh token to .env

Usage:
  py gmail_email.py setup    — one-time token setup
  py gmail_email.py test     — test Gmail MCP connection
  import gmail_email         — used by briefing.py
"""

import os
import json
import datetime
import requests
import anthropic
from pathlib import Path
from dotenv import load_dotenv, set_key

ACTIONS_FILE  = Path(__file__).parent / "gmail_actions.json"
MAX_AGE_HOURS = 48  # increased — file is now refreshed automatically each morning

load_dotenv()

CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_MCP_URL = "https://gmailmcp.googleapis.com/mcp/v1"
TOKEN_URL     = "https://oauth2.googleapis.com/token"
MAX_ACTIONS   = 8
ENV_FILE      = Path(__file__).parent / ".env"


def read_cached_actions() -> dict:
    """Read gmail_actions.json fallback — no expiry error, just note the age."""
    if not ACTIONS_FILE.exists():
        return {"actions": [], "error": "No gmail_actions.json — run py gmail_email.py test or wait for next briefing run"}
    try:
        data = json.loads(ACTIONS_FILE.read_text(encoding="utf-8"))
        gen = data.get("generated_at", "")
        age_note = ""
        if gen:
            try:
                age_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(gen)).total_seconds() / 3600
                if age_h > MAX_AGE_HOURS:
                    age_note = f" ({age_h:.0f}h old)"
            except Exception:
                pass
        actions = data.get("actions", [])
        return {"actions": actions, "note": age_note}
    except Exception as e:
        return {"actions": [], "error": f"Could not read gmail_actions.json: {e}"}


def get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError(
            "GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN "
            "must all be set in .env\nRun: py gmail_email.py setup"
        )
    resp = requests.post(TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    }, timeout=15)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data['error']} — {data.get('error_description','')}\nRun: py gmail_email.py setup")
    return data["access_token"]


def setup_auth():
    """
    One-time OAuth flow using manual copy-paste (OOB-style).
    Google has blocked localhost redirects for sensitive scopes,
    so we use urn:ietf:wg:oauth:2.0:oob to show the code in the browser.
    User copies the code and pastes it into the terminal.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        print("\n❌  GMAIL_CLIENT_ID or GMAIL_CLIENT_SECRET not set in .env")
        print("    Add your Google OAuth Desktop app credentials to .env\n")
        return

    import urllib.parse, webbrowser

    # NOTE: Use "Desktop app" credential type in Google Cloud Console.
    # The OOB redirect URI works with Desktop app credentials.
    redirect_uri  = "urn:ietf:wg:oauth:2.0:oob"
    scope         = "https://www.googleapis.com/auth/gmail.readonly"

    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={urllib.parse.quote(CLIENT_ID)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        "&response_type=code"
        f"&scope={urllib.parse.quote(scope)}"
        "&access_type=offline"
        "&prompt=consent"
    )

    print("\n" + "─" * 60)
    print("  GMAIL AUTHORISATION")
    print("─" * 60)
    print("\n  Step 1: Opening your browser…")
    print("  (If it doesn't open automatically, copy and paste this URL:)")
    print(f"\n  {auth_url}\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("  Step 2: Sign in with dmcalpine76@gmail.com and click Allow.")
    print("  Step 3: Google will show you a code on screen. Copy it.\n")

    code = input("  Paste the code here: ").strip()
    if not code:
        print("\n❌  No code entered.\n")
        return

    # Exchange code for tokens
    resp = requests.post(TOKEN_URL, data={
        "code":          code,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }, timeout=15)
    data = resp.json()

    if "error" in data:
        print(f"\n❌  Token exchange failed: {data}\n")
        print("  Make sure you are using a Desktop app credential (not Web application).")
        print("  In Google Cloud Console: Credentials → Create → OAuth client ID → Desktop app\n")
        return

    if "refresh_token" not in data:
        print("\n⚠️  No refresh token returned.")
        print("  Go to https://myaccount.google.com/permissions")
        print("  Revoke access for your app, then run setup again.\n")
        return

    # Save refresh token to .env
    refresh_token = data["refresh_token"]
    if ENV_FILE.exists():
        set_key(str(ENV_FILE), "GMAIL_REFRESH_TOKEN", refresh_token)
        print(f"\n✅  Refresh token saved to .env")
    else:
        with open(ENV_FILE, "a", encoding="utf-8") as f:
            f.write(f'\nGMAIL_REFRESH_TOKEN={refresh_token}\n')
        print(f"\n✅  Refresh token saved to .env")

    print(f"\n  Now add GMAIL_REFRESH_TOKEN to your GitHub secrets:")
    print(f"  Value: {refresh_token[:20]}…")
    print(f"\n  Then run: py gmail_email.py test\n")


def get_gmail_analysis(client: anthropic.Anthropic = None) -> dict:
    """
    Analyse Gmail via the Gmail MCP server through the Anthropic API.
    Returns {"actions": [...]} or {"actions": [], "error": str}
    """
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        return {
            "actions": [],
            "error": "Gmail not configured — set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN in .env"
        }

    try:
        access_token = get_access_token()
    except RuntimeError as e:
        return {"actions": [], "error": str(e)}

    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"actions": [], "error": "ANTHROPIC_API_KEY not set"}
        client = anthropic.Anthropic(api_key=api_key)

    today = datetime.date.today().strftime("%A %d %B %Y")

    prompt = f"""Today is {today}.

Search my Gmail inbox for emails from the last 24 hours using:
  newer_than:1d in:inbox -category:promotions -category:social -category:updates

For each thread with multiple messages, read all messages to understand the full context.

Then return a JSON object with ONE key: "actions"

"actions": up to {MAX_ACTIONS} concrete tasks or responses needed, in priority order.
Each must have:
  - "action":    clear task title (max 12 words)
  - "context":   one sentence — what triggered this and why it matters
  - "priority":  "urgent" | "high" | "normal"
  - "deadline":  specific deadline if one exists, else ""
  - "from_email": sender email address

Ignore newsletters, automated notifications, marketing, and receipts.
Focus only on emails that genuinely require action from Doug McAlpine (State Gas, Brisbane).

Return ONLY valid JSON — no markdown, no preamble."""

    try:
        message = client.beta.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            mcp_servers=[{
                "type":                "url",
                "url":                 GMAIL_MCP_URL,
                "name":                "gmail",
                "authorization_token": access_token,
            }],
            betas=["mcp-client-2025-11-20"],
            timeout=90,
        )

        raw = ""
        for block in message.content:
            if hasattr(block, "text"):
                raw += block.text

        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if not raw:
            return {"actions": []}

        return json.loads(raw)

    except Exception as e:
        err = str(e)
        if "401" in err or "authentication" in err.lower():
            return {"actions": [], "error": "Gmail MCP authentication failed — run py gmail_email.py setup"}
        return {"actions": [], "error": f"Gmail analysis error: {err}"}


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    if mode == "setup":
        setup_auth()
    elif mode == "test":
        print("Testing Gmail MCP connection…")
        result = get_gmail_analysis()
        if result.get("error"):
            print(f"⚠️  {result['error']}")
        else:
            actions = result.get("actions", [])
            print(f"✓ {len(actions)} action(s) found:")
            for a in actions:
                print(f"  [{a.get('priority','normal'):6}] {a.get('action','')[:65]}")
    else:
        print("Usage: py gmail_email.py [setup|test]")
