"""
test_personal_calendar_feeds.py
-------------------------------
A Google secret iCal address covers one calendar and there is no combined
feed, so the briefing saw only Doug's primary calendar - about 13 events a
fortnight - while 19 more sat on the McAlpine Family Calendar. These guard
the multi-feed reader and the merge.

    py tests/test_personal_calendar_feeds.py
"""
import sys, os, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import personal_calendar as pc

def _ics(events):
    body = ""
    for uid, summ, start, end in events:
        body += ("BEGIN:VEVENT\r\n"
                 f"UID:{uid}\r\n"
                 f"SUMMARY:{summ}\r\n"
                 f"DTSTART;TZID=Australia/Brisbane:{start}\r\n"
                 f"DTEND;TZID=Australia/Brisbane:{end}\r\n"
                 "STATUS:CONFIRMED\r\nEND:VEVENT\r\n")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
            + body + "END:VCALENDAR\r\n")

def _stamp(day_offset, hh, mm=0):
    d = datetime.datetime.now(pc.AEST_OFFSET) + datetime.timedelta(days=day_offset)
    return d.replace(hour=hh, minute=mm, second=0, microsecond=0).strftime("%Y%m%dT%H%M%S")

PRIMARY = _ics([("p1", "Weekly Vroom Meeting", _stamp(1, 20), _stamp(1, 22)),
                ("p2", "Vinnie's 50th",        _stamp(2, 17), _stamp(2, 23))])
FAMILY  = _ics([("f1", "Doug Gym",             _stamp(1, 7, 30), _stamp(1, 8, 30)),
                ("f2", "Drop Kids to School",  _stamp(2, 7, 15), _stamp(2, 8, 45)),
                # same event present on both calendars - must appear once
                ("f3", "Weekly Vroom Meeting", _stamp(1, 20), _stamp(1, 22))])

class _Resp:
    def __init__(self, text): self.text = text
    def raise_for_status(self): pass

def _run(urls, served, monkey_fail=None):
    for k in list(os.environ):
        if k.startswith("GOOGLE_CAL_ICS_URL"):
            del os.environ[k]
    os.environ["GOOGLE_CAL_ICS_URL"] = urls
    real = pc.requests.get
    def fake(url, **kw):
        if monkey_fail and monkey_fail in url:
            raise RuntimeError("feed down")
        return _Resp(served[url])
    pc.requests.get = fake
    try:
        return pc.fetch_personal_events(14)
    finally:
        pc.requests.get = real

SERVED = {"https://primary/basic.ics": PRIMARY, "https://family/basic.ics": FAMILY}

try:
    import icalendar, recurring_ical_events   # noqa: F401
    HAVE_LIBS = True
except ImportError:
    HAVE_LIBS = False

CHECKS = []
if HAVE_LIBS:
    one = _run("https://primary/basic.ics", SERVED)
    both = _run("https://primary/basic.ics,https://family/basic.ics", SERVED)
    titles = sorted(e["subject"] for e in both["events"])
    degraded = _run("https://primary/basic.ics,https://family/basic.ics",
                    SERVED, monkey_fail="family")
    CHECKS = [
        ("one feed reads only its own calendar", len(one["events"]) == 2),
        ("two feeds merge", len(both["events"]) == 4),
        ("family events now present", "Doug Gym" in titles
                                      and "Drop Kids to School" in titles),
        ("duplicate across calendars appears once",
         titles.count("Weekly Vroom Meeting") == 1),
        ("events stay sorted",
         all(both["events"][i]["start_dt"] <= both["events"][i + 1]["start_dt"]
             for i in range(len(both["events"]) - 1))),
        # one bad feed must never cost the others
        ("a dead feed does not lose the good one", len(degraded["events"]) == 2),
        ("a dead feed is reported", "unreachable" in (degraded["error"] or "")),
    ]
else:
    print("icalendar / recurring_ical_events not installed here - "
          "feed-merge checks skipped, URL parsing still covered")

CHECKS += [
    ("no feeds configured is stated",
     "not set" in (_run("", {})["error"] or "")),
]

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    bad = 0
    for label, ok in CHECKS:
        print(("ok   " if ok else "FAIL ") + label)
        bad += not ok
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} passed")
    sys.exit(1 if bad else 0)
