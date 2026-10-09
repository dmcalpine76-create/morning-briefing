"""Temporary diagnostic. Counts and AGES only - no subjects, no titles."""
import datetime, os, collections
import todo_tasks as tt, task_urgency as tu, outlook_email as oe, anthropic

now = datetime.datetime.now(datetime.timezone.utc)
def age_h(ts):
    if not ts: return None
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None: d = d.replace(tzinfo=datetime.timezone.utc)
        return round((now - d).total_seconds() / 3600, 1)
    except Exception:
        return None

tok = oe.get_access_token()
emails = oe.fetch_recent_emails(tok)
print(f"HOURS_BACK constant              : {oe.HOURS_BACK}")
print(f"emails fetched                   : {len(emails)}")
ages = [a for a in (age_h(e.get('received')) for e in emails) if a is not None]
if ages:
    print(f"  age range (hours)              : {min(ages)} .. {max(ages)}")
    buckets = collections.Counter()
    for a in ages:
        buckets["0-24h" if a <= 24 else "24-48h" if a <= 48 else
                "48-168h" if a <= 168 else "OLDER THAN A WEEK"] += 1
    for k in ("0-24h", "24-48h", "48-168h", "OLDER THAN A WEEK"):
        if buckets[k]: print(f"    {k:<20}: {buckets[k]}")
print(f"  sent / received                : {sum(1 for e in emails if e.get('is_sent'))}"
      f" / {sum(1 for e in emails if not e.get('is_sent'))}")

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
an = oe.analyse_emails(client, emails)
acts = an.get("actions", []) or []
print(f"\nactions returned                 : {len(acts)} (cap {oe.MAX_ACTIONS})")
print(f"  with a resolved msg_id         : {sum(1 for a in acts if a.get('msg_id'))}")
print(f"  derived from a SENT email      : {sum(1 for a in acts if a.get('src_sent'))}")
sa = [age_h(a.get("src_time")) for a in acts]
for i, a in enumerate(sa, 1):
    print(f"    action {i}: source email {a}h old" if a is not None
          else f"    action {i}: source UNRESOLVED")

todo = tt.fetch_todo_tasks(); tasks = todo.get("tasks", [])
done = tt.fetch_recent_completions(); dsrc = tt.fetch_recent_completion_sources()
idx = tt.suppression_index(tasks, done, dsrc)
kept, dropped, why = tt.drop_suppressed(acts, idx)
print(f"\nsuppressed                       : {dropped} {why}")
merged = tt.merge_with_inbox_actions(tasks, kept, done)
r = tu.rank_tasks(merged, [])
print(f"open tasks                       : {len(tasks)}")
print(f"live / below_cut / quiet         : {len(r['live'])} / "
      f"{len(r.get('below_cut',[]))} / {len(r.get('quiet',[]))}")
print(f"backlog total (tab button count) : {len(r['backlog'])}")
