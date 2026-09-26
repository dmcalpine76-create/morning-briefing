"""
weekly_wrap.py  —  Friday 16:30 week-in-review  (D14, rebuilt)
----------------------------------------------------------------
The first version reported what had already happened: meetings attended,
tasks completed, price moves, and themes guessed from email subject lines.
All of it was a receipt for a week Doug had just lived. Nothing in it was
exception-based and nothing looked forward, so there was nothing to act on.

This version reports only what a day-by-day reader cannot see:

  1. WHAT SLIPPED      tasks that were urgent and are still open, with how
                       long they have been stuck and what is blocking them.
                       Uses a rolling snapshot so week-over-week movement is
                       real rather than inferred, and degrades to To Do's own
                       lastModified when no snapshot exists yet.
  2. NEXT WEEK'S SHAPE load per day, clashes, and the external meetings with
                       no preparation behind them.
  3. DEADLINE RADAR    statutory, tenure and lodgement dates inside 30 days,
                       from task text and the calendar.

Then one short synthesis paragraph at the top, grounded only in those three
blocks, so the mail can be forwarded or filed as a record of the week.

Run manually:   py weekly_wrap.py
                py weekly_wrap.py --dry      (writes friday_wrap.html, sends nothing)
Scheduled:      .github/workflows/friday_wrap.yml (Fridays 16:30 AEST)
"""

import os
import re
import sys
import json
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import outlook_email as _outlook

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
AEST_OFFSET  = datetime.timezone(datetime.timedelta(hours=10))
DAYFMT       = "%#d" if os.name == "nt" else "%-d"
HISTORY_FILE = "task_history.json"
PUBLISH_DIR  = Path("wrap_publish")
SITE_URL     = os.environ.get(
    "BRIEFING_SITE_URL",
    "https://dmcalpine76-create.github.io/morning-briefing").rstrip("/")
INTERNAL     = "stategas.com"

# palette, matched to the briefing
INK, MUTED, RULE, PAPER = "#1a1a17", "#8c887b", "#e4e1d8", "#f5f2eb"
RED, AMBER, GREEN = "#b3261e", "#8a6d1f", "#1e7d32"


# ── rolling snapshot ─────────────────────────────────────────────────────────
# Published to gh-pages beside gas_history.json and fetched back next week, the
# same trick the daily briefing uses for the gas price sparkline. Without it
# "what slipped" can only ever mean "what looks old", which is a weaker claim.

def load_task_history() -> dict:
    try:
        r = requests.get(f"{SITE_URL}/{HISTORY_FILE}", timeout=15)
        if r.ok:
            return r.json()
    except Exception as e:
        print(f"   .  no previous task history ({e})")
    return {"weeks": []}


def save_task_history(hist: dict, tasks: list, now: datetime.datetime) -> None:
    """
    Never snapshot an empty list. If the To Do fetch failed - an expired token,
    a network refusal - `tasks` is [] and writing that would tell next Friday
    that everything got done. The wrong answer, stated confidently, is worse
    than no answer: the following week's "what slipped" would come back clean
    and Doug would believe it.
    """
    if not tasks:
        print("   !  no tasks read - snapshot NOT written "
              "(an empty week would read as 'everything closed' next Friday)")
        return

    weeks = [w for w in hist.get("weeks", []) if w.get("date") != now.date().isoformat()]
    weeks.append({
        "date": now.date().isoformat(),
        "open": [{"id": t.get("id"), "title": (t.get("title") or "")[:120]}
                 for t in tasks],
    })
    hist["weeks"] = weeks[-12:]          # a quarter of history is plenty
    # NOT published. gh-pages is public even when the repo is private, and
    # this file holds open task titles. It is written locally only until the
    # snapshot moves to the private OneDrive knowledge store; in CI the file
    # is discarded with the runner, so "what slipped" has no baseline yet.
    Path(HISTORY_FILE).write_text(
        json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   .  snapshot written locally, not published ({len(tasks)} open "
          f"tasks, {len(hist['weeks'])} weeks retained)")


def _previous_week(hist: dict, now: datetime.datetime):
    """The most recent snapshot at least four days old."""
    cutoff = now.date() - datetime.timedelta(days=4)
    prior = [w for w in hist.get("weeks", [])
             if w.get("date") and datetime.date.fromisoformat(w["date"]) <= cutoff]
    return prior[-1] if prior else None


# ── 1. what slipped ──────────────────────────────────────────────────────────

def what_slipped(tasks: list, ranked: dict, hist: dict,
                 now: datetime.datetime, limit: int = 8) -> list:
    """
    Open, still-urgent, and not moving. Carried over from a previous snapshot
    where one exists; otherwise judged on how long since To Do last saw it
    touched, which is the same question asked with weaker evidence.
    """
    prev = _previous_week(hist, now)
    prev_ids = {t.get("id") for t in (prev or {}).get("open", [])} if prev else set()
    today = now.date()

    out = []
    for t in ranked.get("live", []) + ranked.get("backlog", []):
        modified = t.get("last_modified")
        untouched = None
        if modified:
            try:
                untouched = (today - modified).days
            except Exception:
                untouched = None

        carried = t.get("id") in prev_ids
        # Stuck means: it still scores as urgent, and either it survived a
        # previous snapshot or nothing has touched it in a working week.
        if not (carried or (untouched is not None and untouched >= 7)):
            continue
        if t.get("urgency_score", 0) <= 0 and not carried:
            continue

        out.append({
            "title":     t.get("title", ""),
            "reason":    t.get("urgency_reason", ""),
            "days_over": t.get("days_over", 0) or 0,
            "untouched": untouched,
            "carried":   carried,
            "score":     t.get("urgency_score", 0),
        })

    out.sort(key=lambda x: (-int(x["carried"]), -x["score"], -(x["untouched"] or 0)))
    return out[:limit], bool(prev), (prev or {}).get("date")


# ── 2. next week's shape ─────────────────────────────────────────────────────

def _overlaps(a: dict, b: dict) -> bool:
    return a["start_dt"] < b["end_dt"] and b["start_dt"] < a["end_dt"]


def next_week_shape(fortnight: dict, tasks: list, now: datetime.datetime) -> dict:
    """Load per day, clashes, and external meetings with nothing behind them."""
    monday = (now.date() + datetime.timedelta(days=(7 - now.weekday())))
    friday = monday + datetime.timedelta(days=4)
    events = [e for e in (fortnight or {}).get("events", [])
              if monday <= e["start_dt"].date() <= friday]
    events.sort(key=lambda e: e["start_dt"])

    days = []
    for i in range(5):
        d = monday + datetime.timedelta(days=i)
        evs = [e for e in events if e["start_dt"].date() == d]
        mins = sum(e.get("in_hours_mins", 0) or 0 for e in evs
                   if e.get("counts_capacity"))
        days.append({"date": d, "count": len(evs), "hours": round(mins / 60, 1),
                     "events": evs})

    clashes = []
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if b["start_dt"] >= a["end_dt"]:
                break
            if _overlaps(a, b) and not (a.get("is_all_day") or b.get("is_all_day")):
                clashes.append((a, b))

    # An external meeting with no open task naming it is one nobody has
    # prepared for - the single most useful thing to notice on a Friday.
    stop = {"the", "and", "with", "for", "meeting", "call", "catch", "up", "re"}
    task_words = set()
    for t in tasks:
        blob = f"{t.get('title','')} {t.get('detail','')}".lower()
        task_words |= {w for w in re.findall(r"[a-z0-9']{4,}", blob) if w not in stop}

    unprepared = []
    for e in events:
        if e.get("is_all_day") or e.get("source") == "personal":
            continue
        emails = [a for a in (e.get("attendee_emails") or []) if a]
        external = [a for a in emails if INTERNAL not in a.lower()]
        if not external:
            continue
        subject_words = {w for w in re.findall(r"[a-z0-9']{4,}",
                                               (e.get("subject") or "").lower())
                         if w not in stop}
        if subject_words and subject_words & task_words:
            continue
        unprepared.append(e)

    return {"monday": monday, "friday": friday, "days": days,
            "events": events, "clashes": clashes[:6],
            "unprepared": unprepared[:6],
            "total_hours": round(sum(d["hours"] for d in days), 1)}


# ── 3. deadline radar ────────────────────────────────────────────────────────

_MONTHS = ("jan feb mar apr may jun jul aug sep oct nov dec").split()
_DATE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")[a-z]*\b", re.I)
STATUTORY = ["lodge", "lodgement", "relinquish", "renewal", "accc", "aemo",
             "gsoo", "statutory", "compliance", "expiry", "expires",
             "annual report", "agm", "tenure", "application", "submission",
             "audit", "royalty", "native title", "epbc"]


def _parse_date(text: str, now: datetime.datetime):
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    day, mon = int(m.group(1)), _MONTHS.index(m.group(2).lower()[:3]) + 1
    for year in (now.year, now.year + 1):
        try:
            d = datetime.date(year, mon, day)
        except ValueError:
            continue
        if d >= now.date():
            return d
    return None


def deadline_radar(tasks: list, fortnight: dict,
                   now: datetime.datetime, horizon: int = 30) -> list:
    limit = now.date() + datetime.timedelta(days=horizon)
    found = []

    for t in tasks:
        blob = f"{t.get('title','')} {t.get('detail','')}"
        low = blob.lower()
        hits = [k for k in STATUTORY if k in low]
        when = _parse_date(blob, now)
        due = t.get("due")
        if not when and due:
            try:
                when = due if isinstance(due, datetime.date) else None
            except Exception:
                when = None
        if when and now.date() <= when <= limit:
            found.append({"when": when, "what": t.get("title", ""),
                          "why": (hits[0] if hits else "dated in the task"),
                          "src": "task"})
        elif hits and not when:
            found.append({"when": None, "what": t.get("title", ""),
                          "why": hits[0], "src": "task"})

    for e in (fortnight or {}).get("events", []):
        subject = e.get("subject", "") or ""
        low = subject.lower()
        hits = [k for k in STATUTORY if k in low]
        if hits and now.date() <= e["start_dt"].date() <= limit:
            found.append({"when": e["start_dt"].date(), "what": subject,
                          "why": hits[0], "src": "calendar"})

    seen, out = set(), []
    for f in sorted(found, key=lambda x: (x["when"] is None,
                                          x["when"] or datetime.date.max)):
        key = (f["what"] or "").lower()[:60]
        if key and key not in seen:
            seen.add(key)
            out.append(f)
    return out[:10]


# ── synthesis ────────────────────────────────────────────────────────────────

def synthesise(slipped, shape, radar, api_key: str) -> str:
    """Two or three sentences, grounded only in the blocks below it."""
    if not (_ANTHROPIC_AVAILABLE and api_key):
        return ""
    facts = {
        "stuck_tasks": [s["title"] for s in slipped][:8],
        "next_week_hours": shape["total_hours"],
        "next_week_meetings": len(shape["events"]),
        "busiest_day": max(shape["days"], key=lambda d: d["hours"])["date"].strftime("%A")
                       if shape["days"] else "",
        "clashes": len(shape["clashes"]),
        "unprepared": [e.get("subject", "") for e in shape["unprepared"]][:5],
        "deadlines": [f"{d['what']} ({d['when']})" for d in radar if d["when"]][:6],
    }
    prompt = (
        "You are writing the opening paragraph of a weekly wrap for the Managing "
        "Director of State Gas, a Queensland gas explorer. Below is everything "
        "known about the week ahead and what is outstanding.\n\n"
        + json.dumps(facts, indent=2, default=str)
        + "\n\nWrite two or three sentences, plain prose, no bullet points, no "
          "heading. Say what the week ahead demands and what is at risk of not "
          "happening. Use only the facts above - invent nothing, and do not "
          "restate the numbers mechanically. If nothing is at risk, say so "
          "plainly rather than manufacturing concern."
    )
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=400,
            messages=[{"role": "user", "content": prompt}], timeout=60)
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"   !  synthesis skipped ({e})")
        return ""


# ── compose ──────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    import html
    return html.escape(str(s or ""))


def build_html(intro, slipped, had_prev, prev_date, shape, radar, now) -> str:
    def h2(text, note=""):
        sub = (f'<span style="font-size:0.7rem;font-weight:400;color:{MUTED};'
               f'margin-left:0.5rem">{_esc(note)}</span>') if note else ""
        return (f'<h2 style="font-size:0.95rem;margin:1.6rem 0 0.6rem;'
                f'padding-bottom:0.35rem;border-bottom:2px solid {INK}">'
                f'{text}{sub}</h2>')

    # 1 — what slipped
    if slipped:
        rows = ""
        for s in slipped:
            age = ("carried over from " + prev_date if s["carried"]
                   else (f"untouched {s['untouched']} days" if s["untouched"]
                         else "no recent activity"))
            rows += (
                f'<div style="padding:0.5rem 0;border-bottom:1px dotted {RULE}">'
                f'<div style="font-weight:700;font-size:0.88rem">{_esc(s["title"])}</div>'
                f'<div style="font-size:0.78rem;color:#3d3a34;margin-top:0.12rem">'
                f'{_esc(s["reason"])}</div>'
                f'<div style="font-size:0.7rem;color:{MUTED};margin-top:0.12rem">'
                f'{_esc(age)}</div></div>')
        note = "" if had_prev else "first run — no prior week to compare against yet"
        slipped_html = h2("What slipped", note) + rows
    else:
        slipped_html = h2("What slipped") + (
            f'<p style="font-size:0.85rem;color:{MUTED}">Nothing urgent is sitting '
            f'still. Every task carrying an urgency signal has moved this week.</p>')

    # 2 — next week
    bars = ""
    peak = max([d["hours"] for d in shape["days"]] + [1])
    for d in shape["days"]:
        w = int(100 * d["hours"] / peak) if peak else 0
        col = RED if d["hours"] >= 6 else (AMBER if d["hours"] >= 4 else GREEN)
        bars += (
            f'<tr><td style="padding:3px 8px 3px 0;font-size:0.8rem;width:90px">'
            f'{d["date"].strftime("%a " + DAYFMT + " %b")}</td>'
            f'<td style="padding:3px 0;width:100%">'
            f'<div style="background:{col};height:9px;width:{w}%;border-radius:2px;'
            f'min-width:2px;display:inline-block"></div></td>'
            f'<td style="padding:3px 0 3px 8px;font-size:0.76rem;color:{MUTED};'
            f'white-space:nowrap">{d["hours"]}h &middot; {d["count"]}</td></tr>')

    clash_html = ""
    for a, b in shape["clashes"]:
        clash_html += (
            f'<div style="font-size:0.8rem;padding:0.3rem 0;color:{RED}">'
            f'<strong>{a["start_dt"].strftime("%a %I:%M%p").replace(" 0", " ")}</strong> '
            f'{_esc(a.get("subject"))} &nbsp;vs&nbsp; {_esc(b.get("subject"))}</div>')

    unprep_html = ""
    for e in shape["unprepared"]:
        unprep_html += (
            f'<div style="font-size:0.8rem;padding:0.3rem 0">'
            f'<strong>{e["start_dt"].strftime("%a " + DAYFMT + " %b")}</strong> '
            f'{_esc(e.get("subject"))}'
            f'<span style="color:{MUTED}"> &middot; '
            f'{e.get("attendee_count", 0)} attendees, external</span></div>')

    week_html = (
        h2("Next week",
           shape["monday"].strftime("%b " + DAYFMT) + " – "
           + shape["friday"].strftime("%b " + DAYFMT))
        + f'<table style="border-collapse:collapse;width:100%">{bars}</table>'
        + (f'<div style="margin-top:0.7rem;font-size:0.78rem;font-weight:700">'
           f'Clashes</div>{clash_html}' if clash_html else "")
        + (f'<div style="margin-top:0.7rem;font-size:0.78rem;font-weight:700">'
           f'External meetings with no preparation behind them</div>{unprep_html}'
           if unprep_html else
           f'<div style="margin-top:0.7rem;font-size:0.8rem;color:{MUTED}">'
           f'Every external meeting has related work on the list.</div>')
    )

    # 3 — deadlines
    if radar:
        rows = ""
        for d in radar:
            when = d["when"].strftime("%a " + DAYFMT + " %b") if d["when"] else "no date found"
            days_out = (d["when"] - now.date()).days if d["when"] else None
            col = RED if (days_out is not None and days_out <= 7) else INK
            rows += (
                f'<div style="padding:0.4rem 0;border-bottom:1px dotted {RULE}">'
                f'<span style="font-weight:700;font-size:0.8rem;color:{col}">{when}</span>'
                f'<span style="font-size:0.86rem;margin-left:0.5rem">{_esc(d["what"])}</span>'
                f'<div style="font-size:0.7rem;color:{MUTED}">{_esc(d["why"])}'
                f' &middot; from your {d["src"]}</div></div>')
        radar_html = h2("Deadline radar", "next 30 days") + rows
    else:
        radar_html = h2("Deadline radar", "next 30 days") + (
            f'<p style="font-size:0.85rem;color:{MUTED}">No statutory or lodgement '
            f'dates found in the next 30 days. This reads your tasks and calendar '
            f'only &mdash; it is not a substitute for the compliance calendar.</p>')

    intro_html = (f'<p style="font-size:0.95rem;line-height:1.55;margin:0 0 0.4rem">'
                  f'{_esc(intro)}</p>') if intro else ""

    week_label = now.strftime(f"week ending %A {DAYFMT} %B %Y")
    return f"""<!DOCTYPE html><html><body style="font-family:'Segoe UI',Arial,sans-serif;
background:{PAPER};margin:0;padding:1.5rem 1rem;color:{INK}">
<div style="max-width:680px;margin:0 auto;background:#fff;padding:1.4rem 1.6rem 2rem;
border:1px solid {RULE};border-radius:6px">
<h1 style="font-family:Georgia,serif;font-size:1.3rem;margin:0 0 0.15rem">Friday Wrap</h1>
<div style="font-size:0.75rem;color:{MUTED};margin-bottom:1.1rem">{week_label}</div>
{intro_html}
{slipped_html}
{week_html}
{radar_html}
<p style="color:{MUTED};font-size:0.7rem;margin-top:2rem;border-top:1px solid {RULE};
padding-top:0.6rem">weekly_wrap.py &middot; {now.strftime('%H:%M AEST')} &middot;
reads your To Do list, Outlook calendar and a rolling weekly snapshot.</p>
</div></body></html>"""


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    now = datetime.datetime.now(AEST_OFFSET)
    dry = "--dry" in sys.argv
    print("Friday Wrap — " + now.strftime(f"%A {DAYFMT} %B %Y"))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    print("\n  tasks…")
    tasks, ranked = [], {"live": [], "backlog": []}
    try:
        import todo_tasks
        tasks = todo_tasks.fetch_todo_tasks().get("tasks", [])
        print(f"   OK {len(tasks)} open task(s)")
    except Exception as e:
        print(f"   !  tasks unavailable: {e}")

    print("  calendar…")
    fortnight = {}
    try:
        import calendar_data
        fortnight = calendar_data.fetch_fortnight(14)
        for err in fortnight.get("errors", []):
            print(f"   .  {err}")
        print(f"   OK {len(fortnight.get('events', []))} event(s)")
    except Exception as e:
        print(f"   !  calendar unavailable: {e}")

    if tasks:
        try:
            import task_urgency
            ranked = task_urgency.rank_tasks(tasks, fortnight.get("events", []),
                                             now.date())
        except Exception as e:
            print(f"   !  ranking unavailable: {e}")

    hist = load_task_history()
    slipped, had_prev, prev_date = what_slipped(tasks, ranked, hist, now)
    shape = next_week_shape(fortnight, tasks, now)
    radar = deadline_radar(tasks, fortnight, now)
    print(f"   OK {len(slipped)} slipped, {len(shape['events'])} events next week, "
          f"{len(radar)} deadline(s)")

    intro = synthesise(slipped, shape, radar, api_key)
    html  = build_html(intro, slipped, had_prev, prev_date, shape, radar, now)

    save_task_history(hist, tasks, now)

    recipients = [a.strip() for a in (
        os.environ.get("BRIEFING_EMAIL_TO", ""),
        os.environ.get("BRIEFING_EMAIL_GMAIL", ""),
    ) if a.strip()]

    if dry or not recipients:
        Path("friday_wrap.html").write_text(html, encoding="utf-8")
        why = "--dry" if dry else "no BRIEFING_EMAIL_TO set"
        print(f"\n  saved to friday_wrap.html ({why}) — nothing sent")
        return 0

    token = _outlook.get_access_token()
    message = {
        "subject":      "Friday Wrap — " + now.strftime(f"{DAYFMT} %B %Y"),
        "body":         {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
    }
    resp = requests.post(
        f"{GRAPH_BASE}/me/sendMail",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"message": message, "saveToSentItems": "false"}, timeout=30)
    resp.raise_for_status()
    print(f"\n  sent to: {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
