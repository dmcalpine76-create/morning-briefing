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

# Which To Do lists the briefing is allowed to read.
#
# Reading EVERY list (4 Oct) pulled in all thirteen, and the result was the
# opposite of useful: the curated lists carry urgency signals so their tasks
# rank onto the Schedule tab, leaving the backlog as a pile of everything
# else - shopping lists, someday lists, anything. An allowlist, not a
# blocklist: name the lists that hold real work and ignore the rest.
#
# Override with "task_lists" in briefing_settings.json.
DEFAULT_TASK_LISTS = ["Daily Priorities", "Tasks"]


def _configured_lists() -> list:
    try:
        import json as _json
        from pathlib import Path as _Path
        cfg = _json.loads((_Path(__file__).parent / "briefing_settings.json")
                          .read_text(encoding="utf-8"))
        names = cfg.get("task_lists")
        if isinstance(names, list) and names:
            return [str(n).strip() for n in names if str(n).strip()]
    except Exception:
        pass
    return list(DEFAULT_TASK_LISTS)


def _wanted(lst: dict, allowed_lower: set) -> bool:
    """A list is read if it is named in the allowlist, or IS the default list.

    wellknownListName 'defaultList' is Microsoft's "Tasks"; matching it by id
    as well as by name means a renamed or localised default still counts.
    """
    if _excluded(lst):
        return False
    name = (lst.get("displayName") or "").strip().lower()
    wk   = (lst.get("wellknownListName") or "").strip().lower()
    return name in allowed_lower or (wk == "defaultlist"
                                     and "tasks" in allowed_lower)


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


# Microsoft's Flagged Emails list is every email you ever flagged in Outlook,
# surfaced through the To Do API as if each were a task. Merged in, it buries
# the backlog in items you never created and duplicates the Priority Digest.
# Matched on wellknownListName, so a user list that happens to be called
# something similar is untouched.
EXCLUDED_WELLKNOWN = {"flaggedemails"}


def _excluded(lst: dict) -> bool:
    wk = (lst.get("wellknownListName") or "").strip().lower()
    return wk in EXCLUDED_WELLKNOWN


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
    With no list named, read EVERY To Do list (tasks are often added straight
    into lists other than Daily Priorities) and merge them, each task tagged
    with its list. Name a list to read just that one, as before.
    """
    if list_name is None:
        return _fetch_all_lists(include_completed)
    return _fetch_one_list(list_name, include_completed)


def _fetch_all_lists(include_completed: bool = False) -> dict:
    try:
        token = _get_token()
        lists = (_graph_get(token, "/me/todo/lists", {}) or {}).get("value", []) or []
    except Exception:
        return _fetch_one_list(TASK_LIST_NAME, include_completed)   # fall back to the old behaviour
    allowed = _configured_lists()
    allowed_lower = {a.lower() for a in allowed}
    merged, errors, skipped, used = [], [], [], []
    for lst in lists:
        if not _wanted(lst, allowed_lower):
            skipped.append(lst.get("displayName", "") or lst.get("wellknownListName", ""))
            continue
        used.append(lst.get("displayName", ""))
        r = _fetch_one_list(lst.get("displayName", ""), include_completed)
        if r.get("error"):
            errors.append(r["error"])
        for t in r.get("tasks", []):
            t["list"] = lst.get("displayName", "")
            merged.append(t)
    order = {"overdue": 0, "today": 1, "next-7": 2, "no-date": 3, "later": 4}
    rank  = {"high": 0, "normal": 1, "low": 2}
    merged.sort(key=lambda x: (order.get(x["bucket"], 9),
                               x["due_date"] or datetime.date.max,
                               rank.get(x["importance"], 1)))
    print(f"   .  reading {len(used)} of {len(lists)} To Do list(s): "
          f"{', '.join(used) or 'none matched the allowlist'}")
    if not used:
        print(f"   !  none of {allowed} were found - check 'task_lists' "
              f"in briefing_settings.json")
    return {"tasks": merged, "list_name": ", ".join(used) or "none",
            "skipped": skipped, "used": used,
            "error": None if merged or not errors else errors[0]}


def _fetch_one_list(list_name: str = None, include_completed: bool = False) -> dict:
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
        lm = None
        raw_lm = t.get("lastModifiedDateTime") or ""
        if raw_lm:
            try:
                lm = datetime.datetime.fromisoformat(raw_lm.replace("Z", "+00:00")).date()
            except Exception:
                lm = None
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
            "last_modified": lm,
        })

    order = {"overdue": 0, "today": 1, "next-7": 2, "no-date": 3, "later": 4}
    rank  = {"high": 0, "normal": 1, "low": 2}
    tasks.sort(key=lambda x: (order.get(x["bucket"], 9),
                              x["due_date"] or datetime.date.max,
                              rank.get(x["importance"], 1)))

    return {"tasks": tasks, "list_name": lst.get("displayName", list_name), "error": None}


def fetch_recent_completions(list_name: str = None, days: int = 45) -> set:
    """
    Normalised titles of tasks completed in the last `days`, across EVERY list
    unless one is named.

    Needed because the open-task list cannot suppress a re-proposal: the moment
    you tick a task off it leaves that list, so an inbox action matching it was
    proposed all over again while its source email was still in the window.
    Completing something made it come back, which is the opposite of useful.

    It used to read Daily Priorities alone. Once open tasks began coming from
    every list, that left the two halves asymmetric - a task completed anywhere
    else was invisible here and came straight back.

    Best-effort — any failure returns an empty set and the merge simply behaves
    as it did before.
    """
    if list_name is None:
        try:
            token = _get_token()
            lists = (_graph_get(token, "/me/todo/lists", {}) or {}).get("value", []) or []
        except Exception as e:
            print(f"   !  completed-task check skipped: {e}")
            return set()
        out = set()
        allowed_lower = {a.lower() for a in _configured_lists()}
        for lst in lists:
            if not _wanted(lst, allowed_lower):
                continue
            out |= fetch_recent_completions(lst.get("displayName", ""), days)
        return out

    list_name = list_name or TASK_LIST_NAME
    try:
        token = _get_token()
        lst   = _resolve_list(token, list_name)
        if not lst:
            return set()
    except Exception as e:
        print(f"   !  completed-task check skipped: {e}")
        return set()

    # Graph rejects some $filter/$orderby combinations on To Do tasks, and a
    # silent empty set here would make the whole re-proposal fix inert without
    # anyone noticing. So: try the narrow query, then the same without the
    # ordering, then read everything and filter here.
    attempts = [
        {"$filter": "status eq 'completed'",
         "$orderby": "lastModifiedDateTime desc", "$top": 200},
        {"$filter": "status eq 'completed'", "$top": 200},
        {"$top": 200},
    ]
    data, why = None, ""
    for i, params in enumerate(attempts, 1):
        try:
            data = _graph_get(token, f"/me/todo/lists/{lst['id']}/tasks", params)
            if i > 1:
                print(f"   .  completed-task check used fallback query {i}")
            break
        except Exception as e:
            why = str(e)[:120]
    if data is None:
        print(f"   !  completed-task check failed, nothing suppressed: {why}")
        return set()

    cutoff = datetime.datetime.now(AEST_OFFSET).date() - datetime.timedelta(days=days)
    out = set()
    for t in data.get("value", []) or []:
        if (t.get("status") or "") != "completed":
            continue                      # the third attempt returns open ones too
        when = (t.get("completedDateTime") or {}).get("dateTime") or \
               t.get("lastModifiedDateTime") or ""
        keep = True
        if when:
            try:
                keep = datetime.datetime.fromisoformat(
                    when.replace("Z", "+00:00")).date() >= cutoff
            except Exception:
                keep = True
        if keep and t.get("title"):
            out.add(_norm_title(t["title"]))
    return out


def fetch_recent_completion_sources(list_name: str = None, days: int = 45) -> set:
    """
    Message ids recorded in the bodies of recently completed tasks.

    This is the strong half of the suppression: the action text changes
    between runs, the id of the email it came from does not.
    """
    if list_name is None:
        try:
            token = _get_token()
            lists = (_graph_get(token, "/me/todo/lists", {}) or {}).get("value", []) or []
        except Exception:
            return set()
        out = set()
        allowed_lower = {a.lower() for a in _configured_lists()}
        for lst in lists:
            if _wanted(lst, allowed_lower):
                out |= fetch_recent_completion_sources(lst.get("displayName", ""), days)
        return out

    try:
        token = _get_token()
        lst = _resolve_list(token, list_name)
        if not lst:
            return set()
        data = _graph_get(token, f"/me/todo/lists/{lst['id']}/tasks",
                          {"$filter": "status eq 'completed'", "$top": 200})
    except Exception:
        return set()

    import re as _re
    cutoff = datetime.datetime.now(AEST_OFFSET).date() - datetime.timedelta(days=days)
    out = set()
    for t in data.get("value", []) or []:
        when = (t.get("completedDateTime") or {}).get("dateTime") or \
               t.get("lastModifiedDateTime") or ""
        keep = True
        if when:
            try:
                keep = datetime.datetime.fromisoformat(
                    when.replace("Z", "+00:00")).date() >= cutoff
            except Exception:
                keep = True
        if not keep:
            continue
        body = ((t.get("body") or {}).get("content") or "")
        for m in _re.finditer(r"\[src:([^\]]{4,})\]", body):
            out.add(m.group(1).strip())
    return out


def _norm_title(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())[:60]


def merge_with_inbox_actions(todo_tasks: list, inbox_actions: list,
                             completed_titles: set = None) -> list:
    """
    One list, two origins. Real To Do tasks are the spine; inbox-extracted
    actions ride alongside marked 'proposed' so accepting one stays a
    deliberate act rather than clutter arriving uninvited.

    An inbox action whose text closely matches an open task — or one completed
    recently — is dropped. It is almost always the same thing already handled.
    """
    norm = _norm_title
    existing = {norm(t["title"]) for t in todo_tasks} | set(completed_titles or ())
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
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(_self_test())


SRC_TAG = "[src:%s]"          # written into a task body when it is created


def src_marker(msg_id: str) -> str:
    return SRC_TAG % msg_id if msg_id else ""


def suppression_index(tasks: list, completed: set = None,
                      completed_src: set = None) -> dict:
    """
    What the Actions tab should stop offering, and why.

    Two keys, deliberately different in strength:

      "src"   - message ids of emails whose task has been COMPLETED. Exact,
                survives the wording changing between runs, and is the only
                reliable signal we have.
      "title" - normalised titles of COMPLETED tasks. A weak fallback for
                tasks created before source ids were recorded.

    Open tasks are NOT suppressed. An earlier version hid actions already
    captured as open tasks; that collides with running inbox_actions first,
    which captures everything into To Do and would then empty the Actions tab
    of the very items just captured. Only "I have finished this" hides an item.
    """
    titles = {_norm_title(t.get("title", "")) for t in (tasks or [])
              if t.get("title") and t.get("is_completed")}
    return {"src": set(completed_src or ()), "title": set(completed or ()) | titles}


def _task_src_ids(tasks: list) -> set:
    """Message ids recorded in task bodies, for tasks that are completed."""
    import re as _re
    out = set()
    for t in (tasks or []):
        if not t.get("is_completed"):
            continue
        for m in _re.finditer(r"\[src:([^\]]{4,})\]", t.get("detail", "") or ""):
            out.add(m.group(1).strip())
    return out


def drop_suppressed(actions: list, index: dict) -> tuple:
    """
    Returns (kept, dropped, reasons) where reasons counts by cause.

    Matching prefers the message id. Title matching is a fallback only, and
    only against completed work - the action text is regenerated by the model
    on every run, so the same item comes back worded differently and title
    matching alone never caught it.
    """
    index = index or {}
    by_src, by_title = index.get("src") or set(), index.get("title") or set()
    if not by_src and not by_title:
        return list(actions or []), 0, {}
    kept, reasons = [], {"source email completed": 0, "title matches completed task": 0}
    for a in (actions or []):
        mid = (a.get("msg_id") or "").strip()
        if mid and mid in by_src:
            reasons["source email completed"] += 1
            continue
        if _norm_title(a.get("action", "")) in by_title:
            reasons["title matches completed task"] += 1
            continue
        kept.append(a)
    reasons = {k: v for k, v in reasons.items() if v}
    return kept, len(actions or []) - len(kept), reasons
