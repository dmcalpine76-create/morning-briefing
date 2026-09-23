"""
test_actions_tab.py
-------------------
Imports briefing.py for real and builds the Actions tab.

Written because a NameError shipped to production: _personal_column used
_html, which briefing.py imports function-locally rather than at module
level. The first test of that function exec'd the source with _html supplied
in the namespace, so the test passed and the CI run died. A stubbed
dependency is not a test of the code that runs.

    py tests/test_actions_tab.py
"""
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import briefing as b

ANALYSIS = {"digest": [{"from_name": "Holly Zhang", "subject": "Model request",
                        "summary": "Needs the financial model.", "action": "Send model"}],
            "actions": [{"action": "Send financial model", "context": "To Treasury",
                         "from_email": "holly@x", "priority": "high", "deadline": "Thu"}]}
PERSONAL = [{"action": "Confirm booking", "context": "Needs numbers",
             "from": "Anna", "deadline": "Sat", "priority": "high"}]

tab = b._build_email_tab(ANALYSIS, asx_ann_data={"announcements": []},
                         personal_actions=PERSONAL, personal_status={})
cols = [c.strip() for c in
        re.findall(r'ep-panel-title">\s*\S*\s*([A-Za-z ]+)', tab) if c.strip()]
t_err   = b._build_email_tab(ANALYSIS, personal_actions=[],
                             personal_status={"error": "IMAP error: bad password"})
t_quiet = b._build_email_tab(ANALYSIS, personal_actions=[],
                             personal_status={"checked": 31, "days": 5})

CHECKS = [
    ("four columns render",            len(cols) >= 4),
    ("Personal column present",        "Personal" in tab),
    ("personal item renders",          "Confirm booking" in tab),
    ("deadline chip renders",          "Sat" in tab),
    ("To Do deep link present",        "to-do.microsoft.com/tasks/add" in tab),
    ("outlook actions still render",   "Send financial model" in tab),
    ("priority digest still renders",  "Holly Zhang" in tab),
    ("no unresolved format fields",    not re.search(r"\{[a-z_]+\}", tab)),
    # the three states the column must never collapse into silence
    ("failure reason is stated",       "was not read" in t_err),
    ("quiet run is stated",            "Nothing personal" in t_quiet),
    ("payload is escaped",
     "&amp;" in b._personal_column([{"action": "Pay Smith & Co",
                                     "context": "x", "from": "a"}])),
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
    print(f"\ncolumns: {cols}")
    print(f"{len(CHECKS) - bad}/{len(CHECKS)} passed")
    sys.exit(1 if bad else 0)
