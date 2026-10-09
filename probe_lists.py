"""
Temporary diagnostic: does the Flagged Emails exclusion actually catch Doug's
flagged-email list, and what would the backlog have contained without it?

Microsoft Graph is unreachable from both the cloud workspace and the desktop
VM, so this has to run on the GitHub runner. The repo is PUBLIC, so this
prints NO list display names and NO task titles - only counts and
wellknownListName, which is a fixed Microsoft enum (none / defaultList /
flaggedEmails / unknownFutureValue), not user data.
"""
import todo_tasks as tt

tok   = tt._get_token()
lists = (tt._graph_get(tok, "/me/todo/lists", {}) or {}).get("value", []) or []

print(f"lists visible to the briefing : {len(lists)}")
seen = {}
for l in lists:
    wk = (l.get("wellknownListName") or "none").strip()
    seen[wk] = seen.get(wk, 0) + 1
print(f"wellknownListName values seen : {seen}")

excluded = [l for l in lists if tt._excluded(l)]
print(f"lists the exclusion catches   : {len(excluded)}")

flagged_tasks = 0
for l in excluded:
    try:
        d = tt._graph_get(tok, f"/me/todo/lists/{l['id']}/tasks",
                          {"$filter": "status ne 'completed'", "$top": 200})
        flagged_tasks += len(d.get("value", []) or [])
    except Exception:
        pass
print(f"open items inside those lists : {flagged_tasks}")

kept = tt.fetch_todo_tasks()
print(f"open tasks AFTER exclusion    : {len(kept.get('tasks', []))}")
print(f"lists skipped                 : {len(kept.get('skipped', []))}")
print()
if excluded:
    print(f"VERDICT: flagged-email list found and EXCLUDED "
          f"({flagged_tasks} items kept out of the backlog)")
else:
    print("VERDICT: NO list matched the exclusion - if a flagged-email list "
          "exists it is NOT tagged flaggedEmails and is still being merged")
