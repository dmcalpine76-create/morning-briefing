"""
test_task_body.py
-----------------
To Do tasks were titles with a single sentence of context. The body is now
built by one shared function so the morning briefing and inbox_actions, which
each wrote their own, produce identical descriptions.

    py tests/test_task_body.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import task_body as tb

ROOT  = Path(__file__).resolve().parent.parent
BRIEF = (ROOT / "briefing.py").read_text(encoding="utf-8")
MAIL  = (ROOT / "outlook_email.py").read_text(encoding="utf-8")

full = tb.build(
    summary="BDO cannot sign off FY2026 until the KC statements land.",
    context="Jarryd Edy following up urgently on cashflow comments.",
    from_name="Jarryd Edy", subject="RE: CF comments",
    when=tb.pretty_when("2026-10-09T06:41:00Z"), deadline="2026-10-30",
    topic="FY2026 Audit", link="https://outlook.office.com/x", msg_id="AAA111")
thin = tb.build(context="Pool invoice outstanding.")
dupe = tb.build(summary="Chase the invoice.", context="Chase the invoice.")

CHECKS = [
    ("the summary leads", full.startswith("BDO cannot sign off")),
    ("the sender and subject appear", "From: Jarryd Edy · RE: CF comments" in full),
    ("the received time appears", "Received: Fri 9 Oct 2026, 4:41pm" in full),
    ("the deadline appears", "Due: 2026-10-30" in full),
    ("the topic appears", "Topic: FY2026 Audit" in full),
    ("the email link appears", "Open the email: https://outlook.office.com/x" in full),
    ("the source marker is last", full.rstrip().endswith("[src:AAA111]")),

    ("a thin action still gets a sensible body", thin == "Pool invoice outstanding."),
    ("no empty labels when fields are missing",
     "From:" not in thin and "Due:" not in thin and "[src:" not in thin),
    ("the context is not repeated when it IS the summary",
     dupe.count("Chase the invoice.") == 1),

    ("a date with no time gains no midnight",
     tb.pretty_when("2026-10-09") == "Fri 9 Oct 2026"),
    ("a full timestamp keeps its time",
     "4:41pm" in tb.pretty_when("2026-10-09T06:41:00Z")),
    ("an unparseable date survives rather than vanishing",
     tb.pretty_when("rubbish") == "rubbish"),
    ("an empty date gives nothing", tb.pretty_when("") == ""),

    ("a runaway summary is capped", len(tb.build(summary="x" * 5000)) < 700),

    # both callers
    ("the briefing composes the body with the shared builder",
     "_taskbody.build(" in BRIEF),
    ("task_body imports at module level, not inside the optional-tabs try",
     BRIEF.index("import task_body as _taskbody") < BRIEF.index("import calendar_data")),
    ("the model is asked for a 2-3 sentence summary",
     "summary (2-3 sentences" in MAIL),
    ("the sender and link are carried onto the action",
     'a["src_from"]' in MAIL and 'a["src_link"]' in MAIL),
    ("Gmail actions use the same builder", BRIEF.count("_taskbody.build(") >= 2),
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
