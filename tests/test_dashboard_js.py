"""
test_dashboard_js.py
--------------------
Every tab on the dashboard went dead after a one-line change to the Actions
code. The dashboard's JavaScript is emitted from a Python f-string, and the
change added

    '\\n\\n[src:' + task.msg_id + ']'

to the task-push payload. Inside an f-string Python turns \\n into a REAL
newline, so the browser received a string literal broken across two lines -
a SyntaxError that killed the entire inline <script>, and with it showTab().
Nothing rendered but the front page, and no Python test could see it because
the Python was perfectly valid.

So: extract the generated <script> blocks and parse them with node. This
catches any malformed JavaScript, not just this one.

    py tests/test_dashboard_js.py
"""
import sys, re, subprocess, tempfile, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import briefing as b

CHECKS = []

# A real render with empty-but-valid inputs exercises every f-string branch.
html = b.generate_html(
    {"International News": []}, datetime.datetime.now(), [],
    {"digest": [], "actions": [{"action": "t", "context": "c", "priority": "normal",
                                "msg_id": "ABC123", "deadline": ""}], "people": []},
    [], [], {}, [], [], [], {}, {},
)
CHECKS.append(("the briefing renders", len(html) > 5000))

scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
scripts = [s for s in scripts if s.strip() and "src=" not in s[:80]]
CHECKS.append(("inline scripts are present", len(scripts) >= 1))

bad = []
for i, src in enumerate(scripts):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(src)
        path = f.name
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if r.returncode != 0:
        first = (r.stderr or "").strip().splitlines()
        bad.append(f"script #{i + 1}: " + (first[2] if len(first) > 2 else str(first[:1])))
CHECKS.append(("every inline script parses as JavaScript", not bad))

# the specific regression
CHECKS.append(("the push payload has no raw newline in a JS string literal",
               not re.search(r"'[^'\n]*\n[^']*\[src:", html)))
CHECKS.append(("showTab survives into the page", "function showTab(" in html))
CHECKS.append(("the source marker still reaches the body",
               "[src:' + task.msg_id" in html))

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    n = 0
    for label, ok in CHECKS:
        print(("ok   " if ok else "FAIL ") + label)
        n += not ok
    for line in bad:
        print("     " + line)
    print(f"\n{len(CHECKS) - n}/{len(CHECKS)} passed")
    sys.exit(1 if n else 0)
