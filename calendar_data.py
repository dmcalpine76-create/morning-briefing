"""
calendar_data.py
----------------
One merged, lane-classified fortnight of commitments, from two sources.

Deliberately additive: outlook_calendar.fetch_calendar_events() is left exactly
as it is, because the existing Calendar tab still depends on its
yesterday/today/tomorrow buckets. This module fetches its own 14-day window
through the same token and parsing conventions, so nothing that works today
changes behaviour.

  Outlook  -> work events, full fidelity (attendees, organiser, join links)
  Google   -> personal events, via personal_calendar.py

No deduplication between them: the Outlook link into Google is a native
subscription, so the personal calendar holds only personal events. Where an
overlap does exist it is handled by an explicit suppression list in settings,
never by fuzzy guessing.

Self-test:
    py calendar_data.py --test
"""

import datetime
from collections import defaultdict

import requests

AEST_OFFSET     = datetime.timezone(datetime.timedelta(hours=10))
GRAPH_BASE      = "https://graph.microsoft.com/v1.0"
REQUEST_TIMEOUT = 30


def _fetch_outlook_window(days_ahead: int = 14) -> dict:
    """A flat list over the whole window, unlike the 3-day bucketed version."""
    try:
        import outlook_calendar
    except ImportError:
        return {"events": [], "error": "outlook_calendar.py not available"}

    try:
        token = outlook_calendar._get_token()
    except Exception as e:
        return {"events": [], "error": str(e)}

    now         = datetime.datetime.now(AEST_OFFSET)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end  = today_start + datetime.timedelta(days=days_ahead)

    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/calendarView",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Prefer": 'outlook.timezone="UTC"',
            },
            params={
                "startDateTime": today_start.astimezone(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime":   window_end.astimezone(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "$select": ("subject,start,end,location,organizer,attendees,"
                            "isAllDay,isOnlineMeeting,onlineMeetingUrl,bodyPreview,"
                            "responseStatus"),
                "$orderby": "start/dateTime",
                "$top": 250,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json().get("value", []) or []
    except Exception as e:
        return {"events": [], "error": f"could not read the work calendar: {e}"}

    events = []
    for ev in raw:
        try:
            events.append(_normalise_outlook(ev))
        except Exception as e:
            print(f"  WARNING: skipped a work event: {e}")
    return {"events": events, "error": None}


def _normalise_outlook(ev: dict) -> dict:
    is_all_day = ev.get("isAllDay", False)

    def _dt(part):
        raw = (ev.get(part) or {}).get("dateTime", "")
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(AEST_OFFSET)

    start_dt, end_dt = _dt("start"), _dt("end")

    loc_obj  = ev.get("location") or {}
    location = (loc_obj.get("displayName") or "").strip() if isinstance(loc_obj, dict) else ""
    online_url = ev.get("onlineMeetingUrl") or ""
    if location.startswith("http"):
        online_url, location = location, "Microsoft Teams"

    org_obj   = (ev.get("organizer") or {}).get("emailAddress", {}) or {}
    attendees = ev.get("attendees") or []

    return {
        "subject":         ev.get("subject") or "(No title)",
        "start_time":      "All day" if is_all_day else start_dt.strftime("%I:%M %p").lstrip("0"),
        "end_time":        "" if is_all_day else end_dt.strftime("%I:%M %p").lstrip("0"),
        "start_dt":        start_dt,
        "end_dt":          end_dt,
        "duration_mins":   0 if is_all_day else int((end_dt - start_dt).total_seconds() // 60),
        "location":        location,
        "organizer":       org_obj.get("name", org_obj.get("address", "")),
        "organizer_email": org_obj.get("address", ""),
        "attendee_emails": [
            (a.get("emailAddress") or {}).get("address", "") for a in attendees
        ],
        "attendee_count":  len(attendees),
        "is_all_day":      is_all_day,
        "is_online":       ev.get("isOnlineMeeting", False),
        "online_url":      online_url,
        "body_preview":    (ev.get("bodyPreview") or "")[:200],
        "response_status": (ev.get("responseStatus") or {}).get("response", "none"),
        "source":          "outlook",
        "in_hours_mins":   0 if is_all_day else int((end_dt - start_dt).total_seconds() // 60),
        "counts_capacity": not is_all_day,
    }


def _suppressed(ev: dict, patterns: list) -> bool:
    """Personal events matching a configured pattern lose to the Outlook copy."""
    subject = (ev.get("subject") or "").lower()
    return any(p.strip().lower() in subject for p in patterns or [] if p.strip())


def fetch_fortnight(days_ahead: int = 14) -> dict:
    """
    Returns:
      {
        "events":   [...],            merged, classified, sorted
        "by_day":   {date: [...]},    for the swimlane columns
        "days":     [date, ...],      every day in the window, in order
        "load":     {date: minutes},  capacity used per day
        "lanes":    [lane dict, ...], lanes actually in use, in config order
        "errors":   [str, ...],
      }
    """
    errors = []

    work = _fetch_outlook_window(days_ahead)
    if work.get("error"):
        errors.append(work["error"])
    events = list(work.get("events", []))

    try:
        import personal_calendar
        cfg = personal_calendar._load_cfg()
        personal = personal_calendar.fetch_personal_events(days_ahead)
        if personal.get("error"):
            errors.append(personal["error"])
        suppress = cfg.get("suppress_titles", [])
        events += [e for e in personal.get("events", []) if not _suppressed(e, suppress)]
    except ImportError:
        errors.append("personal_calendar.py not available")
    except Exception as e:
        errors.append(f"personal calendar skipped: {e}")

    try:
        import calendar_lanes
        lane_cfg = calendar_lanes.load_lane_config()
        calendar_lanes.classify_all(events, lane_cfg)
        lanes_in_order = list(lane_cfg.get("lanes", []))
        if lane_cfg.get("unfiled_lane_visible", True):
            lanes_in_order.append(calendar_lanes.UNFILED)
    except Exception as e:
        errors.append(f"lane classification skipped: {e}")
        lanes_in_order = []

    events.sort(key=lambda e: (e["start_dt"], e.get("subject", "")))

    now   = datetime.datetime.now(AEST_OFFSET)
    day0  = now.date()
    days  = [day0 + datetime.timedelta(days=i) for i in range(days_ahead)]

    by_day = defaultdict(list)
    load   = {d: 0 for d in days}
    for e in events:
        d = e["start_dt"].date()
        # multi-day events belong to every day they touch
        last = e["end_dt"].date() if e.get("end_dt") else d
        cur = d
        while cur <= last and cur <= days[-1]:
            if cur >= day0:
                by_day[cur].append(e)
            cur += datetime.timedelta(days=1)
        if e.get("counts_capacity") and d in load:
            load[d] += e.get("in_hours_mins", 0) or 0

    used_ids = {e.get("lane_id") for e in events}
    lanes = [l for l in lanes_in_order if l.get("id") in used_ids] or lanes_in_order

    return {"events": events, "by_day": dict(by_day), "days": days,
            "load": load, "lanes": lanes, "errors": errors}


def _self_test():
    print("Merged calendar self-test")
    print("=" * 66)
    data = fetch_fortnight(14)
    for e in data["errors"]:
        print(f"  note: {e}")
    print()

    events = data["events"]
    work   = [e for e in events if e.get("source") == "outlook"]
    pers   = [e for e in events if e.get("source") == "personal"]
    print(f"events    : {len(events)}  ({len(work)} work, {len(pers)} personal)")
    print(f"lanes used: {[l['name'] for l in data['lanes']]}")
    unfiled = [e for e in events if e.get('lane_id') == 'unfiled']
    print(f"unfiled   : {len(unfiled)}")
    for e in unfiled[:8]:
        print(f"            - {e['subject'][:50]}")
    print()
    print(f"{'day':<12} {'hrs':>5}  events")
    print("-" * 66)
    for d in data["days"]:
        evs = data["by_day"].get(d, [])
        hrs = data["load"].get(d, 0) / 60
        print(f"{d.strftime('%a %d %b'):<12} {hrs:>5.1f}  " +
              ", ".join(e["subject"][:22] for e in evs[:3]) +
              (f"  +{len(evs)-3}" if len(evs) > 3 else ""))
    print()
    print("Check the lane assignments look sensible and that anything in Unfiled")
    print("deserves a keyword or an override in briefing_settings.json.")
    return 0


if __name__ == "__main__":
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import sys
    sys.exit(_self_test())
