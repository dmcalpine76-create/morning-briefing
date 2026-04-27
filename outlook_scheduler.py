"""
outlook_scheduler.py  —  AI-powered diary scheduling engine
------------------------------------------------------------
Reads tasks from Microsoft To Do, finds free slots in Outlook calendar,
and uses Claude to schedule tasks according to the rules in scheduling_rules.json.

Integrated into briefing.py — also callable standalone:
  py outlook_scheduler.py run      — schedule now, create calendar events
  py outlook_scheduler.py preview  — show proposed schedule, don't create events
  py outlook_scheduler.py flagged  — show yesterday's unfinished blocks

Requires: outlook_email.py (for Graph API auth), scheduling_rules.json
"""

import os
import json
import datetime
import requests
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import outlook_email as _outlook
    _OUTLOOK_AVAILABLE = True
except ImportError:
    _OUTLOOK_AVAILABLE = False

RULES_FILE   = Path(__file__).parent / "scheduling_rules.json"
PLAN_FILE    = Path(__file__).parent / "scheduling_plan.json"
GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
AEST_OFFSET  = datetime.timezone(datetime.timedelta(hours=10))
BLOCK_TAG    = "🎯"          # used to identify scheduler-created events
REQUEST_TIMEOUT = 15


# ── AUTH ─────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    if not _OUTLOOK_AVAILABLE:
        raise RuntimeError("outlook_email.py not found")
    return _outlook.get_access_token()


def _graph_get(token: str, path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _graph_post(token: str, path: str, body: dict) -> dict:
    resp = requests.post(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _graph_patch(token: str, path: str, body: dict) -> dict:
    resp = requests.patch(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── RULES ─────────────────────────────────────────────────────────────────────

def load_rules() -> dict:
    """Load scheduling_rules.json. Returns empty dict if not found."""
    if not RULES_FILE.exists():
        return {}
    try:
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️  Could not load scheduling_rules.json: {e}")
        return {}


def get_day_rules(rules: dict, dt: datetime.datetime) -> dict:
    """
    Return the rules for a specific date, accounting for the fortnightly cycle.
    Week A / Week B is determined by ISO week number (even = A, odd = B).
    Returns a day dict with: enabled, start_time, end_time, notes.
    """
    day_key = dt.strftime("%a").lower()  # mon, tue, etc.
    iso_week = dt.isocalendar()[1]
    week_key = "a" if iso_week % 2 == 0 else "b"

    weeks = rules.get("weeks", {})
    week  = weeks.get(week_key, {})
    day   = week.get(day_key, {})

    global_rules = rules.get("global", {})

    return {
        "enabled":    day.get("enabled", True),
        "start_time": day.get("start_time", global_rules.get("global_start", "09:00")),
        "end_time":   day.get("end_time",   global_rules.get("global_end",   "16:00")),
        "notes":      day.get("notes", ""),
        "week":       week_key.upper(),
        "day_key":    day_key,
    }


# ── FETCH TASKS FROM TO DO ────────────────────────────────────────────────────

def fetch_todo_tasks(token: str, rules: dict) -> list[dict]:
    """
    Fetch tasks from:
      1. The 'Daily Priorities' list
      2. Any other list with overdue tasks (due date < today)
    Returns list of task dicts with: id, title, body, due_date, priority, list_name, is_overdue
    """
    today = datetime.date.today()
    tasks = []
    seen_ids = set()

    try:
        lists_data = _graph_get(token, "/me/todo/lists")
        all_lists  = lists_data.get("value", [])
    except Exception as e:
        print(f"  ⚠️  Could not fetch To Do lists: {e}")
        return []

    target_list_name = "Daily Priorities"

    for lst in all_lists:
        list_id   = lst["id"]
        list_name = lst.get("displayName", "")
        is_priority = list_name.lower() == target_list_name.lower()

        # Only process Daily Priorities list, or any list for overdue check
        try:
            tasks_data = _graph_get(
                token,
                f"/me/todo/lists/{list_id}/tasks",
                {"$filter": "status ne 'completed'", "$top": 100}
            )
        except Exception:
            continue

        for t in tasks_data.get("value", []):
            tid = t.get("id", "")
            if tid in seen_ids:
                continue

            # Parse due date
            due_raw = t.get("dueDateTime", {})
            due_date = None
            if due_raw and due_raw.get("dateTime"):
                try:
                    due_date = datetime.date.fromisoformat(
                        due_raw["dateTime"][:10]
                    )
                except Exception:
                    pass

            is_overdue = due_date and due_date < today

            # Include if: in Daily Priorities list, OR overdue in any list
            if not is_priority and not is_overdue:
                continue

            seen_ids.add(tid)

            # Get body text
            body_content = t.get("body", {}).get("content", "").strip()
            # Strip HTML tags if present
            body_clean = re.sub(r"<[^>]+>", " ", body_content).strip()

            importance = t.get("importance", "normal")
            priority   = "high" if importance == "high" else (
                         "urgent" if is_overdue else "normal")

            tasks.append({
                "id":         tid,
                "title":      t.get("title", ""),
                "body":       body_clean[:400] if body_clean else "",
                "due_date":   due_date.isoformat() if due_date else None,
                "priority":   priority,
                "list_name":  list_name,
                "is_overdue": is_overdue,
                "list_id":    list_id,
                "raw":        t,
            })

    # Sort: overdue first, then by due date, then by importance
    tasks.sort(key=lambda t: (
        0 if t["is_overdue"] else 1,
        t["due_date"] or "9999-99-99",
        0 if t["priority"] == "urgent" else (1 if t["priority"] == "high" else 2)
    ))

    return tasks


# ── FETCH CALENDAR EVENTS ─────────────────────────────────────────────────────

def fetch_upcoming_events(token: str, days: int = 7) -> list[dict]:
    """
    Fetch calendar events for the next `days` days (and yesterday for flagging).
    Returns list of event dicts with: subject, start_dt, end_dt, is_scheduler_block
    """
    now_aest    = datetime.datetime.now(AEST_OFFSET)
    yest_start  = (now_aest - datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    window_end  = now_aest.replace(
        hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days)

    start_utc = yest_start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = window_end.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/calendarView",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Prefer": 'outlook.timezone="AUS Eastern Standard Time"',
            },
            params={
                "startDateTime": start_utc,
                "endDateTime":   end_utc,
                "$select": "subject,start,end,isAllDay,isCancelled,showAs,body",
                "$orderby": "start/dateTime",
                "$top": 200,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️  Could not fetch calendar events: {e}")
        return []

    events = []
    for ev in data.get("value", []):
        if ev.get("isCancelled"):
            continue
        if ev.get("isAllDay"):
            continue

        raw_start = ev["start"].get("dateTime", "")
        raw_end   = ev["end"].get("dateTime", "")

        def _parse(s):
            s = s.split(".")[0]
            if s.endswith("Z"):
                return datetime.datetime.fromisoformat(
                    s[:-1] + "+00:00").astimezone(AEST_OFFSET)
            elif "+" in s[10:] or s.count("-") > 2:
                return datetime.datetime.fromisoformat(s).astimezone(AEST_OFFSET)
            else:
                return datetime.datetime.fromisoformat(s).replace(tzinfo=AEST_OFFSET)

        try:
            start_dt = _parse(raw_start)
            end_dt   = _parse(raw_end)
        except Exception:
            continue

        subject = ev.get("subject", "")
        is_scheduler = subject.startswith(BLOCK_TAG)

        events.append({
            "subject":          subject,
            "start_dt":         start_dt,
            "end_dt":           end_dt,
            "is_scheduler_block": is_scheduler,
            "event_id":         ev.get("id", ""),
        })

    return events


# ── FIND FREE SLOTS ───────────────────────────────────────────────────────────

def find_free_slots(events: list[dict], rules: dict, days_ahead: int = 5) -> list[dict]:
    """
    For each schedulable day in the next days_ahead days, find free time slots
    respecting the rules (working hours, deep work window, buffers, protected times).

    Returns list of slot dicts:
      { date, start_dt, end_dt, duration_mins, slot_type, week }
      slot_type: 'deep_work' | 'afternoon'
    """
    global_rules = rules.get("global", {})
    buffer_mins  = global_rules.get("buffer_after", 15)
    snap_mins    = global_rules.get("snap_to", 30)
    min_block    = global_rules.get("min_block", 30)
    deep_start   = global_rules.get("deep_start", "09:00")
    deep_end     = global_rules.get("deep_end", "12:00")
    protected    = rules.get("protected_times", [])
    work_only    = global_rules.get("work_days_only", True)

    now_aest = datetime.datetime.now(AEST_OFFSET)
    slots    = []

    for day_offset in range(0, days_ahead):
        date = (now_aest + datetime.timedelta(days=day_offset)).date()
        dt   = datetime.datetime.combine(date, datetime.time(0, 0), tzinfo=AEST_OFFSET)

        day_rules = get_day_rules(rules, dt)
        if not day_rules["enabled"]:
            continue

        # Skip weekends for work tasks if configured
        if work_only and dt.weekday() >= 5:
            continue

        # Parse day boundaries
        day_start = _parse_time_on_date(date, day_rules["start_time"])
        day_end   = _parse_time_on_date(date, day_rules["end_time"])
        dw_start  = _parse_time_on_date(date, deep_start)
        dw_end    = _parse_time_on_date(date, deep_end)

        # Don't schedule in the past
        if day_end < now_aest:
            continue
        if day_start < now_aest:
            day_start = _snap_forward(now_aest, snap_mins)

        # Build list of blocked periods for this day
        blocked = []

        # From actual calendar events
        for ev in events:
            if ev["start_dt"].date() != date:
                continue
            if ev["is_scheduler_block"]:
                continue  # don't block on existing scheduler blocks (we'll overwrite)
            buf_end = ev["end_dt"] + datetime.timedelta(minutes=buffer_mins)
            blocked.append((ev["start_dt"], buf_end))

        # From protected times
        day_name = dt.strftime("%a").lower()
        for pt in protected:
            if day_name not in (pt.get("days") or []):
                continue
            if not pt.get("start") or not pt.get("end"):
                continue
            try:
                pt_start = _parse_time_on_date(date, pt["start"])
                pt_end   = _parse_time_on_date(date, pt["end"])
                blocked.append((pt_start, pt_end))
            except Exception:
                continue

        # Sort and merge blocked periods
        blocked.sort(key=lambda x: x[0])
        merged = []
        for b in blocked:
            if merged and b[0] <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b[1]))
            else:
                merged.append(list(b))

        # Find free slots in deep work window and afternoon window
        for window_start, window_end_t, slot_type in [
            (dw_start, dw_end, "deep_work"),
            (dw_end,   day_end, "afternoon"),
        ]:
            cursor = max(window_start, day_start)
            if cursor < now_aest:
                cursor = _snap_forward(now_aest, snap_mins)

            for block_start, block_end in merged + [(window_end_t, window_end_t)]:
                # Free time between cursor and block_start
                free_end = min(block_start, window_end_t)
                if free_end > cursor:
                    # Snap cursor to hour/half-hour
                    snapped = _snap_forward(cursor, snap_mins)
                    if snapped < free_end:
                        duration = int((free_end - snapped).total_seconds() / 60)
                        if duration >= min_block:
                            slots.append({
                                "date":         date.isoformat(),
                                "start_dt":     snapped,
                                "end_dt":       free_end,
                                "duration_mins": duration,
                                "slot_type":    slot_type,
                                "week":         day_rules["week"],
                            })
                cursor = max(cursor, block_end)

    return slots


def _parse_time_on_date(date: datetime.date, time_str: str) -> datetime.datetime:
    h, m = map(int, time_str.split(":"))
    return datetime.datetime.combine(
        date, datetime.time(h, m), tzinfo=AEST_OFFSET)


def _snap_forward(dt: datetime.datetime, snap_mins: int) -> datetime.datetime:
    """Snap datetime forward to next snap_mins boundary."""
    mins = dt.hour * 60 + dt.minute
    snapped = ((mins + snap_mins - 1) // snap_mins) * snap_mins
    return dt.replace(
        hour=snapped // 60, minute=snapped % 60,
        second=0, microsecond=0
    )


# ── AI SCHEDULING ─────────────────────────────────────────────────────────────

def ai_schedule_tasks(
    tasks: list[dict],
    slots: list[dict],
    rules: dict,
    api_key: str,
) -> list[dict]:
    """
    Use Claude to match tasks to slots, estimating durations and respecting rules.

    Returns list of scheduled items:
      { task, slot, estimated_mins, title, description }
    """
    if not _ANTHROPIC_AVAILABLE or not tasks or not slots:
        return []

    global_rules = rules.get("global", {})
    task_types   = rules.get("task_types", [])
    scheduling   = rules.get("scheduling", {})
    cal_blocks   = rules.get("calendar_blocks", {})

    max_hours_day = global_rules.get("max_hours", 3)
    max_blocks    = global_rules.get("max_blocks", 4)
    extra_instr   = scheduling.get("extra_instructions", "")

    # Build task type reference for prompt
    tt_lines = "\n".join(
        f"  - {tt['category']}: keywords={tt['keywords']}, default={tt['duration']}min, "
        f"preferred={tt['time_pref']}, type={tt['type']}"
        for tt in task_types
    )

    # Build task list for prompt
    task_lines = "\n".join(
        f"[{i+1}] {t['title']}"
        + (f" (OVERDUE — due {t['due_date']})" if t['is_overdue'] else
           f" (due {t['due_date']})" if t['due_date'] else "")
        + (f"\n    Priority: {t['priority']}" if t['priority'] != 'normal' else "")
        + (f"\n    Detail: {t['body']}" if t['body'] else "")
        + f"\n    List: {t['list_name']}"
        for i, t in enumerate(tasks[:20])  # cap at 20 tasks
    )

    # Build slot list for prompt
    slot_lines = "\n".join(
        f"[S{i+1}] {s['date']} {s['start_dt'].strftime('%H:%M')}–{s['end_dt'].strftime('%H:%M')} "
        f"({s['duration_mins']}min available, {s['slot_type'].replace('_',' ')}, Week {s['week']})"
        for i, s in enumerate(slots[:40])
    )

    prompt = f"""You are a professional diary scheduler for Doug McAlpine, State Gas, Brisbane (AEST).

SCHEDULING RULES:
- Hard start: 09:00, Hard stop: 16:00 every day
- Deep work window (contracts, documents, financial, regulatory): 09:00–12:00
- Admin/calls/emails window: 12:00–16:00
- Max {max_hours_day} hours of task blocks per day, max {max_blocks} blocks per day
- Snap all tasks to start on the hour or half-hour
- Leave 15 min buffer after each task block
- Overdue tasks must be scheduled TODAY or TOMORROW at latest
- High priority tasks before normal priority
{f'- {extra_instr}' if extra_instr else ''}

TASK TYPE DURATION GUIDE:
{tt_lines}

AVAILABLE SLOTS (use slot index S1, S2 etc):
{slot_lines}

TASKS TO SCHEDULE (use task index 1, 2 etc):
{task_lines}

For each task:
1. Estimate duration in minutes (use task type guide, use your judgement based on description)
2. Pick the best slot (match deep_work tasks to deep work slots, admin to afternoon slots)
3. If a slot is too small for a task, split the task across two slots only if it makes sense
4. Don't exceed the slot's available duration
5. Don't schedule more than {max_hours_day}h total per day across all tasks

Respond ONLY as JSON — no markdown, no preamble:
{{
  "schedule": [
    {{
      "task_index": 1,
      "slot_index": "S1",
      "estimated_mins": 60,
      "reason": "one sentence why this slot fits this task"
    }}
  ],
  "unscheduled": [
    {{
      "task_index": 2,
      "reason": "why it couldn't be scheduled"
    }}
  ]
}}"""

    try:
        client  = _anthropic.Anthropic(api_key=api_key)
        resp    = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
    except Exception as e:
        print(f"  ⚠️  AI scheduling error: {e}")
        return []

    # Map indices back to actual tasks and slots
    prefix  = cal_blocks.get("block_prefix", "🎯 ")
    results = []

    for item in data.get("schedule", []):
        try:
            t_idx = int(item["task_index"]) - 1
            s_key = item["slot_index"]          # e.g. "S3"
            s_idx = int(s_key[1:]) - 1
            est   = int(item["estimated_mins"])
            task  = tasks[t_idx]
            slot  = slots[s_idx]

            # Clamp estimated duration to slot availability
            est = min(est, slot["duration_mins"])
            # Snap to nearest 15 mins
            est = max(15, round(est / 15) * 15)

            end_dt = slot["start_dt"] + datetime.timedelta(minutes=est)

            results.append({
                "task":           task,
                "slot":           slot,
                "estimated_mins": est,
                "start_dt":       slot["start_dt"],
                "end_dt":         end_dt,
                "title":          f"{prefix}{task['title']}",
                "description":    _build_event_body(task, cal_blocks),
                "reason":         item.get("reason", ""),
            })
        except (IndexError, KeyError, ValueError):
            continue

    return results


def _build_event_body(task: dict, cal_blocks: dict) -> str:
    parts = []
    if task.get("body"):
        parts.append(task["body"])
    if task.get("is_overdue"):
        parts.append(f"⚠️ OVERDUE (was due {task['due_date']})")
    elif task.get("due_date"):
        parts.append(f"Due: {task['due_date']}")
    parts.append(f"From To Do list: {task['list_name']}")
    if cal_blocks.get("add_todo_link", True):
        parts.append(f"\nView in To Do: https://to-do.office.com/tasks/id/{task['id']}/details")
    return "\n".join(parts)


# ── CREATE CALENDAR EVENTS ────────────────────────────────────────────────────

def create_calendar_events(token: str, scheduled: list[dict], rules: dict) -> tuple[int, int]:
    """
    Create calendar events in Outlook for each scheduled item.
    Returns (ok_count, fail_count).
    """
    global_rules = rules.get("global", {})
    cal_blocks   = rules.get("calendar_blocks", {})

    show_as     = cal_blocks.get("block_show_as", "free")
    reminder    = cal_blocks.get("block_reminder", 10)
    mark_free   = global_rules.get("mark_as_free", True)
    if mark_free:
        show_as = "free"

    ok, fail = 0, 0

    for item in scheduled:
        try:
            start_str = item["start_dt"].astimezone(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S")
            end_str   = item["end_dt"].astimezone(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S")

            body = {
                "subject": item["title"],
                "body": {
                    "contentType": "text",
                    "content": item["description"],
                },
                "start": {"dateTime": start_str, "timeZone": "UTC"},
                "end":   {"dateTime": end_str,   "timeZone": "UTC"},
                "showAs": show_as,
                "isReminderOn": reminder > 0,
                "reminderMinutesBeforeStart": reminder,
                "categories": ["Scheduled Task"],
            }

            _graph_post(token, "/me/events", body)
            ok += 1
            print(f"   ✓ Created: {item['title'][:60]} @ "
                  f"{item['start_dt'].strftime('%a %d %b %H:%M')}")
        except Exception as e:
            fail += 1
            print(f"   ✗ Failed: {item['task']['title'][:50]}: {e}")

    return ok, fail


# ── FLAG UNFINISHED BLOCKS ────────────────────────────────────────────────────

def get_flagged_blocks(events: list[dict]) -> list[dict]:
    """
    Return yesterday's scheduler-created blocks (potential unfinished tasks).
    """
    yesterday = (datetime.datetime.now(AEST_OFFSET) - datetime.timedelta(days=1)).date()
    return [
        ev for ev in events
        if ev["is_scheduler_block"] and ev["start_dt"].date() == yesterday
    ]


# ── HTML SUMMARY FOR BRIEFING ─────────────────────────────────────────────────

def build_schedule_summary_html(
    scheduled: list[dict],
    unscheduled: list[dict],
    flagged: list[dict],
) -> str:
    """
    Build a compact HTML summary to embed in the morning briefing.
    Shows: today's scheduled blocks, flagged yesterday items, unscheduled tasks.
    """
    if not scheduled and not flagged:
        return ""

    today = datetime.date.today()

    # Today's blocks
    today_items = [s for s in scheduled if s["start_dt"].date() == today]
    future_items = [s for s in scheduled if s["start_dt"].date() > today]

    html_parts = []

    if today_items:
        cards = ""
        for s in today_items:
            t = s["task"]
            time_str = f"{s['start_dt'].strftime('%I:%M %p').lstrip('0')} – {s['end_dt'].strftime('%I:%M %p').lstrip('0')}"
            overdue_badge = '<span style="font-size:0.58rem;font-weight:700;background:#fde8e8;color:#c0392b;padding:0.1rem 0.35rem;border-radius:2px;margin-left:0.35rem">⚠️ OVERDUE</span>' if t.get("is_overdue") else ""
            cards += f"""<div style="padding:0.7rem 1rem;border-bottom:1px solid var(--rule)">
  <div style="font-size:0.65rem;font-weight:700;color:var(--ink-light);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.2rem">{time_str} · {s['estimated_mins']}min</div>
  <div style="font-size:0.85rem;font-weight:700;font-family:var(--font-display)">{t['title']}{overdue_badge}</div>
  {f'<div style="font-size:0.73rem;color:var(--ink-light);margin-top:0.15rem;font-style:italic">{s["reason"]}</div>' if s.get("reason") else ""}
</div>"""
        html_parts.append(f"""<div style="margin-bottom:0.75rem">
<div style="font-size:0.65rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:var(--ink-light);padding:0.5rem 1rem;background:var(--paper-2);border-bottom:1px solid var(--rule)">🎯 Scheduled for today</div>
{cards}</div>""")

    if future_items:
        # Group by date
        by_date = {}
        for s in future_items:
            d = s["start_dt"].strftime("%a %d %b")
            by_date.setdefault(d, []).append(s)
        rows = ""
        for date_str, items in list(by_date.items())[:3]:
            titles = ", ".join(s["task"]["title"][:35] for s in items[:3])
            if len(items) > 3:
                titles += f" +{len(items)-3} more"
            rows += f'<div style="padding:0.45rem 1rem;border-bottom:1px solid var(--rule);font-size:0.8rem"><span style="font-weight:700;min-width:80px;display:inline-block">{date_str}</span><span style="color:var(--ink-light)">{titles}</span></div>'
        html_parts.append(f"""<div style="margin-bottom:0.75rem">
<div style="font-size:0.65rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:var(--ink-light);padding:0.5rem 1rem;background:var(--paper-2);border-bottom:1px solid var(--rule)">📅 Upcoming scheduled tasks</div>
{rows}</div>""")

    if flagged:
        rows = ""
        for ev in flagged:
            rows += f'<div style="padding:0.45rem 1rem;border-bottom:1px solid var(--rule);font-size:0.8rem;display:flex;gap:0.5rem"><span style="color:#d35400">⚑</span><span>{ev["subject"].replace("🎯 ","")}</span><span style="margin-left:auto;font-size:0.7rem;color:var(--ink-light)">{ev["start_dt"].strftime("%H:%M")}</span></div>'
        html_parts.append(f"""<div style="margin-bottom:0.75rem">
<div style="font-size:0.65rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;padding:0.5rem 1rem;background:#fef3e2;border-bottom:1px solid #f5cba7;color:#d35400">⚑ Yesterday's blocks — review and reschedule if needed</div>
{rows}</div>""")

    if unscheduled:
        rows = "".join(
            f'<div style="padding:0.35rem 1rem;font-size:0.78rem;color:var(--ink-light)">• {u["task"]["title"][:60]} — {u.get("reason","no slot available")}</div>'
            for u in unscheduled[:5]
        )
        html_parts.append(f"""<div>
<div style="font-size:0.65rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:var(--ink-light);padding:0.5rem 1rem;background:var(--paper-2);border-bottom:1px solid var(--rule)">⚠️ Could not schedule</div>
{rows}</div>""")

    return f'<div style="border:1px solid var(--rule);border-radius:3px;overflow:hidden">{"".join(html_parts)}</div>'


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def save_plan(result: dict) -> None:
    """Serialise the scheduler result to scheduling_plan.json for the dashboard."""
    import copy

    def _serialise(obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        return str(obj)

    def _clean_scheduled(items):
        out = []
        for s in items:
            task = {k: v for k, v in s["task"].items() if k != "raw"}
            out.append({
                "task":           task,
                "date":           s["start_dt"].date().isoformat(),
                "start_time":     s["start_dt"].strftime("%H:%M"),
                "end_time":       s["end_dt"].strftime("%H:%M"),
                "estimated_mins": s["estimated_mins"],
                "title":          s["title"],
                "description":    s["description"],
                "reason":         s.get("reason", ""),
            })
        return out

    def _clean_flagged(items):
        return [
            {
                "subject":    ev["subject"],
                "start_time": ev["start_dt"].strftime("%H:%M"),
                "date":       ev["start_dt"].date().isoformat(),
            }
            for ev in items
        ]

    def _clean_unscheduled(items):
        return [
            {
                "task":   {k: v for k, v in u["task"].items() if k != "raw"},
                "reason": u.get("reason", "no suitable slot"),
            }
            for u in items
        ]

    plan = {
        "generated_at": datetime.datetime.now(AEST_OFFSET).strftime("%A %#d %B %Y, %H:%M AEST"),
        "scheduled":    _clean_scheduled(result.get("scheduled", [])),
        "unscheduled":  _clean_unscheduled(result.get("unscheduled", [])),
        "flagged":      _clean_flagged(result.get("flagged", [])),
        "error":        result.get("error"),
    }

    PLAN_FILE.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")



def run_scheduler(
    api_key: str,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Full scheduling run. Called from briefing.py or standalone.

    Returns dict with:
      scheduled, unscheduled, flagged, html_summary, ok_count, fail_count
    """
    result = {
        "scheduled":   [],
        "unscheduled": [],
        "flagged":     [],
        "html_summary": "",
        "ok_count":    0,
        "fail_count":  0,
        "error":       None,
    }

    if not _OUTLOOK_AVAILABLE:
        result["error"] = "outlook_email.py not available"
        return result

    rules = load_rules()
    if not rules:
        result["error"] = "scheduling_rules.json not found — run the Scheduling Rules dashboard first"
        return result

    global_rules = rules.get("global", {})
    days_ahead   = global_rules.get("days_ahead", 5)

    try:
        token = _get_token()
    except Exception as e:
        result["error"] = f"Auth error: {e}"
        return result

    if verbose:
        print("\n📋  Fetching To Do tasks…")
    tasks = fetch_todo_tasks(token, rules)
    if verbose:
        print(f"   {len(tasks)} tasks to schedule "
              f"({sum(1 for t in tasks if t['is_overdue'])} overdue)")

    if verbose:
        print("📅  Fetching calendar events…")
    events = fetch_upcoming_events(token, days=days_ahead + 1)
    if verbose:
        print(f"   {len(events)} events found")

    # Flag yesterday's unfinished blocks
    flagged = get_flagged_blocks(events)
    if verbose and flagged:
        print(f"   ⚑  {len(flagged)} unfinished block(s) from yesterday flagged")

    if not tasks:
        if verbose:
            print("   No tasks to schedule — Daily Priorities list is empty")
        result["flagged"]     = flagged
        result["html_summary"] = build_schedule_summary_html([], [], flagged)
        return result

    if verbose:
        print("🧩  Finding free slots…")
    slots = find_free_slots(events, rules, days_ahead=days_ahead)
    if verbose:
        print(f"   {len(slots)} free slots found across {days_ahead} days")

    if not slots:
        result["error"] = "No free slots found in the scheduling window"
        result["flagged"] = flagged
        return result

    if verbose:
        print("🤖  AI scheduling tasks into slots…")
    scheduled = ai_schedule_tasks(tasks, slots, rules, api_key)

    # Identify unscheduled tasks
    scheduled_task_ids = {s["task"]["id"] for s in scheduled}
    unscheduled = [
        {"task": t, "reason": "no suitable slot available"}
        for t in tasks if t["id"] not in scheduled_task_ids
    ]

    if verbose:
        print(f"   {len(scheduled)} tasks scheduled, {len(unscheduled)} unscheduled")

    result["scheduled"]   = scheduled
    result["unscheduled"] = unscheduled
    result["flagged"]     = flagged

    if not dry_run and scheduled:
        if verbose:
            print("📆  Creating calendar events…")
        ok, fail = create_calendar_events(token, scheduled, rules)
        result["ok_count"]   = ok
        result["fail_count"] = fail
        if verbose:
            print(f"   ✓ {ok} events created, {fail} failed")

    result["html_summary"] = build_schedule_summary_html(
        scheduled, unscheduled, flagged)

    # Always save plan for dashboard — regardless of dry_run
    try:
        save_plan(result)
    except Exception as e:
        print(f"  ⚠️  Could not save plan file: {e}")

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

# ── DASHBOARD SERVER ─────────────────────────────────────────────────────────

def serve_dashboard(api_key: str) -> None:
    """
    Serve the scheduling dashboard on localhost and handle:
      GET  /plan       — return scheduling_plan.json
      POST /refresh    — regenerate plan, return new plan JSON
      POST /push_event — create one calendar event, return {success, error}
    Opens browser automatically.
    """
    import threading
    import webbrowser
    from http.server import HTTPServer, BaseHTTPRequestHandler

    dashboard_file = Path(__file__).parent / "scheduling_dashboard.html"
    if not dashboard_file.exists():
        raise SystemExit("❌  scheduling_dashboard.html not found in project folder")

    dashboard_html = dashboard_file.read_text(encoding="utf-8")

    try:
        token = _get_token()
    except Exception as e:
        raise SystemExit(f"❌  Outlook auth error: {e}\n    Run: py outlook_email.py setup")

    rules = load_rules()
    if not rules:
        raise SystemExit("❌  scheduling_rules.json not found\n    Open scheduling_rules.html and save your rules first")

    cal_blocks = rules.get("calendar_blocks", {})

    def _create_one_event(item: dict) -> dict:
        """Create a single calendar event from a dashboard push_event request."""
        import datetime as _dt
        try:
            date_str  = item["date"]
            time_str  = item["start_time"]
            dur_mins  = int(item["duration_mins"])
            title     = item["task_title"]
            body_text = item.get("task_body", "")
            due       = item.get("task_due", "")
            overdue   = item.get("task_overdue", False)
            list_name = item.get("task_list", "")

            prefix    = cal_blocks.get("block_prefix", "🎯 ")
            show_as   = "free" if rules.get("global", {}).get("mark_as_free", True) else cal_blocks.get("block_show_as", "free")
            reminder  = cal_blocks.get("block_reminder", 10)

            # Build description
            parts = []
            if body_text:  parts.append(body_text)
            if overdue:    parts.append(f"⚠️ OVERDUE (was due {due})")
            elif due:      parts.append(f"Due: {due}")
            if list_name:  parts.append(f"From: {list_name}")
            description = "\n".join(parts)

            # Parse datetime in AEST then convert to UTC for Graph
            dt_local  = _dt.datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=AEST_OFFSET)
            dt_end    = dt_local + _dt.timedelta(minutes=dur_mins)
            start_utc = dt_local.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            end_utc   = dt_end.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

            body = {
                "subject": f"{prefix}{title}",
                "body": {"contentType": "text", "content": description},
                "start": {"dateTime": start_utc, "timeZone": "UTC"},
                "end":   {"dateTime": end_utc,   "timeZone": "UTC"},
                "showAs": show_as,
                "isReminderOn": reminder > 0,
                "reminderMinutesBeforeStart": reminder,
                "categories": ["Scheduled Task"],
            }
            _graph_post(token, "/me/events", body)
            print(f"   ✓  Created: {prefix}{title[:50]} @ {date_str} {time_str}")
            return {"success": True}
        except Exception as e:
            print(f"   ✗  Failed: {item.get('task_title','?')[:50]}: {e}")
            return {"success": False, "error": str(e)}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self._serve_html(dashboard_html)
            elif self.path == "/plan":
                if PLAN_FILE.exists():
                    self._serve_json(PLAN_FILE.read_text(encoding="utf-8"))
                else:
                    self._serve_json(json.dumps({"error": "No plan file found. Run: py outlook_scheduler.py preview"}))
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            length  = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length)) if length else {}

            if self.path == "/refresh":
                print("\n   ↺  Regenerating plan…")
                result = run_scheduler(api_key, dry_run=True, verbose=True)
                plan_json = PLAN_FILE.read_text(encoding="utf-8") if PLAN_FILE.exists() else json.dumps({})
                self._serve_json(plan_json)

            elif self.path == "/push_event":
                result = _create_one_event(payload)
                self._serve_json(json.dumps(result))
            else:
                self.send_response(404); self.end_headers()

        def _serve_html(self, html: str):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_json(self, json_str: str):
            body = json_str.encode("utf-8") if isinstance(json_str, str) else json_str
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # suppress HTTP noise

    for port in (8766, 8767, 8768):
        try:
            server = HTTPServer(("localhost", port), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit("❌  No free port found (tried 8766–8768)")

    url = f"http://localhost:{port}"
    print(f"\n   🌐  Scheduling Dashboard: {url}")
    print(f"       Review proposed blocks, adjust times, then click Push to Outlook.")
    print(f"       Press Ctrl+C to close.\n")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print("\n   Server closed.")
    finally:
        server.server_close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("❌  Set ANTHROPIC_API_KEY environment variable")

    mode = sys.argv[1] if len(sys.argv) > 1 else "dashboard"

    if mode == "dashboard":
        # Generate plan then open interactive dashboard
        print("🗓️  Generating scheduling plan…")
        result = run_scheduler(api_key, dry_run=True, verbose=True)
        if result.get("error"):
            print(f"\n⚠️  {result['error']}")
        print("\n🖥️   Opening Scheduling Dashboard…")
        serve_dashboard(api_key)

    elif mode == "preview":
        print("🗓️  Generating plan (preview only — no events created)")
        result = run_scheduler(api_key, dry_run=True)
        if result.get("error"):
            print(f"\n❌  {result['error']}"); sys.exit(1)
        print(f"\n{'='*55}\nPROPOSED SCHEDULE\n{'='*55}")
        for s in result["scheduled"]:
            print(f"  {s['start_dt'].strftime('%a %d %b %H:%M')} – {s['end_dt'].strftime('%H:%M')}  {s['task']['title'][:50]}  ({s['estimated_mins']}min)")
        if result["unscheduled"]:
            print(f"\nUnscheduled: {len(result['unscheduled'])} task(s)")
        print("\nPlan saved to scheduling_plan.json")
        print("Run 'py outlook_scheduler.py dashboard' to open the interactive dashboard")

    elif mode == "flagged":
        result = run_scheduler(api_key, dry_run=True, verbose=False)
        if result["flagged"]:
            print(f"⚑  {len(result['flagged'])} unfinished block(s) from yesterday:")
            for ev in result["flagged"]:
                print(f"   • {ev['subject']} @ {ev['start_dt'].strftime('%H:%M')}")
        else:
            print("✓ No unfinished blocks from yesterday")
    else:
        print("Usage: py outlook_scheduler.py [dashboard|preview|flagged]")
        sys.exit(1)
