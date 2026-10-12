"""
run_diag.py  —  what actually happened on this run.

The 13 October briefing lost its MP3 and every Outlook-derived section, and
the only record of why was a line briefing.py printed and swallowed:

    except Exception as e:
        print(f"   WARNING  Audio briefing failed: {e}")

That line goes to the Actions log, which is served from a host the egress
policy blocks, so from outside CI it may as well not exist. The workflow was
green, the email arrived, and the only symptom was an attachment that wasn't
there.

So each step records its outcome here and the email carries the result. The
email is the right channel rather than a file in the repo: the repo is public
and this touches mailbox state.

Counts and status only - never a subject, sender or task title.

    import run_diag as diag
    diag.ok("Outlook", "9 priority emails, 8 actions")
    diag.fail("Audio", "NoAudioReceived from edge-tts")
    diag.degraded()        -> True if anything failed
"""

OK      = "ok"
FAILED  = "failed"
SKIPPED = "skipped"

_steps: list[dict] = []


def reset() -> None:
    _steps.clear()


def _add(step: str, status: str, detail: str = "") -> None:
    for s in _steps:                      # a step reports once; last wins
        if s["step"] == step:
            s["status"], s["detail"] = status, str(detail)[:300]
            return
    _steps.append({"step": step, "status": status, "detail": str(detail)[:300]})


def ok(step: str, detail: str = "") -> None:
    _add(step, OK, detail)


def fail(step: str, detail: str = "") -> None:
    _add(step, FAILED, detail)


def skip(step: str, detail: str = "") -> None:
    _add(step, SKIPPED, detail)


def steps() -> list:
    return [dict(s) for s in _steps]


def degraded() -> bool:
    return any(s["status"] == FAILED for s in _steps)


def failures() -> list:
    return [dict(s) for s in _steps if s["status"] == FAILED]


def as_text() -> str:
    """Plain-text form, for the console at the end of a run."""
    mark = {OK: "ok", FAILED: "FAILED", SKIPPED: "skipped"}
    return "\n".join(
        f"  {mark[s['status']]:<8} {s['step']}"
        + (f" - {s['detail']}" if s["detail"] else "")
        for s in _steps
    )
