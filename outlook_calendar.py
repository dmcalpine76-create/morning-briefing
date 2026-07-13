"""
outlook_calendar.py  —  Microsoft Graph API calendar integration
-----------------------------------------------------------------
Fetches Outlook calendar events for today and tomorrow, returning
structured data for the morning briefing Calendar tab.

Shares the same MSAL token cache as outlook_email.py — no extra
auth setup needed if Outlook email is already working.  Just add
Calendars.Read to your Azure app's API permissions and re-run:
  py outlook_email.py setup

Usage:
  py outlook_calendar.py test   — print today's events to console
  import outlook_calendar       — used by briefing.py
"""

import os
import json
import datetime
import html as _html
import requests
import msal
from pathlib import Path
from dotenv import load_dotenv

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

load_dotenv()

# ── Config (mirrors outlook_email.py) ──────────────────────────────────────
CLIENT_ID   = os.environ.get("OUTLOOK_CLIENT_ID", "")
TENANT_ID   = os.environ.get("OUTLOOK_TENANT_ID", "")
AUTHORITY   = f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else \
              "https://login.microsoftonline.com/consumers"
SCOPES      = ["Calendars.Read", "User.Read"]
CACHE_FILE  = Path(__file__).parent / ".outlook_token_cache.bin"
GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
DAYFMT      = "%#d" if os.name == "nt" else "%-d"   # no-pad day: Windows vs Linux
AEST_OFFSET = datetime.timezone(datetime.timedelta(hours=10))
REQUEST_TIMEOUT = 12


# ── Auth helpers (same pattern as outlook_email.py) ─────────────────────────

def _load_cache():
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text())
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize())
        try:
            CACHE_FILE.chmod(0o600)
        except Exception:
            pass

def _build_app(cache):
    if not CLIENT_ID:
        raise RuntimeError("OUTLOOK_CLIENT_ID not set in .env")
    return msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )

def _get_token() -> str:
    """Return a valid access token, refreshing silently if possible."""
    cache = _load_cache()
    app = _build_app(cache)
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(
            "No valid Outlook token. Run:  py outlook_email.py setup\n"
            "Then ensure Calendars.Read is added to your Azure app permissions."
        )
    _save_cache(cache)
    return result["access_token"]


# ── Calendar fetching ────────────────────────────────────────────────────────

def _graph_get(token: str, path: str, params: dict = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        headers=headers,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_calendar_events(days_ahead: int = 2) -> dict:
    """
    Fetch calendar events for today + tomorrow (or `days_ahead` days).

    Returns:
        {
          "today":    [event_dict, ...],
          "tomorrow": [event_dict, ...],
          "error":    None | str
        }

    Each event_dict has:
        subject, start_time, end_time, start_dt, end_dt,
        location, organizer, attendee_count, is_all_day,
        is_online, online_url, body_preview, response_status
    """
    try:
        token = _get_token()
    except Exception as e:
        return {"yesterday": [], "today": [], "tomorrow": [], "error": str(e)}

    now_aest    = datetime.datetime.now(AEST_OFFSET)
    today_start = now_aest.replace(hour=0, minute=0, second=0, microsecond=0)
    yest_start  = today_start - datetime.timedelta(days=1)   # include yesterday
    window_end  = today_start + datetime.timedelta(days=days_ahead)

    # Graph calendarView requires UTC ISO strings
    start_utc = yest_start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = window_end.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # Force Graph to return all times in UTC so our conversion is unambiguous
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        }
        resp = requests.get(
            f"{GRAPH_BASE}/me/calendarView",
            headers=headers,
            params={
                "startDateTime": start_utc,
                "endDateTime":   end_utc,
                "$select": (
                    "subject,start,end,location,organizer,attendees,"
                    "isAllDay,isOnlineMeeting,onlineMeetingUrl,"
                    "bodyPreview,responseStatus,isCancelled"
                ),
                "$orderby": "start/dateTime",
                "$top": 50,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"yesterday": [], "today": [], "tomorrow": [], "error": f"Graph API error: {e}"}

    yesterday_events = []
    today_events     = []
    tomorrow_events  = []
    yesterday_start  = today_start - datetime.timedelta(days=1)
    tomorrow_start   = today_start + datetime.timedelta(days=1)

    for ev in data.get("value", []):
        if ev.get("isCancelled"):
            continue

        # Detect all-day by key presence — isAllDay flag is unreliable in some tenants
        has_date_only = "date" in ev.get("start", {}) and "dateTime" not in ev.get("start", {})
        is_all_day = ev.get("isAllDay", False) or has_date_only

        # Parse start/end — Graph returns either dateTime (with tz) or date (all-day)
        if is_all_day:
            # All-day events use "date" key (YYYY-MM-DD), no time component
            raw_date = ev["start"].get("date") or ev["start"].get("dateTime", "")[:10]
            raw_date_end = ev["end"].get("date") or ev["end"].get("dateTime", "")[:10]
            try:
                start_dt = datetime.datetime.fromisoformat(raw_date).replace(tzinfo=AEST_OFFSET)
                end_dt   = datetime.datetime.fromisoformat(raw_date_end).replace(tzinfo=AEST_OFFSET)
            except Exception:
                start_dt = today_start
                end_dt   = today_start
            start_str = "All day"
            end_str   = ""
        else:
            raw_start = ev["start"].get("dateTime", "")
            raw_end   = ev["end"].get("dateTime", "")
            # Graph returns UTC; convert to AEST
            try:
                start_dt = datetime.datetime.fromisoformat(
                    raw_start.replace("Z", "+00:00")
                ).astimezone(AEST_OFFSET)
                end_dt = datetime.datetime.fromisoformat(
                    raw_end.replace("Z", "+00:00")
                ).astimezone(AEST_OFFSET)
                # Windows-compatible strftime (no %-I)
                start_str = start_dt.strftime("%I:%M %p").lstrip("0")
                end_str   = end_dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                start_dt  = today_start
                end_dt    = today_start
                start_str = ""
                end_str   = ""

        # Duration in minutes
        duration_mins = int((end_dt - start_dt).total_seconds() / 60) if not is_all_day else 0

        # Location
        loc_obj  = ev.get("location", {})
        location = loc_obj.get("displayName", "").strip() if isinstance(loc_obj, dict) else ""

        # Organizer
        org = ev.get("organizer", {}).get("emailAddress", {})
        organizer = org.get("name", org.get("address", ""))

        # Attendees
        attendees = ev.get("attendees", [])
        attendee_count = len(attendees)

        # Online meeting
        is_online   = ev.get("isOnlineMeeting", False)
        online_url  = ev.get("onlineMeetingUrl", "")
        if not online_url and is_online:
            # Teams meetings embed the URL in location
            if location.startswith("http"):
                online_url = location
                location   = "Microsoft Teams"

        # My response
        resp_status = ev.get("responseStatus", {}).get("response", "none")

        event = {
            "subject":        ev.get("subject", "(No title)"),
            "start_time":     start_str,
            "end_time":       end_str,
            "start_dt":       start_dt,
            "end_dt":         end_dt,
            "duration_mins":  duration_mins,
            "location":       location,
            "organizer":      organizer,
            "attendee_count": attendee_count,
            "is_all_day":     is_all_day,
            "is_online":      is_online,
            "online_url":     online_url,
            "body_preview":   ev.get("bodyPreview", "")[:200],
            "response_status": resp_status,
        }

        # Bucket into yesterday / today / tomorrow
        if start_dt.date() == yesterday_start.date():
            yesterday_events.append(event)
        elif start_dt.date() == today_start.date():
            today_events.append(event)
        elif start_dt.date() == tomorrow_start.date():
            tomorrow_events.append(event)

    return {"yesterday": yesterday_events, "today": today_events, "tomorrow": tomorrow_events, "error": None}


# ── HTML rendering ───────────────────────────────────────────────────────────


def _briefing_key(subject: str, start_time: str) -> str:
    """Composite key so recurring meetings (same subject on multiple days /
    times) don't share or overwrite each other's briefing bullets."""
    return f"{subject}|{start_time}"

def analyse_calendar_events(events: list[dict], email_context: list[dict] = None) -> dict:
    """
    Use Claude to generate 2-3 bullet briefings per calendar event.

    events        — list of event dicts from fetch_calendar_events()
    email_context — list of raw email dicts from outlook_email (subject, sender,
                    body_preview) to cross-reference against meetings.

    Returns dict keyed by event subject: {"bullets": [...], "error": None|str}
    """
    if not _ANTHROPIC_AVAILABLE or not events:
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}

    # Build compact event summaries for the prompt
    event_summaries = []
    for ev in events:
        parts = [f"Meeting: {ev['subject']}"]
        parts.append(f"Start: {ev['start_time'] or 'All day'}")
        if not ev["is_all_day"]:
            parts.append(f"Time: {ev['start_time']} – {ev['end_time']}")
        if ev["organizer"]:
            parts.append(f"Organiser: {ev['organizer']}")
        if ev["attendee_count"] > 1:
            parts.append(f"Attendees: {ev['attendee_count']} people")
        if ev["location"]:
            parts.append(f"Location: {ev['location']}")
        if ev["body_preview"]:
            parts.append(f"Description: {ev['body_preview'][:300]}")
        event_summaries.append("\n".join(parts))

    # Build compact email context (subjects + senders + preview, capped at 30 emails)
    email_lines = []
    for em in (email_context or [])[:30]:
        subj   = em.get("subject", "")[:100]
        sender = em.get("from", em.get("sender", ""))[:60]
        prev   = em.get("body_preview", em.get("preview", ""))[:150]
        if subj:
            email_lines.append(f"• {subj} (from: {sender}) — {prev}")

    email_block = (
        "RECENT EMAILS (for cross-referencing):\n" + "\n".join(email_lines)
        if email_lines else "No email context available."
    )

    events_block = "\n\n---\n\n".join(event_summaries)

    prompt = f"""You are a professional executive assistant preparing a morning briefing for Doug McAlpine, who works at State Gas in Brisbane, Australia.

For each meeting listed below, write 2-3 bullet points using ONLY information explicitly present in:
1. The meeting details provided (title, organiser, attendees, description)
2. The email subjects and previews listed

STRICT RULES:
- Do NOT invent, assume, or hallucinate any context not present in the data above.
- Do NOT write generic advice like "review the agenda" or "prepare talking points".
- If a meeting has a description or body preview, summarise what it actually says.
- If an email subject clearly relates to a meeting, reference it specifically by subject.
- If you have genuinely no relevant context for a meeting, return a single bullet: "No additional context found in emails or meeting description."
- Each bullet must be under 25 words and grounded in the actual data provided.

{email_block}

CALENDAR EVENTS TO BRIEF:

{events_block}

Respond ONLY as JSON in this exact format (no markdown, no preamble):
{{
  "briefings": [
    {{
      "subject": "<exact meeting subject>",
      "start": "<exact Start value shown for that meeting>",
      "bullets": ["bullet 1", "bullet 2"]
    }}
  ]
}}"""

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = __import__("json").loads(raw)
        result = {}
        for item in data.get("briefings", []):
            start = item.get("start", "").replace("All day", "").strip()
            key = _briefing_key(item.get("subject", ""), start or "All day")
            result[key] = {
                "bullets": item.get("bullets", []),
                "error":   None,
            }
            # Subject-only fallback so a mangled "start" echo still matches one event
            result.setdefault(item.get("subject", ""), {
                "bullets": item.get("bullets", []),
                "error":   None,
            })
        return result
    except Exception as e:
        # Return empty rather than crashing — calendar tab degrades gracefully
        return {"_error": str(e)}


def _duration_label(mins: int) -> str:
    if mins <= 0:
        return ""
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _response_badge(status: str) -> str:
    badges = {
        "accepted":     ('<span class="cal-badge cal-badge-accepted">✓ Accepted</span>', ),
        "declined":     ('<span class="cal-badge cal-badge-declined">✗ Declined</span>', ),
        "tentativelyAccepted": ('<span class="cal-badge cal-badge-tentative">? Tentative</span>', ),
        "notResponded": ('<span class="cal-badge cal-badge-pending">⏳ Pending</span>', ),
        "organizer":    ('<span class="cal-badge cal-badge-organizer">👤 Organiser</span>', ),
    }
    return badges.get(status, ("",))[0]


def _brief_for(briefings: dict, ev: dict) -> dict:
    """Look up an event's briefing by composite key, falling back to subject."""
    if not briefings:
        return {}
    key = _briefing_key(ev["subject"], ev["start_time"] or "All day")
    return briefings.get(key) or briefings.get(ev["subject"], {})


def _event_card(ev: dict, is_past: bool = False, briefing: dict = None) -> str:
    subject   = _html.escape(ev["subject"])
    time_str  = ev["start_time"]
    end_str   = ev["end_time"]
    dur       = _duration_label(ev["duration_mins"])
    location  = _html.escape(ev["location"]) if ev["location"] else ""
    organizer = _html.escape(ev["organizer"]) if ev["organizer"] else ""
    preview   = _html.escape(ev["body_preview"]) if ev["body_preview"] else ""
    badge     = _response_badge(ev["response_status"])
    past_cls  = " cal-event-past" if is_past else ""
    all_day   = ev["is_all_day"]

    # Time block
    if all_day:
        time_block = '<div class="cal-event-time cal-all-day">All day</div>'
    else:
        time_block = f"""<div class="cal-event-time">
            <span class="cal-start">{time_str}</span>
            {"<span class='cal-end'>→ " + end_str + "</span>" if end_str else ""}
            {"<span class='cal-dur'>" + dur + "</span>" if dur else ""}
        </div>"""

    # Meta row
    meta_parts = []
    if location:
        loc_icon = "🔗" if ev["is_online"] else "📍"
        if ev["online_url"]:
            meta_parts.append(
                f'{loc_icon} <a href="{_html.escape(ev["online_url"])}" '
                f'target="_blank" class="cal-join-link">{location} — Join</a>'
            )
        else:
            meta_parts.append(f"{loc_icon} {location}")
    if organizer:
        meta_parts.append(f"👤 {organizer}")
    if ev["attendee_count"] > 1:
        meta_parts.append(f"👥 {ev['attendee_count']} attendees")

    meta_html = (
        '<div class="cal-event-meta">' + " &nbsp;·&nbsp; ".join(meta_parts) + "</div>"
        if meta_parts else ""
    )

    preview_html = (
        f'<div class="cal-event-preview">{preview}</div>' if preview else ""
    )

    # AI briefing bullets column
    bullets = (briefing or {}).get("bullets", [])
    if bullets:
        items = "".join(f'<li class="cal-brief-bullet">{_html.escape(b)}</li>' for b in bullets)
        brief_col = f'''<div class="cal-brief-col">
            <div class="cal-brief-label">📋 Briefing</div>
            <ul class="cal-brief-list">{items}</ul>
        </div>'''
    else:
        brief_col = ""

    return f"""<div class="cal-event{past_cls}">
    {time_block}
    <div class="cal-event-body">
        <div class="cal-event-subject">{subject} {badge}</div>
        {meta_html}
        {preview_html}
    </div>
    {brief_col}
</div>"""


def build_calendar_tab_html(calendar_data: dict, briefings: dict = None) -> str:
    """
    Build the full HTML for the Calendar tab.
    calendar_data — dict returned by fetch_calendar_events()
    briefings     — dict returned by analyse_calendar_events(), keyed by subject
    """
    if calendar_data.get("error"):
        err = _html.escape(str(calendar_data["error"]))
        return f"""<div class="cal-error">
            <p>⚠️ Could not load calendar: {err}</p>
            <p class="cal-error-hint">Check that <code>Calendars.Read</code> is added to your
            Azure app permissions and re-run <code>py outlook_email.py setup</code>.</p>
        </div>"""

    now_aest = datetime.datetime.now(AEST_OFFSET)

    yesterday_events = calendar_data.get("yesterday", [])
    today_events     = calendar_data.get("today", [])
    tomorrow_events  = calendar_data.get("tomorrow", [])

    # ── Yesterday section ────────────────────────────────────────────────────
    yest_dt = now_aest - datetime.timedelta(days=1)
    if yesterday_events:
        cards = "".join(_event_card(ev, is_past=True) for ev in yesterday_events)
        yesterday_html = f"""<section class="cal-day-section cal-yesterday">
            <div class="cal-day-header">
                <span class="cal-day-label">Yesterday</span>
                <span class="cal-day-date">{yest_dt.strftime(f"%A {DAYFMT} %B")}</span>
                <span class="cal-day-count">{len(yesterday_events)} event{"s" if len(yesterday_events) != 1 else ""}</span>
            </div>
            <div class="cal-events">{cards}</div>
        </section>"""
    else:
        yesterday_html = f"""<section class="cal-day-section cal-yesterday">
            <div class="cal-day-header">
                <span class="cal-day-label">Yesterday</span>
                <span class="cal-day-date">{yest_dt.strftime(f"%A {DAYFMT} %B")}</span>
            </div>
            <div class="cal-empty">No meetings yesterday.</div>
        </section>"""

    # ── Today section ────────────────────────────────────────────────────────
    if today_events:
        cards = ""
        for ev in today_events:
            is_past = (not ev["is_all_day"]) and (ev["end_dt"] < now_aest)
            brief   = _brief_for(briefings, ev)
            cards += _event_card(ev, is_past=is_past, briefing=brief)
        today_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Today</span>
                <span class="cal-day-date">{now_aest.strftime(f"%A {DAYFMT} %B")}</span>
                <span class="cal-day-count">{len(today_events)} event{"s" if len(today_events) != 1 else ""}</span>
            </div>
            <div class="cal-events">{cards}</div>
        </section>"""
    else:
        today_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Today</span>
                <span class="cal-day-date">{now_aest.strftime(f"%A {DAYFMT} %B")}</span>
            </div>
            <div class="cal-empty">🎉 No meetings today — enjoy the clear run!</div>
        </section>"""

    # ── Tomorrow section ─────────────────────────────────────────────────────
    tomorrow_dt = now_aest + datetime.timedelta(days=1)
    if tomorrow_events:
        cards = "".join(_event_card(ev, briefing=_brief_for(briefings, ev)) for ev in tomorrow_events)
        tomorrow_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Tomorrow</span>
                <span class="cal-day-date">{tomorrow_dt.strftime(f"%A {DAYFMT} %B")}</span>
                <span class="cal-day-count">{len(tomorrow_events)} event{"s" if len(tomorrow_events) != 1 else ""}</span>
            </div>
            <div class="cal-events">{cards}</div>
        </section>"""
    else:
        tomorrow_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Tomorrow</span>
                <span class="cal-day-date">{tomorrow_dt.strftime(f"%A {DAYFMT} %B")}</span>
            </div>
            <div class="cal-empty">No meetings scheduled for tomorrow.</div>
        </section>"""

    return f'<div class="cal-view">{yesterday_html}{today_html}{tomorrow_html}</div>'


# ── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("Fetching calendar events...")
        data = fetch_calendar_events()
        if data["error"]:
            print(f"ERROR: {data['error']}")
            sys.exit(1)
        print(f"\n=== TODAY ({len(data['today'])} events) ===")
        for ev in data["today"]:
            print(f"  {ev['start_time']:10} {ev['subject'][:60]}")
            if ev["location"]:
                print(f"             📍 {ev['location']}")
        print(f"\n=== TOMORROW ({len(data['tomorrow'])} events) ===")
        for ev in data["tomorrow"]:
            print(f"  {ev['start_time']:10} {ev['subject'][:60]}")
        print("\nDone.")
    else:
        print("Usage: py outlook_calendar.py test")
