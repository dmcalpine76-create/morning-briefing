"""
todo_tasks.py
-------------
Reads real Microsoft To Do tasks so the Schedule tab can show what is actually
on your list, not only what today's email happened to suggest.

Until now the briefing only ever WROTE tasks to To Do. Everything shown as a
"task" was an AI-extracted action from the inbox, stamped due today. That meant
nothing you typed into To Do yourself ever appeared, and nothing had a real due
date, so "overdue" could not exist.

Uses the existing Outlook token (outlook_email.py already requests
Tasks.ReadWrite), so no new consent and no second token to refresh.

Self-test:
    py todo_tasks.py --test
"""

import sys
import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

GRAPH_BASE      = "https://graph.microsoft.com/v1.0"
REQUEST_TIMEOUT = 30
AEST_OFFSET     = datetime.timezone(datetime.timedelta(hours=10))

# The list both scripts agree on; core/config.py is the single source of truth.
try:
    from core.config import TASK_LIST_NAME
except Exception:
    TASK_LIST_NAME = "Daily Priorities"


def _get_token() -> str:
    """Reuse whichever auth path this project already has working."""
    try:
        import outlook_email
        return outlook_email.get_access_token()
    except Exception:
        pass
    from core.graph import get_access_token          # noqa: WPS433
    return get_access_token()


def _graph_get(token: str, path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _resolve_list(token: str, list_name: str) -> dict:
    data  = _graph_get(token, "/me/todo/lists")
    lists = data.get("value", []) or []
    for lst in lists:
        if (lst.get("displayName") or "").strip().lower() == list_name.strip().lower():
            return lst
    for lst in lists:
        if lst.get("wellknownListName") == "defaultList":
            return lst
    return lists[0] if lists else None


def _parse_due(task: dict):
    """
    To Do stores a due date as midnight in some timezone. We only ever want the
    calendar date — treating it as a timestamp is how 'due today' turns into
    'overdue by a few hours'.
    """
    due = task.get("dueDateTime")
    if not due or not due.get("dateTime"):
        return None
    raw = due["dateTime"].replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return datetime.datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _bucket(due, today):
    if due is None:
        return "no-date", 0
    delta = (due - today).days
    if delta < 0:
        return "overdue", -delta
    if delta == 0:
        return "today", 0
    if delta <= 7:
        return "next-7", delta
    return "later", delta


def fetch_todo_tasks(list_name: str = None, include_completed: bool = False) -> dict:
    """
    Returns {"tasks": [...], "list_name": str, "error": None | str}

    Each task:
        id, title, detail, importance (low|normal|high), due_date (date|None),
        bucket (overdue|today|next-7|later|no-date), days_over, days_until,
        source ("todo"), is_completed
    """
    list_name = list_name or TASK_LIST_NAME
    try:
        token = _get_token()
    except Exception as e:
        return {"tasks": [], "list_name": list_name,
                "error": f"no Outlook token — run 'py outlook_email.py setup' ({e})"}

    try:
        lst = _resolve_list(token, list_name)
    except Exception as e:
        return {"tasks": [], "list_name": list_name,
                "error": f"could not read your To Do lists: {e}"}
    if not lst:
        return {"tasks": [], "list_name": list_name, "error": "no To Do lists found"}

    params = {"$top": 200}
    if not include_completed:
        params["$filter"] = "status ne 'completed'"

    try:
        data = _graph_get(token, f"/me/todo/lists/{lst['id']}/tasks", params)
    except Exception as e:
        return {"tasks": [], "list_name": lst.get("displayName", list_name),
                "error": f"could not read tasks: {e}"}

    today = datetime.datetime.now(AEST_OFFSET).date()
    tasks = []
    for t in data.get("value", []) or []:
        due = _parse_due(t)
        bucket, delta = _bucket(due, today)
        body = (t.get("body") or {}).get("content", "") or ""
        tasks.append({
            "id":           t.get("id", ""),
            "title":        (t.get("title") or "").strip(),
            "detail":       body.strip()[:300],
            "importance":   (t.get("importance") or "normal").lower(),
            "due_date":     due,
            "bucket":       bucket,
            "days_over":    delta if bucket == "overdue" else 0,
            "days_until":   delta if bucket in ("next-7", "later") else 0,
            "is_completed": (t.get("status") or "") == "completed",
            "source":       "todo",
        })

    order = {"overdue": 0, "today": 1, "next-7": 2, "no-date": 3, "later": 4}
    rank  = {"high": 0, "normal": 1, "low": 2}
    tasks.sort(key=lambda x: (order.get(x["bucket"], 9),
                              x["due_date"] or datetime.date.max,
                              rank.get(x["importance"], 1)))

    return {"tasks": tasks, "list_name": lst.get("displayName", list_name), "error": None}


def merge_with_inbox_actions(todo_tasks: list, inbox_actions: list) -> list:
    """
    One list, two origins. Real To Do tasks are the spine; inbox-extracted
    actions ride alongside marked 'proposed' so accepting one stays a
    deliberate act rather than clutter arriving uninvited.

    An inbox action whose text closely matches an existing task is dropped —
    it is almost always the same thing already captured.
    """
    def norm(s):
        return "".join(ch for ch in (s or "").lower() if ch.isalnum())[:60]

    existing = {norm(t["title"]) for t in todo_tasks}
    merged   = list(todo_tasks)

    for i, item in enumerate(inbox_actions or []):
        title = (item.get("action") or "").strip()
        if not title or norm(title) in existing:
            continue
        merged.append({
            "id":           f"proposed_{i}",
            "title":        title,
            "detail":       (item.get("context") or "").strip()[:300],
            "importance":   {"urgent": "high", "high": "high"}.get(
                                (item.get("priority") or "normal").lower(), "normal"),
            "due_date":     None,
            "bucket":       "today",
            "days_over":    0,
            "days_until":   0,
            "is_completed": False,
            "source":       "proposed",
            "from_email":   item.get("from_email", ""),
            "deadline_txt": item.get("deadline", ""),
        })
    return merged


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test():
    print("Microsoft To Do self-test")
    print("=" * 66)
    print(f"list configured : {TASK_LIST_NAME}")

    result = fetch_todo_tasks()
    if result["error"]:
        print(f"ERROR           : {result['error']}")
        return 1

    tasks = result["tasks"]
    print(f"list found      : {result['list_name']}")
    print(f"open tasks      : {len(tasks)}")
    print()

    counts = {}
    for t in tasks:
        counts[t["bucket"]] = counts.get(t["bucket"], 0) + 1
    for b in ("overdue", "today", "next-7", "no-date", "later"):
        if counts.get(b):
            print(f"  {b:<9} {counts[b]}")
    print()

    if not tasks:
        print("No open tasks. If that is wrong, check the list name above matches")
        print("the list you actually use in Microsoft To Do.")
        return 0

    print(f"{'bucket':<9} {'due':<12} {'imp':<7} title")
    print("-" * 66)
    for t in tasks[:30]:
        due = t["due_date"].strftime("%a %d %b") if t["due_date"] else "—"
        extra = f"  ({t['days_over']}d over)" if t["bucket"] == "overdue" else ""
        print(f"{t['bucket']:<9} {due:<12} {t['importance']:<7} {t['title'][:34]}{extra}")
    if len(tasks) > 30:
        print(f"... and {len(tasks) - 30} more")

    print()
    print("Check the overdue count looks right and that due dates match what you")
    print("see in To Do — a date read as a timestamp is the usual cause of drift.")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
