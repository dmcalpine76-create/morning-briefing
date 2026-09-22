"""
task_urgency.py
---------------
Works out which tasks actually matter, because the obvious signals don't.

On a real Daily Priorities list, 62 of 70 open tasks read as "overdue" and
nearly all are importance=normal. That is an artifact: the inbox extractor
stamps due = today when it creates a task, so the due date records when the
task was CAPTURED, not when it is owed. Sorting by it sorts by age, and the
importance flag has never been set, so it carries nothing.

So urgency is derived from signals that do exist:

  1. calendar coupling  — the task names something on the next fortnight's
                          calendar or in the Deadlines lane. A renewal that
                          lines up with a lodgement date is genuinely urgent
                          in a way a three-week-old follow-up is not.
  2. explicit deadline  — a real date or "by Friday" sitting in the task text
                          that the due field never captured.
  3. statutory gravity  — lodge, relinquish, ACCC, AEMO and friends have
                          external consequences that internal follow-ups don't.
  4. recency            — a task edited this week is live; one untouched since
                          1 September is probably dead or superseded.

Scoring is deterministic, so the same list ranks the same way on the 5:17 and
8:15 runs. Claude is optional: it reorders the shortlist and writes the
one-line reasons, and if it is unavailable the deterministic order and
generated reasons stand.

Self-test:
    py task_urgency.py --test
"""

import re
import json
import datetime

AEST_OFFSET = datetime.timezone(datetime.timedelta(hours=10))

SHORTLIST_SIZE = 8
CANDIDATE_POOL = 20

W_COUPLING_EXACT = 55   # a shared tenement/matter identifier — near-certain
W_COUPLING_FUZZY = 28   # merely shared words — suggestive, not proof
W_DEADLINE  = 30
W_STATUTORY = 20
W_RECENCY   = 15
W_IMPORTANT = 10

STATUTORY = [
    "lodge", "lodgement", "relinquish", "renewal", "accc", "aemo", "gsoo",
    "statutory", "compliance", "expiry", "expires", "annual report", "agm",
    "tenure", "s.95", "application", "submission", "audit",
]

_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has", "was",
    "are", "his", "her", "their", "our", "your", "you", "will", "would",
    "should", "could", "about", "into", "onto", "over", "under", "respond",
    "review", "confirm", "follow", "send", "provide", "complete", "prepare",
    "receive", "decide", "monitor", "read", "check", "update", "meeting",
    "draft", "reschedule", "liaise", "return", "post", "approved",
}

# "by Friday", "before 31 October", "due 5 Nov", "COB Thursday", "end of month"
_DEADLINE_PATTERNS = [
    r"\bby\s+(mon|tue|wed|thu|fri|sat|sun)\w*\b",
    r"\bby\s+\d{1,2}(st|nd|rd|th)?\s+\w+\b",
    r"\bbefore\s+\d{1,2}(st|nd|rd|th)?\s+\w+\b",
    r"\bdue\s+\d{1,2}(st|nd|rd|th)?\s+\w+\b",
    r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b",
    r"\b(cob|eod|eom|end of (the )?(month|week|quarter))\b",
    r"\bdeadline\b",
    r"\bno later than\b",
]
_DEADLINE_RE = re.compile("|".join(_DEADLINE_PATTERNS), re.I)

# Distinctive identifiers: ATP 2062, PL 231, EA00025, s.95
_CODE_RE = re.compile(r"\b(atp|pl|pca|ea|epq)\s?\d{2,6}\b|\bs\.\d{2,3}\b", re.I)


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9.']+", (text or "").lower())
    return {w for w in words if len(w) > 3 and w not in _STOP}


def _codes(text: str) -> set:
    return {m.group(0).lower().replace(" ", "") for m in _CODE_RE.finditer(text or "")}


def _calendar_coupling(task: dict, events: list) -> tuple:
    """Returns (score, reason) — a shared identifier beats mere word overlap."""
    blob = f"{task.get('title','')} {task.get('detail','')}"
    t_codes, t_tokens = _codes(blob), _tokens(blob)
    best = (0, "")

    for ev in events or []:
        subject = ev.get("subject", "") or ""
        e_codes = _codes(subject)
        shared_codes = t_codes & e_codes
        if shared_codes:
            when = ev.get("start_dt")
            day = when.strftime("%a %d %b") if hasattr(when, "strftime") else "soon"
            return (W_COUPLING_EXACT, f"{sorted(shared_codes)[0].upper()} is on the calendar {day}")

        overlap = t_tokens & _tokens(subject)
        if len(overlap) >= 2:
            when = ev.get("start_dt")
            day = when.strftime("%a %d %b") if hasattr(when, "strftime") else "soon"
            score = W_COUPLING_FUZZY
            if score > best[0]:
                best = (score, f"relates to “{subject[:38]}” on {day}")
    return best


def score_task(task: dict, events: list, today: datetime.date) -> dict:
    blob = f"{task.get('title','')} {task.get('detail','')}"
    score, reasons = 0, []

    coupling_score, coupling_reason = _calendar_coupling(task, events)
    if coupling_score:
        score += coupling_score
        reasons.append(coupling_reason)

    if _DEADLINE_RE.search(blob):
        score += W_DEADLINE
        hit = _DEADLINE_RE.search(blob).group(0).strip()
        reasons.append(f"names a deadline (“{hit}”)")

    hits = [k for k in STATUTORY if k in blob.lower()]
    if hits:
        score += W_STATUTORY
        reasons.append(f"statutory or external obligation ({hits[0]})")

    modified = task.get("last_modified")
    if modified:
        try:
            age = (today - modified).days
            if age <= 7:
                score += W_RECENCY
                reasons.append("touched in the last week")
        except Exception:
            pass

    if task.get("importance") == "high":
        score += W_IMPORTANT
        reasons.append("flagged high in To Do")

    days_over = task.get("days_over", 0) or 0
    score += min(days_over, 30) * 0.1        # gentle tiebreak, never a driver

    return {**task, "urgency_score": round(score, 1),
            "urgency_reasons": reasons,
            "urgency_reason": reasons[0] if reasons else "no urgency signal found"}


def rank_tasks(tasks: list, events: list = None, today: datetime.date = None,
               shortlist: int = SHORTLIST_SIZE) -> dict:
    """
    Returns {"live": [...], "backlog": [...]}

    live    — the ranked shortlist for the Schedule tab
    backlog — everything else, newest first, for the Backlog tab
    """
    today = today or datetime.datetime.now(AEST_OFFSET).date()
    scored = [score_task(t, events or [], today) for t in tasks or []]
    scored.sort(key=lambda t: (-t["urgency_score"], t.get("days_over", 0)))

    live = [t for t in scored if t["urgency_score"] > 0][:shortlist]
    live_ids = {t.get("id") for t in live}
    backlog = [t for t in scored if t.get("id") not in live_ids]
    backlog.sort(key=lambda t: t.get("days_over", 0))
    return {"live": live, "backlog": backlog}


def refine_with_claude(client, live: list, events: list) -> list:
    """
    Optional. Reorders the shortlist and rewrites the reasons in plain language.
    Any failure returns the deterministic list untouched — this must never be
    load-bearing.
    """
    if not client or not live:
        return live
    lines = "\n".join(
        f"[{i}] {t['title']} | signals: {'; '.join(t['urgency_reasons']) or 'none'}"
        for i, t in enumerate(live)
    )
    cal = "\n".join(
        f"- {e.get('subject','')} on {e['start_dt'].strftime('%a %d %b')}"
        for e in (events or [])[:20] if hasattr(e.get("start_dt"), "strftime")
    ) or "no calendar context"

    prompt = f"""You are ordering a managing director's shortlist for today.

CALENDAR, NEXT FORTNIGHT:
{cal}

CANDIDATE TASKS (index | title | signals already detected):
{lines}

Return ONLY a JSON array, most urgent first, one object per task:
  "index":  the [n] above
  "reason": under 12 words, why it is urgent TODAY, grounded only in the
            signals and calendar above. Never invent a deadline.

Do not add or drop tasks. No markdown fences."""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
            timeout=60,
        )
        raw = msg.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        order = json.loads(raw)
        out, seen = [], set()
        for item in order:
            i = item.get("index")
            if isinstance(i, int) and 0 <= i < len(live) and i not in seen:
                seen.add(i)
                t = dict(live[i])
                if item.get("reason"):
                    t["urgency_reason"] = str(item["reason"])[:90]
                out.append(t)
        for i, t in enumerate(live):
            if i not in seen:
                out.append(t)
        return out
    except Exception as e:
        print(f"  WARNING: urgency refinement skipped ({e})")
        return live


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test():
    today = datetime.date(2026, 9, 23)
    AEST = AEST_OFFSET
    ev = lambda s, day: {"subject": s, "start_dt": datetime.datetime(2026, 9, day, 9, 0, tzinfo=AEST)}

    events = [
        ev("ATP 2062 renewal lodgement", 25),
        ev("Board meeting", 30),
        ev("Annual Report sign-off", 29),
        ev("Ops stand-up", 24),
    ]

    tasks = [
        {"id": "1", "title": "Lodge ATP 2062 renewal application",
         "detail": "", "importance": "normal", "days_over": 21,
         "last_modified": datetime.date(2026, 9, 21)},
        {"id": "2", "title": "Review and approve draft 2026 Annual Report",
         "detail": "Needs comments by Friday", "importance": "normal",
         "days_over": 20, "last_modified": datetime.date(2026, 9, 10)},
        {"id": "3", "title": "Follow up Warwick Squire re introduction",
         "detail": "", "importance": "normal", "days_over": 36,
         "last_modified": datetime.date(2026, 8, 18)},
        {"id": "4", "title": "Complete and return AEMO 2027 GSOO survey",
         "detail": "", "importance": "normal", "days_over": 22,
         "last_modified": datetime.date(2026, 9, 1)},
        {"id": "5", "title": "Post approved LinkedIn article on gas policy",
         "detail": "", "importance": "normal", "days_over": 22,
         "last_modified": datetime.date(2026, 9, 1)},
        {"id": "6", "title": "Submit ATP 2062 partial relinquishment",
         "detail": "", "importance": "normal", "days_over": 23,
         "last_modified": datetime.date(2026, 9, 2)},
    ]

    print("Task urgency self-test")
    print("=" * 74)
    result = rank_tasks(tasks, events, today, shortlist=4)

    print(f"{'score':>6}  {'title':<44} reason")
    print("-" * 96)
    for t in result["live"]:
        print(f"{t['urgency_score']:>6}  {t['title'][:44]:<44} {t['urgency_reason']}")
    print()
    print("backlog (no urgency signal), oldest last:")
    for t in result["backlog"]:
        print(f"        {t['title'][:44]:<44} {t['days_over']}d old")
    print()

    live_ids = [t["id"] for t in result["live"]]
    checks = [
        ("ATP renewal ranks first (code matches a calendar deadline)", live_ids[0] == "1"),
        ("LinkedIn post is NOT in the live list", "5" not in live_ids),
        ("Warwick follow-up is NOT in the live list", "3" not in live_ids),
        ("Annual Report is live (explicit 'by Friday' + calendar)", "2" in live_ids),
        ("age alone never promotes: 36d Warwick below 21d ATP",
         "3" not in live_ids or live_ids.index("3") > live_ids.index("1")),
    ]
    passed = 0
    for label, ok in checks:
        print(("ok   " if ok else "FAIL ") + label)
        passed += ok
    print()
    print(f"{passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import sys
    sys.exit(_self_test())
