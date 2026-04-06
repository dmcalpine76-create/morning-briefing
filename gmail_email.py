"""
gmail_email.py  —  Gmail personal inbox analysis via Google OAuth2
------------------------------------------------------------------
Authenticates with Gmail using Google's OAuth2 device flow,
then fetches and analyses the last 24 hours of email.

SETUP (one time only):
  1. Go to https://console.cloud.google.com
  2. Create a new project: "Morning Briefing"
  3. APIs & Services -> Enable APIs -> search "Gmail API" -> Enable
  4. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
       Application type: Desktop app
       Name: Morning Briefing
  5. Click Download JSON — open it and copy:
       client_id     -> GMAIL_CLIENT_ID in .env
       client_secret -> GMAIL_CLIENT_SECRET in .env
  6. APIs & Services -> OAuth consent screen:
       User type: External
       App name: Morning Briefing
       Add your Gmail address as a test user
  7. Run: py gmail_email.py setup

Usage:
  py gmail_email.py setup   — one-time browser auth
  py gmail_email.py test    — test connection
  import gmail_email        — used by briefing.py
"""

import os
import json
import base64
import datetime
import re
import requests
import anthropic
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
CACHE_FILE    = Path(__file__).parent / ".gmail_token_cache.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

TOKEN_URL  = "https://oauth2.googleapis.com/token"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
API_BASE   = "https://gmail.googleapis.com/gmail/v1"

MAX_EMAILS       = 60
MAX_DIGEST_ITEMS = 10
MAX_ACTIONS      = 8
MAX_PEOPLE       = 8
REQUEST_TIMEOUT  = 15


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

def _load_token() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_token(token: dict):
    CACHE_FILE.write_text(json.dumps(token, indent=2), encoding="utf-8")
    try:
        CACHE_FILE.chmod(0o600)
    except Exception:
        pass


def _refresh_access_token(refresh_token: str) -> str:
    """Exchange refresh token for a new access token."""
    resp = requests.post(TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    return data["access_token"]


def get_access_token() -> str:
    """Return a valid access token, refreshing silently if needed."""
    token = _load_token()
    if not token:
        raise RuntimeError(
            "Gmail not authorised.\n"
            "Run:  py gmail_email.py setup"
        )
    # Try to refresh using the saved refresh token
    try:
        access = _refresh_access_token(token["refresh_token"])
        return access
    except Exception as e:
        raise RuntimeError(f"Gmail token refresh failed: {e}\nRun: py gmail_email.py setup")


def setup_auth():
    """Device code flow — run once to authorise Gmail access."""
    if not CLIENT_ID or not CLIENT_SECRET:
        print("\n❌  GMAIL_CLIENT_ID or GMAIL_CLIENT_SECRET not set in .env")
        print("    See setup instructions at the top of gmail_email.py\n")
        return

    print("\n" + "─" * 60)
    print("  GMAIL AUTHORISATION")
    print("─" * 60)

    # Request device code
    resp = requests.post(DEVICE_URL, data={
        "client_id": CLIENT_ID,
        "scope":     " ".join(SCOPES),
    }, timeout=15)

    if resp.status_code != 200:
        print(f"\n❌  Failed to start auth: {resp.text}\n")
        return

    flow = resp.json()
    print(f"\n  1. Open this URL in any browser:")
    print(f"     {flow['verification_url']}")
    print(f"\n  2. Enter this code when prompted:")
    print(f"     {flow['user_code']}")
    print(f"\n  Waiting for you to complete login…\n")

    # Poll for token
    interval   = flow.get("interval", 5)
    expires_in = flow.get("expires_in", 1800)
    device_code = flow["device_code"]
    import time
    waited = 0
    while waited < expires_in:
        time.sleep(interval)
        waited += interval
        poll = requests.post(TOKEN_URL, data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "device_code":   device_code,
            "grant_type":    "urn:ietf:params:oauth:grant-type:device_code",
        }, timeout=15)
        data = poll.json()
        if "access_token" in data:
            _save_token(data)
            print(f"✅  Gmail authorised successfully!")
            # Confirm identity
            try:
                me = requests.get(
                    f"{API_BASE}/users/me/profile",
                    headers={"Authorization": f"Bearer {data['access_token']}"},
                    timeout=10,
                ).json()
                print(f"    Account: {me.get('emailAddress', '')}\n")
            except Exception:
                pass
            return
        elif data.get("error") == "authorization_pending":
            continue
        elif data.get("error") == "slow_down":
            interval += 5
        else:
            print(f"\n❌  Auth error: {data}\n")
            return

    print("\n❌  Timed out waiting for authorisation.\n")


# ─────────────────────────────────────────────
# EMAIL FETCHING
# ─────────────────────────────────────────────

def _gmail_get(token: str, path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _decode_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    def extract(part):
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        for p in part.get("parts", []):
            result = extract(p)
            if result:
                return result
        return ""
    return extract(payload)[:600].strip()


def _parse_gmail_msg(msg: dict, is_sent: bool) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "(no subject)").strip()
    from_h  = headers.get("from", "")
    to_h    = headers.get("to", "")
    date_h  = headers.get("date", "")
    # Parse name and email from "Name <email>" format
    m = re.match(r"^(.*?)\s*<(.+?)>$", from_h)
    if m:
        from_name, from_email = m.group(1).strip().strip('"'), m.group(2)
    else:
        from_name, from_email = from_h, from_h
    body    = _decode_body(msg.get("payload", {}))
    snippet = msg.get("snippet", "")
    preview = body if body else snippet[:500]
    labels  = msg.get("labelIds", [])
    return {
        "subject":    subject,
        "from_name":  from_name,
        "from_email": from_email,
        "to":         to_h,
        "received":   date_h,
        "preview":    preview,
        "importance": "high" if "IMPORTANT" in labels else "normal",
        "is_read":    "UNREAD" not in labels,
        "has_attach": any("has:attachment" in (h.get("value","")) for h in msg.get("payload",{}).get("headers",[])),
        "folder":     "Sent" if is_sent else "Inbox",
        "is_sent":    is_sent,
    }


def fetch_recent_emails(token: str, hours_back: int = 24) -> list:
    """Fetch recent emails from Gmail inbox and sent items."""
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=hours_back)
    after = int(since.timestamp())

    all_emails = []
    seen_ids   = set()

    def fetch_folder(query: str, is_sent: bool, max_results: int = 40):
        print(f"   → {('Sent' if is_sent else 'Inbox')}…", end=" ", flush=True)
        try:
            data = _gmail_get(token, "/users/me/messages", {
                "q":          query,
                "maxResults": max_results,
            })
            msgs   = data.get("messages", [])
            added  = 0
            for m in msgs:
                mid = m["id"]
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                try:
                    full = _gmail_get(token, f"/users/me/messages/{mid}", {"format": "full"})
                    all_emails.append(_parse_gmail_msg(full, is_sent))
                    added += 1
                except Exception:
                    continue
            print(f"{added} emails")
        except Exception as e:
            print(f"skipped ({type(e).__name__})")

    fetch_folder(f"in:inbox after:{after}", is_sent=False, max_results=40)
    fetch_folder(f"in:sent after:{after}",  is_sent=True,  max_results=20)

    all_emails.sort(key=lambda e: e["received"], reverse=True)
    return all_emails[:MAX_EMAILS]


# ─────────────────────────────────────────────
# AI ANALYSIS
# ─────────────────────────────────────────────

def _fmt(email: dict, index: int) -> str:
    flags = []
    if email["is_sent"]:       flags.append("SENT BY ME")
    elif not email["is_read"]: flags.append("UNREAD")
    if email["importance"] == "high": flags.append("IMPORTANT")
    if email["has_attach"]:    flags.append("has attachment")
    flag_str  = f" [{', '.join(flags)}]" if flags else ""
    timestamp = email["received"][:25] if email["received"] else ""
    to_str    = f"To: {email['to']}\n" if email["is_sent"] and email["to"] else ""
    return (
        f"[{index+1}]{flag_str}\n"
        f"From: {email['from_name']} <{email['from_email']}>\n"
        f"{to_str}"
        f"Subject: {email['subject']}\n"
        f"Time: {timestamp}\n"
        f"Preview: {email['preview']}\n"
    )


def analyse_emails(client: anthropic.Anthropic, emails: list) -> dict:
    if not emails:
        return {"digest": [], "actions": [], "people": []}

    emails_text = "\n\n".join(_fmt(e, i) for i, e in enumerate(emails))
    today = datetime.date.today().strftime("%A %d %B %Y")

    prompt = f"""You are a sharp executive assistant. Today is {today}.
You have {len(emails)} personal Gmail emails from the last 24 hours (received + sent).
Return a JSON object with exactly three keys: "digest", "actions", "people".

"digest": up to {MAX_DIGEST_ITEMS} most important emails.
Each: subject, from_name, from_email, received, is_read (bool), is_sent (bool),
priority ("action-required"|"important"|"informational"),
summary (2 sentences max), action (short phrase or "").

"actions": up to {MAX_ACTIONS} concrete things to do today based on these emails.
Each: action (specific task), context (1 sentence), deadline ("" if none),
priority ("urgent"|"high"|"normal"), from_email (subject reference).

"people": up to {MAX_PEOPLE} people to contact or meetings to schedule.
Each: name, email, action ("contact"|"schedule-meeting"|"follow-up"),
reason (1-2 sentences), suggested_timing, context (which email).

Deprioritise: newsletters, promotional emails, automated notifications,
social media alerts, subscription confirmations, marketing.

Return ONLY the JSON object — no markdown, no extra text.

EMAILS:
{emails_text}
"""

    try:
        message = client.messages.create(
            model      = "claude-opus-4-6",
            max_tokens = 4000,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        return {
            "digest":  result.get("digest", []),
            "actions": result.get("actions", []),
            "people":  result.get("people", []),
        }
    except Exception as e:
        print(f"  ⚠️  Gmail analysis failed: {e}")
        return {"digest": [], "actions": [], "people": []}


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

def get_gmail_analysis(client: anthropic.Anthropic) -> dict:
    """Fetch and analyse Gmail. Returns digest/actions/people or empty dict."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return {}
    try:
        token  = get_access_token()
        print(f"   Scanning Gmail…")
        emails = fetch_recent_emails(token)
        sent   = sum(1 for e in emails if e["is_sent"])
        recvd  = len(emails) - sent
        print(f"   📨  {recvd} received + {sent} sent = {len(emails)} total")
        if not emails:
            return {}
        print(f"   🤖  Running AI analysis…")
        result = analyse_emails(client, emails)
        print(f"   ✓   {len(result['digest'])} priority · "
              f"{len(result['actions'])} actions · "
              f"{len(result['people'])} people/meetings")
        return result
    except RuntimeError as e:
        print(f"  ⚠️  Gmail: {e}")
        return {}
    except Exception as e:
        print(f"  ⚠️  Gmail error: {e}")
        return {}


# ─────────────────────────────────────────────
# STANDALONE
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_auth()
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        print("\n🔍  Testing Gmail connection…")
        try:
            token = get_access_token()
            me    = _gmail_get(token, "/users/me/profile")
            print(f"✅  Connected as: {me.get('emailAddress')}")
            print(f"    Total messages: {me.get('messagesTotal', '?')}\n")
            emails = fetch_recent_emails(token)
            sent   = sum(1 for e in emails if e["is_sent"])
            print(f"📨  {len(emails)} emails in last 24h ({len(emails)-sent} received, {sent} sent)\n")
            for i, e in enumerate(emails[:6]):
                tag = "→ SENT" if e["is_sent"] else ("★ UNREAD" if not e["is_read"] else "  read")
                print(f"  {i+1}. [{tag}]")
                print(f"     {e['from_name']}: {e['subject']}")
                print(f"     {e['preview'][:80]}…\n")
        except RuntimeError as e:
            print(f"❌  {e}\n")
    else:
        print(__doc__)
