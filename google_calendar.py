"""
google_calendar.py  —  Google Calendar integration via OAuth2
-------------------------------------------------------------
Fetches yesterday/today/tomorrow events from personal Google Calendar,
returning the same data structure as outlook_calendar.py.

SETUP (one time only — shares the same Google OAuth app as gmail_email.py):
  1. In Google Cloud Console, your existing "Morning Briefing" OAuth app
     needs the Google Calendar API enabled:
     APIs & Services → Enable APIs → search "Google Calendar API" → Enable
  2. The OAuth scope needs updating — re-run authorisation:
     py google_calendar.py setup
     (This replaces the existing Gmail token with one that covers both
      Gmail and Calendar. After this you won't need to re-auth Gmail separately.)
  3. Test it:
     py google_calendar.py test

Usage:
  py google_calendar.py setup   — one-time browser authorisation
  py google_calendar.py test    — print today's events to console
  import google_calendar        — used by briefing.py
"""

import os
import json
import datetime
import html as _html
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────

CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID",     "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")

# Shared token cache — if gmail_email.py is also used, they share the same file
# but google_calendar.py requests a superset of scopes so it re-auths once
CACHE_FILE   = Path(__file__).parent / ".google_token_cache.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

TOKEN_URL    = "https://oauth2.googleapis.com/token"
CAL_API_BASE = "https://www.googleapis.com/calendar/v3"
AEST_OFFSET  = datetime.timezone(datetime.timedelta(hours=10))
REQUEST_TIMEOUT = 15


# ── AUTH ──────────────────────────────────────────────────────────────────────

def _load_token() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_token(token: dict):
    CACHE_FILE.write_text(json.dumps(token, indent=2), encoding="utf-8")
    try:
        CACHE_FILE.chmod(0o600)
    except Exception:
        pass


def _refresh(refresh_token: str) -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    return data["access_token"]


def get_access_token() -> str:
    token = _load_token()
    if not token:
        raise RuntimeError(
            "Google Calendar not authorised.\nRun:  py google_calendar.py setup"
        )
    try:
        return _refresh(token["refresh_token"])
    except Exception as e:
        raise RuntimeError(
            f"Google token refresh failed: {e}\nRun:  py google_calendar.py setup"
        )


def setup_auth():
    """
    OOB (out-of-band) auth flow.
    Builds a Google consent URL, you open it in any browser,
    approve access, copy the code shown, paste it back here.
    Works with sensitive Gmail/Calendar scopes on any machine.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        print("\n❌  GMAIL_CLIENT_ID or GMAIL_CLIENT_SECRET not set in .env")
        print("    Follow the setup instructions at the top of this file\n")
        return

    import urllib.parse, webbrowser

    redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    scope_str    = " ".join(SCOPES)

    auth_url = (
        "https://accounts.google.com/o/oauth2/auth"
        f"?client_id={urllib.parse.quote(CLIENT_ID)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={urllib.parse.quote(scope_str)}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    print("\n" + "─" * 60)
    print("  GOOGLE AUTHORISATION (Gmail + Calendar)")
    print("─" * 60)
    print("\n  Step 1: Open this URL in your browser (opening now…):")
    print(f"\n  {auth_url}\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("  Step 2: Sign in with dmcalpine76@gmail.com and click Allow.")
    print("  Step 3: Google will show you a code. Copy it and paste it below.\n")

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
        return

    if "refresh_token" not in data:
        print("\n⚠️  No refresh token returned.")
        print("   Go to https://myaccount.google.com/permissions and revoke")
        print("   access for 'Morning Briefing', then run setup again.\n")
        return

    _save_token(data)
    print(f"\n✅  Authorised successfully!")
    print(f"    Token saved to {CACHE_FILE}")
    print(f"\n    Test with:  py google_calendar.py test\n")


# ── CALENDAR FETCHING ─────────────────────────────────────────────────────────

def _gcal_get(token: str, path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{CAL_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_gcal_event(ev: dict) -> dict:
    """Parse a Google Calendar event into the same shape as outlook_calendar events."""
    summary  = ev.get("summary", "(No title)")
    status   = ev.get("status", "confirmed")
    if status == "cancelled":
        return None

    start_raw = ev.get("start", {})
    end_raw   = ev.get("end", {})

    # All-day events use "date", timed events use "dateTime"
    is_all_day = "date" in start_raw and "dateTime" not in start_raw

    if is_all_day:
        try:
            start_dt = datetime.datetime.fromisoformat(
                start_raw["date"]).replace(tzinfo=AEST_OFFSET)
            end_dt   = datetime.datetime.fromisoformat(
                end_raw.get("date", start_raw["date"])).replace(tzinfo=AEST_OFFSET)
        except Exception:
            start_dt = datetime.datetime.now(AEST_OFFSET)
            end_dt   = start_dt
        start_str = "All day"
        end_str   = ""
        duration_mins = 0
    else:
        try:
            start_dt = datetime.datetime.fromisoformat(
                start_raw["dateTime"].replace("Z", "+00:00")
            ).astimezone(AEST_OFFSET)
            end_dt = datetime.datetime.fromisoformat(
                end_raw["dateTime"].replace("Z", "+00:00")
            ).astimezone(AEST_OFFSET)
            start_str = start_dt.strftime("%I:%M %p").lstrip("0")
            end_str   = end_dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            start_dt  = datetime.datetime.now(AEST_OFFSET)
            end_dt    = start_dt
            start_str = ""
            end_str   = ""
        duration_mins = int((end_dt - start_dt).total_seconds() / 60)

    # Location
    location = ev.get("location", "").strip()

    # Organiser
    organiser_obj = ev.get("organizer", {})
    organizer = organiser_obj.get("displayName") or organiser_obj.get("email", "")

    # Attendees
    attendees     = ev.get("attendees", [])
    attendee_count = len(attendees)

    # Conference / online meeting
    conf     = ev.get("conferenceData", {})
    is_online = bool(conf)
    online_url = ""
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            online_url = ep.get("uri", "")
            break

    # My response status
    my_response = "none"
    for att in attendees:
        if att.get("self"):
            my_response = att.get("responseStatus", "none")
            break
    # Map Google → Outlook-compatible response status names
    resp_map = {
        "accepted":    "accepted",
        "declined":    "declined",
        "tentative":   "tentativelyAccepted",
        "needsAction": "notResponded",
        "none":        "none",
    }
    response_status = resp_map.get(my_response, "none")

    # Body preview from description
    body_preview = ev.get("description", "")[:200].strip()

    return {
        "subject":         summary,
        "start_time":      start_str,
        "end_time":        end_str,
        "start_dt":        start_dt,
        "end_dt":          end_dt,
        "duration_mins":   duration_mins,
        "location":        location,
        "organizer":       organizer,
        "attendee_count":  attendee_count,
        "is_all_day":      is_all_day,
        "is_online":       is_online,
        "online_url":      online_url,
        "body_preview":    body_preview,
        "response_status": response_status,
    }


def fetch_calendar_events(days_ahead: int = 2, calendar_id: str = "primary") -> dict:
    """
    Fetch Google Calendar events for yesterday + today + tomorrow.
    Returns the same shape as outlook_calendar.fetch_calendar_events():
      {"yesterday": [...], "today": [...], "tomorrow": [...], "error": None}
    """
    try:
        token = get_access_token()
    except Exception as e:
        return {"yesterday": [], "today": [], "tomorrow": [], "error": str(e)}

    now_aest    = datetime.datetime.now(AEST_OFFSET)
    today_start = now_aest.replace(hour=0, minute=0, second=0, microsecond=0)
    yest_start  = today_start - datetime.timedelta(days=1)
    window_end  = today_start + datetime.timedelta(days=days_ahead)

    # Google Calendar API uses RFC3339 format
    time_min = yest_start.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    time_max = window_end.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    try:
        data = _gcal_get(token, f"/calendars/{calendar_id}/events", {
            "timeMin":      time_min,
            "timeMax":      time_max,
            "singleEvents": "true",
            "orderBy":      "startTime",
            "maxResults":   100,
        })
    except Exception as e:
        return {"yesterday": [], "today": [], "tomorrow": [], "error": f"Calendar API error: {e}"}

    yesterday_events = []
    today_events     = []
    tomorrow_events  = []

    yesterday_date = (today_start - datetime.timedelta(days=1)).date()
    today_date     = today_start.date()
    tomorrow_date  = (today_start + datetime.timedelta(days=1)).date()

    for item in data.get("items", []):
        ev = _parse_gcal_event(item)
        if ev is None:
            continue
        ev_date = ev["start_dt"].date()
        if ev_date == yesterday_date:
            yesterday_events.append(ev)
        elif ev_date == today_date:
            today_events.append(ev)
        elif ev_date == tomorrow_date:
            tomorrow_events.append(ev)

    return {
        "yesterday": yesterday_events,
        "today":     today_events,
        "tomorrow":  tomorrow_events,
        "error":     None,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    if mode == "setup":
        setup_auth()

    elif mode == "test":
        print("Fetching Google Calendar events…")
        data = fetch_calendar_events()
        if data["error"]:
            print(f"ERROR: {data['error']}")
            sys.exit(1)
        now = datetime.datetime.now(AEST_OFFSET)
        print(f"\n=== YESTERDAY ({len(data['yesterday'])} events) ===")
        for ev in data["yesterday"]:
            print(f"  {ev['start_time']:12} {ev['subject'][:55]}")
        print(f"\n=== TODAY ({len(data['today'])} events) ===")
        for ev in data["today"]:
            past = " [past]" if ev["end_dt"] < now else ""
            print(f"  {ev['start_time']:12} {ev['subject'][:55]}{past}")
        print(f"\n=== TOMORROW ({len(data['tomorrow'])} events) ===")
        for ev in data["tomorrow"]:
            print(f"  {ev['start_time']:12} {ev['subject'][:55]}")
        print("\nDone.")
    else:
        print("Usage: py google_calendar.py [setup|test]")
