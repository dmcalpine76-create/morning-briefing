"""
test_backlog_sources.py
-----------------------
The Backlog tab was showing undated items that were never on the Daily
Priorities list, and items already resolved kept coming back. Two causes:

  1. merge_with_inbox_actions() adds every inbox-extracted action as a
     'proposed' task with due_date=None. rank_tasks() then swept anything
     scoring zero into the backlog, so unaccepted suggestions appeared there
     undated, with no Add control and no way to dismiss them.
  2. The de-duplication compared proposed actions against OPEN To Do titles
     only. Ticking a task off removed that suppression, so while the source
     email was still inside the 48h window the identical action was proposed
     again. Completing something made it reappear.

    py tests/test_backlog_sources.py
"""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import todo_tasks as tt
import task_urgency as tu
import schedule_render as sr

TODAY = datetime.date(2026, 9, 30)

OPEN_TASKS = [
    {"id": "t1", "title": "Lodge ATP 2062 renewal", "detail": "", "source": "todo",
     "days_over": 3, "due_date": TODAY, "bucket": "overdue", "last_modified": TODAY},
    {"id": "t2", "title": "Tidy the shared drive", "detail": "", "source": "todo",
     "days_over": 40, "due_date": TODAY - datetime.timedelta(days=40),
     "bucket": "overdue", "last_modified": TODAY - datetime.timedelta(days=40)},
]

ACTIONS = [
    {"action": "Reply to Aaron about the draft", "context": "no signal at all"},
    {"action": "Lodge ATP 2062 renewal",         "context": "same as an open task"},
    {"action": "Send Vroom the signed form",     "context": "done last week"},
]

COMPLETED = {tt._norm_title("Send Vroom the signed form")}

merged_no_hist = tt.merge_with_inbox_actions(OPEN_TASKS, ACTIONS)
merged         = tt.merge_with_inbox_actions(OPEN_TASKS, ACTIONS, COMPLETED)
titles         = [t["title"] for t in merged]

ranked = tu.rank_tasks(merged, [], TODAY)
backlog_sources = {t.get("source") for t in ranked["backlog"]}
backlog_titles  = [t["title"] for t in ranked["backlog"]]

zero_proposed = [t for t in merged
                 if t.get("source") == "proposed"
                 and tu.score_task(t, [], TODAY)["urgency_score"] == 0]

CHECKS = [
    ("an open task is not re-proposed",
     titles.count("Lodge ATP 2062 renewal") == 1),
    ("a recently completed task is not re-proposed",
     "Send Vroom the signed form" not in titles),
    ("without history it WAS re-proposed (the bug this guards)",
     "Send Vroom the signed form" in [t["title"] for t in merged_no_hist]),
    ("the backlog holds only real To Do tasks",
     backlog_sources <= {"todo"}),
    ("an unaccepted suggestion is not stranded in the backlog",
     bool(zero_proposed)
     and all(t["title"] not in backlog_titles for t in zero_proposed)),
    ("real untouched tasks still reach the backlog",
     "Tidy the shared drive" in backlog_titles),
    ("every backlog item carries a due date from To Do",
     all(t.get("due_date") for t in ranked["backlog"])),
    ("the backlog tab still renders",
     "Backlog" in sr.build_backlog_tab(ranked)
     or "Nothing in the backlog" in sr.build_backlog_tab(ranked)),
    # the tab is now ONLY the signal-free tasks; anything that merely lost the
    # top-eight race is ranked work and sits under the shortlist instead
    ("the backlog holds only signal-free tasks",
     all(t.get("signal_score", 0) <= 0 for t in ranked.get("quiet", []))),
    ("tasks that lost the cap are kept separate",
     all(t.get("signal_score", 0) > 0 for t in ranked.get("below_cut", []))),
    ("the two groups together are the old backlog",
     len(ranked.get("below_cut", [])) + len(ranked.get("quiet", []))
     == len(ranked["backlog"])),
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
