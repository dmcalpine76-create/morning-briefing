"""
test_audio_script_rules.py
--------------------------
The spoken briefing was inventing relationships between diary items - matching
tasks to gaps, guessing at agendas, explaining why one meeting followed another
- and padding each commitment with commentary on how the day should feel. The
prompt asked for all of it: "call out the gaps as opportunities", "name what
could go in the longest one", "take as long as this needs".

These check the instructions that replaced it are still there, because the
failure mode is a quiet edit to the prompt rather than a crash.

    py tests/test_audio_script_rules.py
"""
import sys, re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "audio_briefing.py").read_text(encoding="utf-8")

MUST_BE_GONE = [
    "call out the gaps as opportunities",
    "name what could go in the longest one",
    "Take as long as this needs",
    "must finish on time because of what follows it",
    "800 to 1000 words",
]

MUST_BE_PRESENT = [
    "TWO TO THREE SENTENCES",
    "never infer",
    "connect one commitment to another",
    "Never match a task to a meeting",
    "No commentary on how Doug should feel",
    "SEPARATE LIST",
]

voice = re.search(r'DEFAULT_VOICE\s*=\s*"([^"]+)"', SRC)
words = re.search(r'(\d{3}) to (\d{3}) words', SRC)

CHECKS = [(f"gone: {p[:38]}", p not in SRC) for p in MUST_BE_GONE]
CHECKS += [(f"present: {p[:38]}", p in SRC) for p in MUST_BE_PRESENT]
CHECKS += [
    ("default voice is a deep American male",
     voice is not None and voice.group(1) == "en-US-ChristopherNeural"),
    ("target length is shorter than the old six minutes",
     words is not None and int(words.group(2)) <= 800),
    ("the emotive-language ban lists concrete words",
     all(w in SRC for w in ("busy", "encouragement", "don't forget to"))),
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
