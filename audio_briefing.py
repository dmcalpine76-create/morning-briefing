"""
audio_briefing.py  —  Spoken MP3 edition of the morning briefing  (D12)
------------------------------------------------------------------------
Turns the day's top stories, personal topic highlights and work actions
into a ~4 minute spoken briefing, rendered to briefing.mp3 with a
configurable voice and attached to the morning email by briefing.py.

Pipeline:
  1. Claude (Haiku) writes a flowing radio-style script from the top
     stories + personal topics + email actions (falls back to a simple
     template if the API call fails).
  2. edge-tts renders it with a Microsoft neural TTS voice (free,
     no API key needed).

Voice selection:
  Set AUDIO_VOICE in your .env or as a GitHub secret to any edge-tts
  voice name. Some good options:

    AUSTRALIAN
      en-AU-WilliamNeural    Male, Australian (default)
      en-AU-NatashaNeural    Female, Australian

    BRITISH
      en-GB-RyanNeural       Male, British
      en-GB-SoniaNeural      Female, British
      en-GB-ThomasNeural     Male, British (older)

    AMERICAN
      en-US-GuyNeural        Male, American
      en-US-JennyNeural      Female, American
      en-US-DavisNeural      Male, American (conversational)
      en-US-AriaNeural       Female, American (expressive)
      en-US-AndrewNeural     Male, American (newer)

    OTHER
      en-NZ-MitchellNeural   Male, New Zealand
      en-NZ-MollyNeural      Female, New Zealand
      en-IE-ConnorNeural     Male, Irish
      en-ZA-LukeNeural       Male, South African

  Full list:  py -c "import edge_tts,asyncio;[print(v['ShortName'],v['Gender']) for v in asyncio.run(edge_tts.list_voices()) if v['Locale'].startswith('en')]"

Requirements:
    pip install edge-tts        (add `edge-tts` to requirements.txt)

briefing.py imports this module inside a try/except — if edge-tts isn't
installed, the briefing simply skips the audio edition.
"""

import os
import asyncio
import datetime
from pathlib import Path

import edge_tts   # hard requirement — briefing.py guards the import

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# Voice — override via AUDIO_VOICE env var or .env
DEFAULT_VOICE = "en-AU-WilliamNeural"
# `or` (not a .get default) so an empty GitHub secret also falls back;
# strip removes stray spaces/newlines/quotes pasted into the secret.
VOICE      = (os.environ.get("AUDIO_VOICE") or DEFAULT_VOICE).strip().strip('"').strip("'") or DEFAULT_VOICE
RATE       = "+4%"          # slightly brisk, radio-news pace
MAX_MP3_MB = 8              # sanity cap for the email attachment
# GitHub Actions runners use UTC — the 5am/8am AEST runs are still
# "yesterday" in UTC, so always take the date from Brisbane time.
BRISBANE_TZ = datetime.timezone(datetime.timedelta(hours=10))   # QLD has no daylight saving


def _collect_material(sections: dict, analysis: dict,
                      active_topics: list = None,
                      topic_stories: dict = None) -> str:
    """Flatten the day's content into raw material for the script writer."""
    lines = []

    # ── News categories: all summarised stories (top 5 per category) ──
    for cat, stories in (sections or {}).items():
        for s in (stories or [])[:5]:
            sig = s.get("significance") or ""
            lines.append(f"[{cat}] ({sig}) {s.get('headline') or ''} — "
                         f"{(s.get('summary') or '')[:220]}")

    # ── Personal topics: top 3 per topic ──
    if active_topics and topic_stories:
        lines.append("")
        lines.append("PERSONAL WATCH TOPICS:")
        for topic in (active_topics or []):
            tid = topic.get("id", "")
            name = topic.get("name", tid)
            stories = (topic_stories or {}).get(tid, [])
            if not stories:
                continue
            for s in stories[:3]:
                sig = s.get("significance") or ""
                lines.append(f"[Topic: {name}] ({sig}) "
                             f"{s.get('headline') or ''} — "
                             f"{(s.get('summary') or '')[:220]}")

    # ── Work actions ──
    actions = (analysis or {}).get("actions", [])
    if actions:
        lines.append("")
        lines.append(f"WORK ACTIONS TODAY ({len(actions)}):")
        for a in actions[:6]:
            lines.append(f"- {a.get('task', a.get('title', a.get('action', '')))} "
                         f"(urgency: {a.get('urgency', a.get('priority', 'normal'))})")

    # ── Priority emails ──
    digest = (analysis or {}).get("digest", [])
    if digest:
        lines.append("")
        lines.append("PRIORITY EMAILS:")
        for d in digest[:4]:
            lines.append(f"- From {d.get('from', d.get('from_name', ''))}: "
                         f"{d.get('subject', '')}")

    return "\n".join(lines)


def _write_script(material: str, generated_at: datetime.datetime,
                  api_key: str) -> str:
    """Ask Haiku for a flowing spoken script; fall back to a plain read-out."""
    day = generated_at.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    date_spoken = f"{generated_at.strftime('%A')} the {day}{suffix} of {generated_at.strftime('%B')}"
    if _ANTHROPIC_AVAILABLE and api_key:
        try:
            client = _anthropic.Anthropic(api_key=api_key)
            prompt = f"""Write a spoken morning briefing script for Doug McAlpine, who works
at State Gas, a junior Queensland gas explorer focused on the Taroom Trough.
It will be read aloud by a text-to-speech voice, so write for the EAR:

- Open with: "Good morning Doug, it's {date_spoken}. Here's your briefing."
- 800 to 1000 words total (about six minutes spoken)
- Flowing conversational prose. No headings, no bullet points, no asterisks,
  no emoji, no URLs, and never spell out ticker codes letter by letter —
  say the company name instead.
- Structure the briefing in this order:
  1. Headlines: the day's biggest NEW news across all three news sections
     (International News, Australian News, Australian Finance & Markets).
     Cover at least two stories from EACH section, leading with anything
     marked critical or major, whatever the subject.
  2. Markets & business: the key Australian finance and markets stories.
  3. Watch topics: transition with something like "Now, to your watch
     topics." Cover the most significant items from the PERSONAL WATCH
     TOPICS section (energy and gas, AI, and the others), giving priority
     to anything touching Queensland gas, the Taroom Trough or State Gas,
     Comet Ridge, Beach Energy, Santos or Blue Energy. About two minutes.
     Don't repeat a story already covered — just note the connection.
  4. Today's work actions, most urgent first (skip if none).
  5. A one-line sign-off.
- Energy and gas must NOT dominate the headlines segment — give broad
  news its fair share.

TODAY'S MATERIAL:
{material}

Respond with ONLY the script text."""
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2200,
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
                      .replace("- ", ". ").replace("\n", " ")[:4500]
            + " That's your briefing for this morning. Have a good day.")


async def _render(script: str, mp3_path: Path) -> None:
    try:
        await edge_tts.Communicate(script, VOICE, rate=RATE).save(str(mp3_path))
    except edge_tts.exceptions.NoAudioReceived:
        if VOICE == DEFAULT_VOICE:
            raise
        # Usually an invalid AUDIO_VOICE name — retry once with the default
        print(f"   ⚠️  Voice '{VOICE}' returned no audio "
              f"(len {len(VOICE)}) — retrying with {DEFAULT_VOICE}")
        mp3_path.unlink(missing_ok=True)
        await edge_tts.Communicate(script, DEFAULT_VOICE, rate=RATE).save(str(mp3_path))


def generate_mp3(sections: dict, analysis: dict, out_dir: Path,
                 api_key: str = "",
                 active_topics: list = None,
                 topic_stories: dict = None) -> Path | None:
    """
    Main entry point — called by briefing.py.
    Returns the Path to briefing.mp3, or None if anything went wrong
    (the briefing itself must never fail because of the audio edition).
    """
    try:
        material = _collect_material(sections, analysis,
                                     active_topics, topic_stories)
        if not material.strip():
            print("   ⚠️  No material for audio briefing — skipping")
            return None
        now = datetime.datetime.now(BRISBANE_TZ)
        script = _write_script(material, now, api_key)
        mp3_path = out_dir / "briefing.mp3"
        print(f"   🎙️  Voice: {VOICE}  Rate: {RATE}")
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
    import sys
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
        demo_topics = [{"id": "crimson-desert", "name": "Crimson Desert",
                        "emoji": "🎮"}]
        demo_topic_stories = {"crimson-desert": [{
            "headline": "Crimson Desert release date confirmed",
            "summary": "Pearl Abyss announces Q4 2026 launch window.",
            "significance": "major",
        }]}
        p = generate_mp3(demo_sections, demo_analysis, Path("."),
                         os.environ.get("ANTHROPIC_API_KEY", ""),
                         demo_topics, demo_topic_stories)
        print(f"Result: {p}")
    else:
        print("Usage: py audio_briefing.py test")
        print(f"Current voice: {VOICE}")
        print("Set AUDIO_VOICE env var or in .env to change")
