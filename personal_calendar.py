"""
personal_calendar.py
--------------------
Reads the personal Google calendar from its private iCal feed and returns
events in exactly the same dict shape as outlook_calendar.fetch_calendar_events(),
so the briefing can merge the two lists without anything downstream caring.

Why an ICS feed and not the Google Calendar API:
  - no OAuth, no second token to refresh alongside the 90-day Outlook one
  - a single URL in .env / GitHub Secrets is the whole configuration

Subscribe to the PERSONAL calendar's secret address, NOT the merged view and
NOT the subscribed copy of Outlook. Because the Outlook -> Google link is a
native subscription, the personal calendar contains only personal events, so
merging with Graph needs no deduplication.

Config lives in briefing_settings.json under "personal_calendar".
Secret URL lives in .env / GitHub Secrets as GOOGLE_CAL_ICS_URL.

Self-test:
    py personal_calendar.py --test                 # uses GOOGLE_CAL_ICS_URL
    py personal_calendar.py --test some_file.ics   # uses a local file
"""

import os
import sys
import json
import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

# Brisbane has no daylight saving, so a fixed +10 offset is correct all year.
AEST_OFFSET  = datetime.timezone(datetime.timedelta(hours=10))
SETTINGS_FILE = Path(__file__).parent / "briefing_settings.json"
REQUEST_TIMEOUT = 30

DEFAULT_CFG = {
    "enabled":        True,
    "lane":           "Personal",
    "show_titles":    True,
    "count_capacity": True,
    "work_day_start": "08:00",
    "work_day_end":   "18:00",
    "work_days":      [0, 1, 2, 3, 4],   # Monday = 0
    "redacted_label": "Personal — busy",
}

_MISSING_LIBS_HINT = (
    "personal calendar needs two libraries:\n"
    "    pip install icalendar recurring-ical-events"
)


def _load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if SETTINGS_FILE.exists():
        try:
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            cfg.update(s.get("personal_calendar", {}) or {})
        except Exception as e:
            print(f"  WARNING: could not read personal_calendar settings: {e}")
    return cfg


def _hhmm(value: str, fallback: str) -> datetime.time:
    try:
        h, m = str(value).split(":")
        return datetime.time(int(h), int(m))
    except Exception:
        h, m = fallback.split(":")
        return datetime.time(int(h), int(m))


def _as_aest(value):
    """Normalise an icalendar date or datetime to an AEST-aware datetime."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=AEST_OFFSET)
        return value.astimezone(AEST_OFFSET)
    # a plain date == an all-day event; anchor it at local midnight
    return datetime.datetime(value.year, value.month, value.day, tzinfo=AEST_OFFSET)


def working_minutes(start_dt, end_dt, cfg) -> int:
    """
    Minutes of this event that land inside the configured working day.
    A 6:30pm dinner returns 0; an 8am dentist returns its full length.
    Events spanning several days are summed a day at a time.
    """
    ws = _hhmm(cfg.get("work_day_start"), DEFAULT_CFG["work_day_start"])
    we = _hhmm(cfg.get("work_day_end"),   DEFAULT_CFG["work_day_end"])
    work_days = set(cfg.get("work_days") or DEFAULT_CFG["work_days"])

    total = 0
    day = start_dt.date()
    while day <= end_dt.date():
        if day.weekday() in work_days:
            win_start = datetime.datetime.combine(day, ws, tzinfo=AEST_OFFSET)
            win_end   = datetime.datetime.combine(day, we, tzinfo=AEST_OFFSET)
            lo = max(start_dt, win_start)
            hi = min(end_dt,   win_end)
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        day += datetime.timedelta(days=1)
    return total


def _to_event(comp, start_raw, end_raw, cfg) -> dict:
    """Build the same dict shape outlook_calendar.fetch_calendar_events() returns."""
    start_dt = _as_aest(start_raw)
    end_dt   = _as_aest(end_raw)

    is_all_day = not isinstance(start_raw, datetime.datetime)
    duration   = 0 if is_all_day else int((end_dt - start_dt).total_seconds() // 60)

    raw_subject = str(comp.get("SUMMARY", "") or "(No title)")
    subject = raw_subject if cfg.get("show_titles", True) else cfg.get(
        "redacted_label", DEFAULT_CFG["redacted_label"])

    location = str(comp.get("LOCATION", "") or "").strip()
    desc     = str(comp.get("DESCRIPTION", "") or "").strip()

    if is_all_day:
        start_str, end_str = "All day", ""
    else:
        start_str = start_dt.strftime("%I:%M %p").lstrip("0")
        end_str   = end_dt.strftime("%I:%M %p").lstrip("0")

    in_hours = working_minutes(start_dt, end_dt, cfg) if not is_all_day else 0

    return {
        "subject":         subject,
        "start_time":      start_str,
        "end_time":        end_str,
        "start_dt":        start_dt,
        "end_dt":          end_dt,
        "duration_mins":   duration,
        "location":        location,
        "organizer":       "",
        "attendee_count":  0,
        "is_all_day":      is_all_day,
        "is_online":       False,
        "online_url":      "",
        "body_preview":    "" if not cfg.get("show_titles", True) else desc[:200],
        "response_status": "organizer",
        # merge metadata — the work rules must never run over these
        "source":          "personal",
        "lane":            cfg.get("lane", "Personal"),
        "in_hours_mins":   in_hours,
        "counts_capacity": bool(cfg.get("count_capacity", True)) and in_hours > 0,
    }


def fetch_personal_events(days_ahead: int = 14, ics_text: str = None) -> dict:
    """
    Returns {"events": [event_dict, ...], "error": None | str}

    Events run from the start of today to start_of_today + days_ahead,
    with recurring series expanded into concrete instances.
    Pass ics_text to parse a feed you already have (used by --test).
    """
    cfg = _load_cfg()
    if not cfg.get("enabled", True):
        return {"events": [], "error": None}

    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        return {"events": [], "error": _MISSING_LIBS_HINT}

    if ics_text is None:
        url = os.environ.get("GOOGLE_CAL_ICS_URL", "").strip()
        if not url:
            return {"events": [], "error":
                    "GOOGLE_CAL_ICS_URL not set — personal calendar skipped"}
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            ics_text = resp.text
        except Exception as e:
            return {"events": [], "error": f"could not fetch personal calendar: {e}"}

    try:
        cal = icalendar.Calendar.from_ical(ics_text)
    except Exception as e:
        return {"events": [], "error": f"could not parse personal calendar: {e}"}

    now         = datetime.datetime.now(AEST_OFFSET)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end  = today_start + datetime.timedelta(days=days_ahead)

    try:
        # Expands RRULE series and honours EXDATE cancellations and
        # RECURRENCE-ID overrides for moved instances.
        occurrences = recurring_ical_events.of(cal).between(today_start, window_end)
    except Exception as e:
        return {"events": [], "error": f"could not expand recurring events: {e}"}

    events = []
    for comp in occurrences:
        try:
            start_raw = comp.get("DTSTART").dt
            end_prop  = comp.get("DTEND")
            if end_prop is not None:
                end_raw = end_prop.dt
            elif isinstance(start_raw, datetime.datetime):
                end_raw = start_raw + datetime.timedelta(hours=1)
            else:
                end_raw = start_raw + datetime.timedelta(days=1)

            status = str(comp.get("STATUS", "") or "").upper()
            if status == "CANCELLED":
                continue

            events.append(_to_event(comp, start_raw, end_raw, cfg))
        except Exception as e:
            print(f"  WARNING: skipped a personal event: {e}")

    events.sort(key=lambda e: e["start_dt"])
    return {"events": events, "error": None}


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test(source: str = None):
    print("Personal calendar self-test")
    print("=" * 60)

    cfg = _load_cfg()
    print(f"config      : lane={cfg['lane']}  show_titles={cfg['show_titles']}  "
          f"working day {cfg['work_day_start']}-{cfg['work_day_end']}")

    try:
        import icalendar, recurring_ical_events          # noqa: F401
        print("libraries   : OK")
    except ImportError:
        print("libraries   : MISSING")
        print()
        print(_MISSING_LIBS_HINT)
        return 1

    ics_text = None
    if source:
        p = Path(source)
        if not p.exists():
            print(f"file not found: {source}")
            return 1
        ics_text = p.read_text(encoding="utf-8", errors="replace")
        print(f"source      : {p.name} ({len(ics_text)} chars)")
    else:
        url = os.environ.get("GOOGLE_CAL_ICS_URL", "").strip()
        if not url:
            print("source      : GOOGLE_CAL_ICS_URL is not set in .env")
            return 1
        print(f"source      : GOOGLE_CAL_ICS_URL ({len(url)} chars)")

    result = fetch_personal_events(14, ics_text=ics_text)
    if result["error"]:
        print(f"ERROR       : {result['error']}")
        return 1

    events = result["events"]
    print(f"events      : {len(events)} over the next 14 days")
    counted = sum(1 for e in events if e["counts_capacity"])
    print(f"              {counted} overlap your working day and count against capacity")
    print()

    if not events:
        print("No events found. If your calendar is not empty, check that the URL is")
        print("the secret iCal address of your PERSONAL calendar.")
        return 0

    print(f"{'date':<12} {'time':<18} {'in-hrs':>7}  subject")
    print("-" * 74)
    for e in events:
        day  = e["start_dt"].strftime("%a %d %b")
        when = "all day" if e["is_all_day"] else f"{e['start_time']} - {e['end_time']}"
        mins = f"{e['in_hours_mins']}m" if e["in_hours_mins"] else "-"
        print(f"{day:<12} {when:<18} {mins:>7}  {e['subject'][:36]}")

    print()
    print("Check that repeating events appear on every expected day, that anything")
    print("you cancelled is absent, and that anything you moved shows its new time.")
    return 0


if __name__ == "__main__":
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if a != "--test"]
    sys.exit(_self_test(args[0] if args else None))
