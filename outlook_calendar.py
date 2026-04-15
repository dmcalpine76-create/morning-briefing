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
        # Use AEST timezone preference so Graph returns times already in AEST
        # This avoids ambiguous UTC strings with no Z suffix
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Prefer": 'outlook.timezone="AUS Eastern Standard Time"',
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
            # Graph returns times in AEST (requested via Prefer header).
            # Strings with no offset are already in AEST — attach AEST_OFFSET directly.
            # Strings with Z or +offset are converted to AEST.
            try:
                def _parse_dt(s):
                    # Normalise: strip sub-second precision, handle Z and offset formats
                    s = s.split(".")[0]  # remove fractional seconds
                    if s.endswith("Z"):
                        # UTC — convert to AEST
                        return datetime.datetime.fromisoformat(
                            s[:-1] + "+00:00"
                        ).astimezone(AEST_OFFSET)
                    elif "+" in s[10:] or (s.count("-") > 2):
                        # Has explicit timezone offset — parse and convert to AEST
                        return datetime.datetime.fromisoformat(s).astimezone(AEST_OFFSET)
                    else:
                        # No timezone info — treat as local (AEST = Brisbane)
                        return datetime.datetime.fromisoformat(s).replace(tzinfo=AEST_OFFSET)
                start_dt = _parse_dt(raw_start)
                end_dt   = _parse_dt(raw_end)
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


def analyse_calendar_events(events: list[dict], email_context: list[dict] = None) -> dict:
    """
    Use Claude Sonnet to generate per-event briefings, cross-referencing emails.

    For each event, we:
      1. Search email_context for fuzzy matches on subject, organiser, attendee names
      2. Pass matched emails + full event details to Claude
      3. Ask Claude to reason from actual evidence, not generic advice

    events        — list of event dicts (today + tomorrow only, not yesterday)
    email_context — full list of raw email dicts {subject, from, body_preview}

    Returns dict keyed by event subject: {"bullets": [...]}
    """
    if not _ANTHROPIC_AVAILABLE or not events:
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}

    client = _anthropic.Anthropic(api_key=api_key)
    results = {}

    for ev in events:
        subject   = ev["subject"]
        organiser = ev.get("organizer", "")
        preview   = ev.get("body_preview", "")
        attendees = ev.get("attendee_names", [])   # populated below if available

        # ── Find relevant emails by fuzzy keyword matching ──
        # Extract searchable terms: words from subject + organiser name parts
        import re as _re
        stop = {"the","a","an","and","or","re","fw","fwd","meeting","call",
                "catch","up","with","for","about","on","at","in","of","to"}
        def _terms(text):
            return {w.lower() for w in _re.split(r"[\s:,/\-]+", text)
                    if len(w) > 2 and w.lower() not in stop}

        # Build search terms from subject — but exclude words so common they
        # match everything (company name, domain, very short words)
        GLOBAL_STOPWORDS = {
            "state", "gas", "stategas", "doug", "mcalpine",  # too common in this inbox
            "meeting", "call", "catch", "catchup", "teams", "zoom",
            "the", "and", "for", "with", "about", "from", "this",
        }
        ev_terms = {w for w in _terms(subject) if w not in GLOBAL_STOPWORDS}

        # Add organiser surname only if it's a specific external name
        if organiser and "@" in organiser:
            domain = organiser.split("@")[-1].split(".")[0].lower()
            surname = organiser.split("@")[0].split(".")[-1].lower()
            # Only add if it's not internal (stategas domain)
            if "stategas" not in domain and len(surname) > 2:
                ev_terms.add(surname)
                ev_terms.add(domain)  # e.g. "santos", "qpmenergy", "bdo"

        # Score emails: count how many distinct ev_terms appear
        # Require at least 1 match, prefer emails with more matches
        scored_emails = []
        for em in (email_context or []):
            em_text = " ".join([
                em.get("subject", ""),
                em.get("from", ""),
                em.get("body_preview", ""),
            ]).lower()
            score = sum(1 for t in ev_terms if t in em_text)
            if score > 0:
                scored_emails.append((score, em))

        # Sort by score descending, take top 6
        scored_emails.sort(key=lambda x: x[0], reverse=True)
        matched_emails = [em for _, em in scored_emails[:6]]

        # Build email evidence block
        if matched_emails and ev_terms:
            em_lines = []
            for em in matched_emails:
                em_lines.append(
                    f"  • [{em.get('from','')}] {em.get('subject','')} — "
                    f"{em.get('body_preview','')[:200]}"
                )
            email_block = "MATCHED EMAILS:\n" + "\n".join(em_lines)
        else:
            email_block = "No emails matched this meeting."

        # Build event detail block
        ev_lines = [f"Subject: {subject}"]
        if not ev["is_all_day"]:
            ev_lines.append(f"Time: {ev['start_time']} – {ev['end_time']}")
        if organiser:
            ev_lines.append(f"Organiser: {organiser}")
        if ev["attendee_count"] > 0:
            ev_lines.append(f"Attendees: {ev['attendee_count']} people")
        if ev["location"]:
            ev_lines.append(f"Location: {ev['location']}")
        if preview:
            ev_lines.append(f"Description: {preview}")
        event_block = "\n".join(ev_lines)

        prompt = f"""You are preparing a morning briefing for Doug McAlpine, State Gas, Brisbane.

MEETING DETAILS:
{event_block}

{email_block}

Write 2-3 bullet points that will help Doug prepare for this meeting.

Rules:
- If emails matched: summarise what they reveal about this topic — be specific about what was discussed, requested, or is pending.
- If no emails matched but the description has content: summarise what the description says.
- If organiser or attendees suggest a known company or role, you may briefly note what kind of interaction this likely is (e.g. "Vendor review — [Company]").
- Do NOT invent agenda items, action lists, or preparation steps that aren't grounded in the actual data.
- If there is genuinely nothing useful to say, write one bullet: "No email context found — review any pre-meeting materials."
- Keep each bullet under 30 words.

Respond ONLY as JSON: {{"bullets": ["bullet 1", "bullet 2"]}}"""

        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = __import__("json").loads(raw)
            results[subject] = {"bullets": data.get("bullets", []), "error": None}
        except Exception as e:
            results[subject] = {"bullets": [], "error": str(e)}

    return results


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
                <span class="cal-day-date">{yest_dt.strftime("%A %#d %B")}</span>
                <span class="cal-day-count">{len(yesterday_events)} event{"s" if len(yesterday_events) != 1 else ""}</span>
            </div>
            <div class="cal-events">{cards}</div>
        </section>"""
    else:
        yesterday_html = f"""<section class="cal-day-section cal-yesterday">
            <div class="cal-day-header">
                <span class="cal-day-label">Yesterday</span>
                <span class="cal-day-date">{yest_dt.strftime("%A %#d %B")}</span>
            </div>
            <div class="cal-empty">No meetings yesterday.</div>
        </section>"""

    # ── Today section ────────────────────────────────────────────────────────
    if today_events:
        cards = ""
        for ev in today_events:
            is_past = (not ev["is_all_day"]) and (ev["end_dt"] < now_aest)
            brief   = (briefings or {}).get(ev["subject"], {})
            cards += _event_card(ev, is_past=is_past, briefing=brief)
        today_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Today</span>
                <span class="cal-day-date">{now_aest.strftime("%A %#d %B")}</span>
                <span class="cal-day-count">{len(today_events)} event{"s" if len(today_events) != 1 else ""}</span>
            </div>
            <div class="cal-events">{cards}</div>
        </section>"""
    else:
        today_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Today</span>
                <span class="cal-day-date">{now_aest.strftime("%A %#d %B")}</span>
            </div>
            <div class="cal-empty">🎉 No meetings today — enjoy the clear run!</div>
        </section>"""

    # ── Tomorrow section ─────────────────────────────────────────────────────
    tomorrow_dt = now_aest + datetime.timedelta(days=1)
    if tomorrow_events:
        cards = "".join(_event_card(ev, briefing=(briefings or {}).get(ev["subject"], {})) for ev in tomorrow_events)
        tomorrow_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Tomorrow</span>
                <span class="cal-day-date">{tomorrow_dt.strftime("%A %#d %B")}</span>
                <span class="cal-day-count">{len(tomorrow_events)} event{"s" if len(tomorrow_events) != 1 else ""}</span>
            </div>
            <div class="cal-events">{cards}</div>
        </section>"""
    else:
        tomorrow_html = f"""<section class="cal-day-section">
            <div class="cal-day-header">
                <span class="cal-day-label">Tomorrow</span>
                <span class="cal-day-date">{tomorrow_dt.strftime("%A %#d %B")}</span>
            </div>
            <div class="cal-empty">No meetings scheduled for tomorrow.</div>
        </section>"""

    return f'<div class="cal-view">{yesterday_html}{today_html}{tomorrow_html}</div>'


# ── Calendar tab CSS (injected into briefing.py's <style> block) ─────────────

CALENDAR_CSS = """
        /* ── CALENDAR TAB ── */
        .cal-view { padding: 0; }
        .cal-day-section { margin-bottom: 0; border-bottom: 1px solid var(--rule); }
        .cal-day-header {
            display: flex; align-items: baseline; gap: 0.75rem;
            padding: 0.65rem 1.25rem;
            background: var(--paper-2);
            border-bottom: 1px solid var(--rule);
            position: sticky; top: 0; z-index: 10;
        }
        .cal-day-label {
            font-family: var(--font-display); font-size: 0.75rem;
            font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase;
            color: var(--ink);
        }
        .cal-day-date { font-size: 0.78rem; color: var(--ink-light); }
        .cal-day-count {
            margin-left: auto; font-size: 0.62rem; font-weight: 700;
            letter-spacing: 0.1em; text-transform: uppercase;
            color: var(--ink-light); background: var(--white);
            border: 1px solid var(--rule); padding: 0.12rem 0.5rem;
            border-radius: 2rem;
        }

        .cal-events { padding: 0.5rem 0; }
        .cal-event {
            display: flex; gap: 1rem; align-items: flex-start;
            padding: 0.7rem 1.25rem;
            border-bottom: 1px solid var(--rule);
            transition: background 0.15s;
        }
        .cal-event:last-child { border-bottom: none; }
        .cal-event:hover { background: var(--paper-2); }
        .cal-event-past { opacity: 0.45; }

        .cal-event-time {
            min-width: 90px; flex-shrink: 0;
            display: flex; flex-direction: column; gap: 2px;
            padding-top: 1px;
        }
        .cal-start { font-size: 0.82rem; font-weight: 700; color: var(--ink); }
        .cal-end   { font-size: 0.72rem; color: var(--ink-light); }
        .cal-dur   {
            font-size: 0.62rem; font-weight: 700; letter-spacing: 0.08em;
            text-transform: uppercase; color: var(--ink-light);
            background: var(--paper-2); border: 1px solid var(--rule);
            padding: 0.08rem 0.35rem; border-radius: 3px; align-self: flex-start;
            margin-top: 2px;
        }
        .cal-all-day {
            font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em;
            text-transform: uppercase; color: var(--ink-light);
        }

        .cal-event-body  { flex: 1; min-width: 0; }
        .cal-event-subject {
            font-size: 0.88rem; font-weight: 700; color: var(--ink);
            line-height: 1.3; margin-bottom: 4px;
        }
        .cal-event-meta {
            font-size: 0.75rem; color: var(--ink-light); margin-bottom: 3px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .cal-join-link {
            color: #2980b9; text-decoration: none; font-weight: 600;
        }
        .cal-join-link:hover { text-decoration: underline; }
        .cal-event-preview {
            font-size: 0.74rem; color: var(--ink-light);
            display: -webkit-box; -webkit-line-clamp: 2;
            -webkit-box-orient: vertical; overflow: hidden;
            margin-top: 3px; line-height: 1.4;
        }

        /* Response badges */
        .cal-badge {
            display: inline-block; font-size: 0.58rem; font-weight: 700;
            letter-spacing: 0.08em; text-transform: uppercase;
            padding: 0.1rem 0.4rem; border-radius: 3px;
            vertical-align: middle; margin-left: 5px;
        }
        .cal-badge-accepted   { background: #eafaf1; color: #27ae60; border: 1px solid #a9dfbf; }
        .cal-badge-declined   { background: #fdecea; color: #c0392b; border: 1px solid #f1948a; }
        .cal-badge-tentative  { background: #fef9e7; color: #b7770d; border: 1px solid #f9e79f; }
        .cal-badge-pending    { background: var(--paper-2); color: var(--ink-light); border: 1px solid var(--rule); }
        .cal-badge-organizer  { background: #eaf2ff; color: #2471a3; border: 1px solid #aed6f1; }

        .cal-empty {
            padding: 1.5rem 1.25rem; font-size: 0.82rem;
            color: var(--ink-light); font-style: italic;
        }

        .cal-error {
            padding: 1.5rem 1.25rem; color: #c0392b; font-size: 0.85rem;
        }
        .cal-error-hint { color: var(--ink-light); font-size: 0.8rem; margin-top: 0.5rem; }
        .cal-error code {
            background: var(--paper-2); padding: 0.1rem 0.3rem;
            border-radius: 3px; font-family: monospace; font-size: 0.85em;
        }

        @media (max-width: 700px) {
            .cal-event { flex-direction: column; gap: 0.3rem; }
            .cal-event-time { min-width: unset; flex-direction: row; align-items: center; gap: 0.5rem; }
        }
"""


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
