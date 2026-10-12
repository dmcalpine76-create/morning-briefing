"""
test_run_diag.py
----------------
The 13 October run published a briefing with no MP3 and no Outlook sections,
exited 0, and recorded the reason only in an Actions log served from a host the
egress policy blocks. These checks cover the two things that has to stop:

  1. a step that fails is recorded, with the reason, not just printed
  2. a failed step reaches the email, where it can actually be read

    py tests/test_run_diag.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_diag as diag
import outlook_email as oe

diag.reset()
diag.ok("Outlook", "9 priority emails, 8 actions")
diag.ok("Audio", "1.7 MB, voice en-US-ChristopherNeural, rate +25%")
healthy = diag.steps()
healthy_html = oe._build_diag_block(healthy)

diag.reset()
diag.ok("Outlook", "9 priority emails, 8 actions")
diag.fail("Audio", "NoAudioReceived: edge-tts returned no audio")
degraded = diag.steps()
degraded_html = oe._build_diag_block(degraded)

diag.reset()
diag.fail("Audio", "first")
diag.ok("Audio", "second")          # a step reports once; last wins
relast = diag.steps()

diag.reset()
diag.fail("Outlook", "<script>alert(1)</script>")
escaped_html = oe._build_diag_block(diag.steps())

diag.reset()
diag.ok("Audio", "x" * 900)
long_detail = diag.steps()[0]["detail"]

CHECKS = [
    ("a healthy run records no failure",
     not any(s["status"] == "failed" for s in healthy)),
    ("a failed step is recorded with its reason",
     any(s["status"] == "failed" and "NoAudioReceived" in s["detail"]
         for s in degraded)),
    ("the failure reaches the email body",
     "NoAudioReceived" in degraded_html),
    ("a degraded run says so at the top of the block",
     "incomplete" in degraded_html and "Audio did not run" in degraded_html),
    ("a healthy run carries no alarming banner",
     "incomplete" not in healthy_html),
    ("a healthy run still reports what ran",
     "Outlook" in healthy_html and "Audio" in healthy_html),
    ("no diagnostics means no block at all (old callers unaffected)",
     oe._build_diag_block(None) == "" and oe._build_diag_block([]) == ""),
    ("a step reports once, last write wins",
     len(relast) == 1 and relast[0]["status"] == "ok"),
    ("detail is escaped, not injected into the email",
     "<script>" not in escaped_html and "&lt;script&gt;" in escaped_html),
    ("detail is capped so a huge traceback can't bloat the email",
     len(long_detail) <= 300),
    ("degraded() reflects the recorded steps",
     (diag.reset() or diag.fail("X", "y") or diag.degraded()) is True),
]

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    bad = 0
    for label, okk in CHECKS:
        print(("ok   " if okk else "FAIL ") + label)
        bad += not okk
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} passed")
    sys.exit(1 if bad else 0)
