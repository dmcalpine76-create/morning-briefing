"""
Temporary diagnostic. Graph is unreachable from both the cloud workspace and
the desktop VM, so this runs on the runner.

The repo is PUBLIC: prints COUNTS ONLY - no task titles, no list names, no
email subjects.
"""
import datetime, os
import todo_tasks as tt
import task_urgency as tu
import outlook_email as oe
import anthropic

tok = tt._get_token()

# ── the To Do side ──────────────────────────────────────────────────────────
lists = (tt._graph_get(tok, "/me/todo/lists", {}) or {}).get("value", []) or []
print(f"to do lists                      : {len(lists)}")
print(f"excluded by wellknownListName    : {sum(1 for l in lists if tt._excluded(l))}")

todo = tt.fetch_todo_tasks()
tasks = todo.get("tasks", [])
print(f"open tasks after exclusion       : {len(tasks)}")

today = datetime.date.today()
made_today = sum(1 for t in tasks if t.get("last_modified") == today)
print(f"  of those, touched today        : {made_today}")

done = tt.fetch_recent_completions()
print(f"recent completions (all lists)   : {len(done)}")

# ── the inbox side ──────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
analysis = oe.get_email_analysis(client)
actions = analysis.get("actions", []) or []
print(f"inbox actions extracted          : {len(actions)}")

open_titles = {tt._norm_title(t.get("title","")) for t in tasks if t.get("title")}
n_open  = sum(1 for a in actions if tt._norm_title(a.get("action","")) in open_titles)
n_done  = sum(1 for a in actions
              if tt._norm_title(a.get("action","")) in done
              and tt._norm_title(a.get("action","")) not in open_titles)
print(f"  suppressed: already OPEN task  : {n_open}")
print(f"  suppressed: completed recently : {n_done}")
print(f"  surviving to the Actions tab   : {len(actions) - n_open - n_done}")

# ── the ranking side ────────────────────────────────────────────────────────
sup = tt.suppressed_titles(tasks, done)
kept, _ = tt.drop_suppressed(actions, sup)
merged = tt.merge_with_inbox_actions(tasks, kept, done)
ranked = tu.rank_tasks(merged, [])
live, backlog = ranked["live"], ranked["backlog"]
print(f"ranked live                      : {len(live)}")
print(f"backlog                          : {len(backlog)}")
withsig = sum(1 for t in backlog if t.get("signal_score", 0) > 0)
print(f"  backlog items WITH a signal    : {withsig}  <- pushed out by the top-8 cap")
print(f"  backlog items with no signal   : {len(backlog) - withsig}")
