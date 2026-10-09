"""
test_action_suppression.py
--------------------------
Clearing a task in To Do had no effect on whether it reappeared in Work
Actions. Two separate reasons, found in that order:

  1. The Actions tab rendered analysis["actions"] straight from the inbox
     read - the de-duplication only ran on the path feeding the Schedule and
     Backlog tabs.
  2. Fixing (1) changed nothing, because matching was on the action TITLE and
     the model rewords the action on every run. A probe against the live
     mailbox found 0 of 8 actions suppressed despite 277 completed tasks.

The fix is to key on the source email. Each action now carries the msg_id of
the message it came from, that id is written into the task body as [src:...]
when the task is created, and a completed task suppresses the same action
however it is worded next time.

Open tasks are deliberately NOT suppressed: Doug runs inbox_actions first,
which captures everything into To Do, so hiding anything already captured
would empty the Actions tab of the items just captured.

    py tests/test_action_suppression.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import todo_tasks as tt

SRC   = (Path(__file__).resolve().parent.parent / "todo_tasks.py").read_text(encoding="utf-8")
BRIEF = (Path(__file__).resolve().parent.parent / "briefing.py").read_text(encoding="utf-8")
MAIL  = (Path(__file__).resolve().parent.parent / "outlook_email.py").read_text(encoding="utf-8")
# assert against the PROMPT, not the whole file - the comments explaining the
# old behaviour quote the very strings we are checking have gone
PROMPT = MAIL[MAIL.index('prompt = f"""You are a sharp executive assistant'):
              MAIL.index("EMAILS:")]

TASKS = [
    {"title": "Lodge ATP 2062 renewal", "list": "Daily Priorities",
     "is_completed": False, "detail": "x " + tt.src_marker("AAA-open")},
    {"title": "Send Vroom the signed form", "list": "Work",
     "is_completed": True,  "detail": "done " + tt.src_marker("BBB-done")},
]
idx = tt.suppression_index(TASKS, completed={tt._norm_title("Old worded task")},
                           completed_src=tt._task_src_ids(TASKS))

ACTIONS = [
    {"action": "Chase Vroom for the counter-signed copy", "msg_id": "BBB-done"},
    {"action": "Lodge ATP 2062 renewal",                  "msg_id": "AAA-open"},
    {"action": "Old worded task",                         "msg_id": ""},
    {"action": "Review the drilling budget",              "msg_id": "CCC-new"},
]
kept, dropped, why = tt.drop_suppressed(ACTIONS, idx)
titles = [a["action"] for a in kept]

CHECKS = [
    ("a reworded action is caught by its source email",
     "Chase Vroom for the counter-signed copy" not in titles),
    ("an OPEN task does not suppress - inbox_actions runs first",
     "Lodge ATP 2062 renewal" in titles),
    ("the title fallback still catches pre-marker completions",
     "Old worded task" not in titles),
    ("a genuinely new action survives", "Review the drilling budget" in titles),
    ("the count is right", dropped == 2),
    ("the reason is reported per cause", set(why) and sum(why.values()) == 2),
    ("an empty index changes nothing",
     tt.drop_suppressed(ACTIONS, {})[1] == 0),
    ("source ids are read only from COMPLETED tasks",
     tt._task_src_ids(TASKS) == {"BBB-done"}),

    # plumbing
    ("the action schema asks for the email index, not a subject",
     "index (the [n] of the email" in PROMPT
     and "from_email (subject reference)" not in PROMPT),
    ("the source is resolved server-side", 'a["msg_id"]     = (src or {}).get("msg_id"' in MAIL),
    ("the model is told the real window, not 24h",
     "HOURS_BACK       = 48" in MAIL and "last 24 hours" not in PROMPT
     and "{HOURS_BACK} hours" in PROMPT),
    ("the model is told not to pad to the maximum", "do NOT pad the list" in MAIL),
    ("the marker is written on the server push path", "src_marker(src)" in BRIEF),
    ("the marker is written on the browser push path", "[src:' + task.msg_id" in BRIEF),
    ("msg_id is carried into the task payload", '"msg_id":   item.get("msg_id"' in BRIEF),
    ("completions are scanned for source ids",
     "fetch_recent_completion_sources" in SRC and "fetch_recent_completion_sources()" in BRIEF),

    # the list exclusion, unchanged
    ("Flagged Emails is still excluded", "flaggedemails" in tt.EXCLUDED_WELLKNOWN),
]

# The model labels the emails [1], [2]... and returns the index THAT way -
# "[1]", not 1 - so int() threw on every action and every source was lost
# silently. A live probe found 0 of 6 resolved. Parse the digits out of
# whatever shape arrives.
import re as _re
def _idx(raw):
    m = _re.search(r"\d+", str(raw or ""))
    return int(m.group(0)) - 1 if m else None

CHECKS += [
    ("the bracketed form the model actually sends resolves", _idx("[1]") == 0),
    ("a two-digit bracketed index resolves", _idx(" [15] ") == 14),
    ("a bare int resolves", _idx(3) == 2),
    ("a bare string resolves", _idx("3") == 2),
    ("other shapes resolve", _idx("#3") == 2 and _idx("Email 2") == 1),
    ("nothing usable stays unresolved",
     _idx(None) is None and _idx("") is None and _idx("none") is None),
    ("the parser in outlook_email is the digit-extracting one",
     '_re.search' in MAIL or 're.search(r"\\d+", str(raw_idx' in MAIL),
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
