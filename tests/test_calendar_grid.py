"""
test_calendar_grid.py
---------------------
The reworked Calendar tab: a fortnight across, hours down the side.

Guards the two things most likely to regress silently - that personal time is
drawn but never counted toward booked hours, and that every block keeps a lane
tag, since colour alone cannot separate six lanes.

    py tests/test_calendar_grid.py
"""
import sys, re, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schedule_render as sr

AEST = sr.AEST_OFFSET
NOW  = datetime.datetime(2026, 9, 29, 11, 20, tzinfo=AEST)
D0   = NOW.date()

LANES = [{"id": "board", "name": "Board & Governance", "color": "#1a3a5c"},
         {"id": "internal", "name": "Internal & Team", "color": "#1a4a2e"},
         {"id": "personal", "name": "Personal", "color": "#3d3d38"}]

def ev(day_off, sh, eh, subj, lane, source="outlook", counts=True, allday=False):
    st = datetime.datetime.combine(D0 + datetime.timedelta(days=day_off),
                                   datetime.time(int(sh), int(round((sh % 1) * 60))),
                                   tzinfo=AEST)
    en = datetime.datetime.combine(D0 + datetime.timedelta(days=day_off),
                                   datetime.time(int(eh), int(round((eh % 1) * 60))),
                                   tzinfo=AEST)
    return {"subject": subj, "start_dt": st, "end_dt": en, "lane_id": lane,
            "source": source, "is_all_day": allday, "counts_capacity": counts,
            "in_hours_mins": 0 if allday else int((en - st).total_seconds() // 60)}

EVENTS = [
    ev(0, 9, 12, "Board meeting", "board"),                       # 3h work
    ev(0, 13, 14, "Ops review", "internal"),                      # 1h work
    ev(0, 7.25, 8.75, "Drop kids to school", "personal", "personal", counts=False),
    ev(1, 8, 18, "Long day", "internal"),                         # 10h -> over
    ev(3, 0, 0, "Leave", "personal", "personal", counts=False, allday=True),
]
CAL = {"days": [D0 + datetime.timedelta(days=i) for i in range(14)],
       "events": EVENTS, "lanes": LANES, "load": {}, "errors": []}

html = sr.build_calendar_tab(CAL, NOW)

CHECKS = [
    ("renders a grid", "cg-grid" in html),
    ("fourteen day columns", html.count('class="cg-col') == 14),
    ("hours run down the side", html.count('class="cg-hr"') >= 8),
    # personal is drawn...
    ("personal event is drawn", "Drop kids to school" in html),
    # ...but never counted
    ("booked counts work only, not personal", "4h booked" in html),
    ("free is nine less booked", "5h free" in html),
    ("over-commitment is flagged", "over by 1h" in html),
    # colour is never the only cue
    ("blocks carry a lane tag", html.count('class="cg-tag"') == 4),
    ("legend names every lane",
     all(l["name"].replace("&", "&amp;") in html for l in LANES)),
    ("personal is marked as uncounted", "not counted in booked hours" in html),
    # all-day items have no hour slot but must not vanish
    ("all-day item surfaced separately", "cg-adrow" in html and "Leave" in html),
    ("all-day item is not a grid block", html.count("Leave") == 1),
    ("today is marked", "cg-today" in html),
    ("now line drawn on today", "cg-now" in html),
    ("week boundary marked", "cg-wkstart" in html),
    ("no unresolved format fields", not re.search(r"\{[a-z_]+\}", html)),
    ("no POSIX-only strftime outside the DAYFMT switch",
     not re.search(r"%-[dmHIjMSyU]",
                   "\n".join(l for l in Path(sr.__file__).read_text(encoding="utf-8").splitlines()
                             if not l.startswith("DAYFMT")
                             and not l.lstrip().startswith("#")))),
    ("empty calendar says so", "No calendar data" in sr.build_calendar_tab({}, NOW)),
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
