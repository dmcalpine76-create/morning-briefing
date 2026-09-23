"""
test_no_undefined_names.py
--------------------------
Static check for names used but never defined.

Two bugs of exactly this shape shipped in one afternoon: _personal_column
used _html, then URGENCY_BADGE, both of which briefing.py defines inside
_build_email_tab rather than at module level. py_compile cannot see either -
a NameError is a runtime event - and the first one killed a production run
after the whole briefing had been gathered.

    py tests/test_no_undefined_names.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ["briefing.py", "market_monitor.py", "schedule_render.py",
           "asx_announcements.py", "calendar_data.py", "calendar_lanes.py",
           "personal_calendar.py", "gmail_personal.py", "todo_tasks.py",
           "task_urgency.py", "settings_server.py"]

if __name__ == "__main__":
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        print("pyflakes not installed - run: py -m pip install pyflakes")
        sys.exit(0)          # advisory, never blocks a briefing run

    present = [m for m in MODULES if (ROOT / m).exists()]
    out = subprocess.run([sys.executable, "-m", "pyflakes", *present],
                         cwd=ROOT, capture_output=True, text=True)
    bad = [l for l in (out.stdout + out.stderr).splitlines()
           if "undefined name" in l]
    for line in bad:
        print("FAIL " + line)
    print(f"\nchecked {len(present)} modules; {len(bad)} undefined name(s)")
    sys.exit(1 if bad else 0)
