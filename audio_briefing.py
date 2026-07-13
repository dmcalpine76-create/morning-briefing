"""
audio_briefing.py  —  Spoken MP3 edition of the morning briefing  (D12)
------------------------------------------------------------------------
Turns the day's top stories and work actions into a ~3 minute spoken
briefing, rendered to briefing.mp3 with a natural Australian voice and
attached to the morning email by briefing.py.

Pipeline:
  1. Claude (Haiku) writes a flowing radio-style script from the top
     stories + email actions (falls back to a simple template if the
     API call fails).
  2. edge-tts renders it with the en-AU-WilliamNeural voice (free
     Microsoft neural TTS — no API key needed).

Requirements:
    pip install edge-tts        (add `edge-tts` to requirements.txt)

briefing.py imports this module inside a try/except — if edge-tts isn't
installed, the briefing simply skips the audio edition.
"""

import asyncio
import datetime
from pathlib import Path

import edge_tts   # hard requirement — briefing.py guards the import

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

VOICE    = "en-AU-WilliamNeural"
RATE     = "+4%"          # slightly brisk, radio-news pace
MAX_MP3_MB = 8            # sanity cap for the email attachment


def _collect_material(sections: dict, analysis: dict) -> str:
    """Flatten the day's content into raw material for the script writer."""
    lines = []
    for cat, stories in (sections or {}).items():
        for s in (stories or [])[:3]:
            sig = s.get("significance", "")
            lines.append(f"[{cat}] ({sig}) {s.get('headline','')} — {s.get('summary','')[:220]}")
    actions = (analysis or {}).get("actions", [])
    if actions:
        lines.append("")
        lines.append(f"WORK ACTIONS TODAY ({len(actions)}):")
        for a in actions[:6]:
            lines.append(f"- {a.get('task', a.get('title',''))} "
                         f"(urgency: {a.get('urgency','normal')})")
    digest = (analysis or {}).get("digest", [])
    if digest:
        lines.append("")
        lines.append("PRIORITY EMAILS:")
        for d in digest[:4]:
            lines.append(f"- From {d.get('from','')}: {d.get('subject','')}")
    return "\n".join(lines)


def _write_script(material: str, generated_at: datetime.datetime,
                  api_key: str) -> str:
    """Ask Haiku for a flowing spoken script; fall back to a plain read-out."""
    date_spoken = generated_at.strftime("%A the %d of %B")
    if _ANTHROPIC_AVAILABLE and api_key:
        try:
            client = _anthropic.Anthropic(api_key=api_key)
            prompt = f"""Write a spoken morning briefing script for Doug McAlpine, who works
at State Gas, a junior Queensland gas explorer focused on the Taroom Trough.
It will be read aloud by a text-to-speech voice, so write for the EAR:

- Open with: "Good morning Doug, it's {date_spoken}. Here's your briefing."
- 400 to 550 words total (about three minutes spoken)
- Flowing conversational prose. No headings, no bullet points, no asterisks,
  no emoji, no URLs, and never spell out ticker codes letter by letter —
  say the company name instead.
- Prioritise: gas & energy news first (especially anything touching
  Queensland gas, the Taroom Trough or his watchlist companies), then AI,
  then one or two other notable stories, then Manchester United or gaming
  only if genuinely interesting.
- Finish with a quick run-through of today's work actions, most urgent
  first, then a one-line sign-off.

TODAY'S MATERIAL:
{material}

Respond with ONLY the script text."""
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
                timeout=90,
            )
            script = resp.content[0].text.strip()
            if len(script) > 200:
                return script
        except Exception as e:
            print(f"   ⚠️  Audio script generation failed, using fallback: {e}")

    # Plain fallback — just read the material out
    return (f"Good morning Doug, it's {date_spoken}. Here's your briefing. "
            + material.replace("[", " In ").replace("]", ": ")
                      .replace("- ", ". ").replace("\n", " ")[:3500]
            + " That's your briefing for this morning. Have a good day.")


async def _render(script: str, mp3_path: Path) -> None:
    communicate = edge_tts.Communicate(script, VOICE, rate=RATE)
    await communicate.save(str(mp3_path))


def generate_mp3(sections: dict, analysis: dict, out_dir: Path,
                 api_key: str = "") -> Path | None:
    """
    Main entry point — called by briefing.py.
    Returns the Path to briefing.mp3, or None if anything went wrong
    (the briefing itself must never fail because of the audio edition).
    """
    try:
        material = _collect_material(sections, analysis)
        if not material.strip():
            print("   ⚠️  No material for audio briefing — skipping")
            return None
        now = datetime.datetime.now()
        script = _write_script(material, now, api_key)
        mp3_path = out_dir / "briefing.mp3"
        asyncio.run(_render(script, mp3_path))
        if not mp3_path.exists() or mp3_path.stat().st_size < 10_000:
            print("   ⚠️  Audio render produced no usable file — skipping")
            return None
        if mp3_path.stat().st_size > MAX_MP3_MB * 1024 * 1024:
            print("   ⚠️  Audio file too large to attach — skipping")
            return None
        return mp3_path
    except Exception as e:
        print(f"   ⚠️  Audio briefing error: {e}")
        return None


if __name__ == "__main__":
    import os, sys
    from dotenv import load_dotenv
    load_dotenv()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo_sections = {"Energy & Gas": [{
            "headline": "Queensland gas exploration accelerates",
            "summary":  "Several Taroom Trough players announced expanded drilling programs.",
            "significance": "major",
        }]}
        demo_analysis = {"actions": [{"task": "Review the drilling contract",
                                      "urgency": "high"}]}
        p = generate_mp3(demo_sections, demo_analysis, Path("."),
                         os.environ.get("ANTHROPIC_API_KEY", ""))
        print(f"Result: {p}")
    else:
        print("Usage: py audio_briefing.py test")
