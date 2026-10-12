"""
test_email_analysis_budget.py
-----------------------------
From 13 October every Outlook section vanished. The analysis reply had grown
past max_tokens=4000 once each action carried a 2-3 sentence summary, so the
JSON was cut off, json.loads failed, and the except returned empty lists.

This keeps the output cap comfortably above a full-house reply - every list at
its maximum, with the fields the prompt now asks for - and checks that a
failure records why instead of only printing it.

    py tests/test_email_analysis_budget.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import outlook_email as oe

# Rough per-item token cost of each list's fields, as the prompt specifies them,
# plus keys, punctuation and indentation. Deliberately generous.
PER_DIGEST = 170
PER_ACTION = 190      # action, context, deadline, priority, index, summary
PER_PERSON = 120

full_house = (oe.MAX_DIGEST_ITEMS * PER_DIGEST
              + oe.MAX_ACTIONS * PER_ACTION
              + oe.MAX_PEOPLE * PER_PERSON
              + 300)  # envelope

src = (Path(oe.__file__)).read_text(encoding="utf-8")

CHECKS = [
    ("the cap clears a full-house reply with 2x headroom",
     oe.ANALYSIS_MAX_TOKENS >= 2 * full_house),
    ("the old 4000 cap would NOT have cleared it (the bug this guards)",
     4000 < full_house),
    ("analyse_emails uses the named cap, not a literal",
     "max_tokens = ANALYSIS_MAX_TOKENS" in src),
    ("a failed analysis records why, including the stop reason",
     "stop_reason=" in src and "LAST_ERROR = (f\"AI analysis failed" in src),
    ("hitting the cap is called out by name",
     "hit the max_tokens cap" in src),
]

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"  full-house estimate: {full_house} tokens; cap: {oe.ANALYSIS_MAX_TOKENS}")
    bad = 0
    for label, ok in CHECKS:
        print(("ok   " if ok else "FAIL ") + label)
        bad += not ok
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} passed")
    sys.exit(1 if bad else 0)
