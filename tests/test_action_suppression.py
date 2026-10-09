"""
test_action_suppression.py
--------------------------
Clearing a task in To Do had no effect on whether it reappeared in Work
Actions the next morning. The Actions tab rendered analysis["actions"]
straight from the inbox read; the de-duplication only ever ran on the path
feeding the Schedule and Backlog tabs, so the two tabs disagreed about what
was still outstanding.

Two narrower faults rode along with it:
  * fetch_recent_completions read Daily Priorities alone, while open tasks had
    started coming from every list - complete something anywhere else and it
    was invisible to the suppression.
  * Microsoft's Flagged Emails list is every email ever flagged in Outlook,
    served through the To Do API as tasks. Merged in, it buried the backlog.

    py tests/test_action_suppression.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import todo_tasks as tt

SRC  = (Path(__file__).resolve().parent.parent / "todo_tasks.py").read_text(encoding="utf-8")
BRIEF = (Path(__file__).resolve().parent.parent / "briefing.py").read_text(encoding="utf-8")

OPEN_TASKS = [
    {"title": "Lodge ATP 2062 renewal", "list": "Daily Priorities"},
    {"title": "Call Tony about the board pack", "list": "Work"},
]
COMPLETED = {tt._norm_title("Send Vroom the signed form")}

ACTIONS = [
    {"action": "Lodge ATP 2062 renewal",        "context": "already an open task"},
    {"action": "Send Vroom the signed form",    "context": "completed last week"},
    {"action": "Call Tony about the board pack","context": "open, but in another list"},
    {"action": "Review the drilling budget",    "context": "genuinely new"},
]

sup = tt.suppressed_titles(OPEN_TASKS, COMPLETED)
kept, dropped = tt.drop_suppressed(ACTIONS, sup)
kept_titles = [a["action"] for a in kept]

none_kept, none_dropped = tt.drop_suppressed(ACTIONS, set())

CHECKS = [
    ("an action already captured as an open task is hidden",
     "Lodge ATP 2062 renewal" not in kept_titles),
    ("an action completed recently is hidden",
     "Send Vroom the signed form" not in kept_titles),
    ("an open task in ANY list suppresses, not just Daily Priorities",
     "Call Tony about the board pack" not in kept_titles),
    ("a genuinely new action survives",
     "Review the drilling budget" in kept_titles),
    ("the count of hidden items is reported", dropped == 3),
    ("an empty suppression set changes nothing",
     none_dropped == 0 and len(none_kept) == len(ACTIONS)),
    ("matching ignores case and punctuation",
     tt._norm_title("Lodge ATP-2062 Renewal!") == tt._norm_title("lodge atp 2062 renewal")),

    # completions now cover the same ground as the open-task read
    ("completions scan every list when none is named",
     'if list_name is None:' in SRC and '/me/todo/lists' in SRC
     and "out |= fetch_recent_completions(" in SRC),

    # non-task lists
    ("Flagged Emails is excluded", "flaggedemails" in tt.EXCLUDED_WELLKNOWN),
    ("the exclusion matches on wellknownListName, not the display name",
     'wk = (lst.get("wellknownListName")' in SRC),
    ("excluded lists are skipped when merging", "if _excluded(lst):" in SRC),
    ("skipped lists are named in the run log", "skipped non-task list(s)" in SRC),
    ("the exclusion also applies to the completions scan",
     SRC.count("if _excluded(lst):") >= 2),

    # the wiring that was missing
    ("the Actions tab is filtered before it renders",
     "drop_suppressed(" in BRIEF
     and BRIEF.index("drop_suppressed(") < BRIEF.index("html = generate_html(")),
    ("the filter runs after the inbox is read",
     BRIEF.index("email_analysis = _outlook.get_email_analysis") < BRIEF.index("drop_suppressed(")),
    ("the run log says how many were hidden and why",
     "hidden from the Actions tab" in BRIEF),
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
