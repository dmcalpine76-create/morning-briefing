"""
test_weekly_wrap.py
-------------------
The three blocks the wrap exists for, against synthetic data: what slipped
(with and without a prior snapshot), next week's shape (clashes, unprepared
external meetings, load) and the deadline radar.

    py tests/test_weekly_wrap.py
"""
import sys, datetime, re, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import weekly_wrap as w

AEST = w.AEST_OFFSET
now  = datetime.datetime(2026, 9, 25, 16, 30, tzinfo=AEST)   # a Friday
def ev(subj, day, h, dur=60, attend=None, allday=False):
    st = datetime.datetime(2026, 9, day, h, 0, tzinfo=AEST)
    return {"subject": subj, "start_dt": st,
            "end_dt": st + datetime.timedelta(minutes=dur),
            "in_hours_mins": dur, "counts_capacity": not allday,
            "is_all_day": allday, "attendee_emails": attend or [],
            "attendee_count": len(attend or []), "source": "outlook"}

fortnight = {"events": [
    ev("Board meeting", 28, 9, 180, ["a@stategas.com"]),
    ev("Investor call - Alpine", 28, 10, 60, ["t@alpinecapital.au"]),   # clash
    ev("ATP 2062 renewal lodgement", 30, 9, 60, ["x@stategas.com"]),
    ev("Site inspection", 29, 8, 240, ["ops@stategas.com"]),
    ev("Treasury model review", 1, 14, 60, ["holly.zhang@treasury.qld.gov.au"]),
    ev("Team standup", 2, 9, 30, ["a@stategas.com"]),
], "errors": []}

tasks = [
 {"id":"1","title":"Lodge ATP 2062 renewal application","detail":"due 30 Sep",
  "days_over":21,"last_modified":datetime.date(2026,9,2)},
 {"id":"2","title":"Prepare board papers","detail":"","days_over":6,
  "last_modified":datetime.date(2026,9,24)},
 {"id":"3","title":"Follow up Warwick re introduction","detail":"","days_over":40,
  "last_modified":datetime.date(2026,8,10)},
 {"id":"4","title":"Annual report sign-off","detail":"AGM 15 Nov","days_over":10,
  "last_modified":datetime.date(2026,9,20)},
]
import task_urgency
ranked = task_urgency.rank_tasks(tasks, fortnight["events"], now.date())

checks = []

# --- what slipped, with and without a prior snapshot
sl_none, had, pd = w.what_slipped(tasks, ranked, {"weeks": []}, now)
checks.append(("no history still finds stuck work", len(sl_none) > 0 and had is False))
hist = {"weeks":[{"date":"2026-09-18","open":[{"id":"1","title":"Lodge ATP"},
                                              {"id":"3","title":"Warwick"}]}]}
sl_hist, had2, pd2 = w.what_slipped(tasks, ranked, hist, now)
carried = [s for s in sl_hist if s["carried"]]
checks.append(("prior snapshot marks carry-overs", had2 is True and len(carried) == 2))
checks.append(("carry-overs rank first", sl_hist[0]["carried"] is True))
checks.append(("prev date reported", pd2 == "2026-09-18"))

# --- next week's shape
shape = w.next_week_shape(fortnight, tasks, now)
checks.append(("week starts the following Monday", shape["monday"] == datetime.date(2026,9,28)))
checks.append(("only Mon-Fri included", all(d["date"].weekday() < 5 for d in shape["days"])))
checks.append(("load computed", shape["total_hours"] > 0))
checks.append(("clash detected", len(shape["clashes"]) >= 1))
subj = [e.get("subject") for e in shape["unprepared"]]
checks.append(("external meeting with no task flagged", "Investor call - Alpine" in subj))
checks.append(("internal-only meeting not flagged", "Team standup" not in subj))
checks.append(("meeting matching a task not flagged", "Board meeting" not in subj))

# --- deadline radar
radar = w.deadline_radar(tasks, fortnight, now)
whats = [r["what"] for r in radar]
checks.append(("statutory task surfaced", any("ATP 2062" in x for x in whats)))
checks.append(("calendar lodgement surfaced", any("lodgement" in x.lower() for x in whats)))
checks.append(("dated items sorted first", radar[0]["when"] is not None))
checks.append(("deduplicated", len(whats) == len(set(w2.lower()[:60] for w2 in whats))))

# --- render
html = w.build_html("Next week is heavy.", sl_hist, True, pd2, shape, radar, now)
checks.append(("renders", len(html) > 2000))
checks.append(("no unresolved format fields", not re.search(r"\{[a-z_]+\}", html)))
checks.append(("escapes payload",
               "&amp;" in w.build_html("", [{"title":"A & B","reason":"x","days_over":1,
                                             "untouched":9,"carried":False,"score":10}],
                                       False, None, shape, radar, now)))
checks.append(("empty week is stated, not blank",
               "Nothing urgent is sitting still" in
               w.build_html("", [], True, "2026-09-18", shape, [], now)))
# %-d is legitimate on the DAYFMT definition line, which is the Windows/POSIX
# switch itself. Anywhere else it is the bug that crashed a briefing run.
_src = [l for l in open(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "weekly_wrap.py"), encoding="utf-8").read().splitlines()
        if not l.startswith("DAYFMT")]
checks.append(("no POSIX-only strftime outside the DAYFMT switch",
               not re.search(r"%-[dmHIjMSyU]", "\n".join(_src))))
checks.append(("DAYFMT switch present",
               'os.name == "nt"' in open(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "weekly_wrap.py"), encoding="utf-8").read()))

bad = 0
for label, ok in checks:
    print(("ok   " if ok else "FAIL ") + label); bad += not ok
print(f"\n{len(checks)-bad}/{len(checks)} passed")
open("/tmp/wrap_preview.html","w",encoding="utf-8").write(html)
sys.exit(1 if bad else 0)
