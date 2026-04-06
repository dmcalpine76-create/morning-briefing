#!/usr/bin/env python3
"""
manage_topics.py  —  Add, remove and list your personal watch topics
---------------------------------------------------------------------
Usage:
    python manage_topics.py list
    python manage_topics.py add
    python manage_topics.py remove <topic-id>
    python manage_topics.py toggle <topic-id>    # pause/resume a topic
    python manage_topics.py show   <topic-id>    # show full details

Topics are saved to topics.json and picked up automatically
the next time briefing.py runs.
"""

import json
import sys
import re
import datetime
from pathlib import Path

TOPICS_FILE = Path(__file__).parent / "topics.json"

# A palette of colors to cycle through for new topics
COLOR_PALETTE = [
    "#1a3a5c", "#3a1a4a", "#1a4a2e", "#4a2a1a",
    "#1a3a4a", "#3a3a1a", "#4a1a2a", "#1a4a4a",
    "#2a1a4a", "#1a2a4a", "#4a1a4a", "#2a4a1a",
]

EMOJI_SUGGESTIONS = [
    "🔬 Science", "💊 Health", "⚖️  Law", "🏛️  Politics", "🏗️  Infrastructure",
    "🚀 Space", "🛡️  Defence", "🌏 Geopolitics", "💡 Technology", "🏦 Banking",
    "🏠 Property", "🌾 Agriculture", "🚗 Automotive", "✈️  Aviation", "⚓ Shipping",
    "🎓 Education", "🎭 Culture", "🏋️  Sport", "💰 Commodities", "🔐 Cybersecurity",
]


def load() -> dict:
    if not TOPICS_FILE.exists():
        data = {"topics": []}
        save(data)
        return data
    return json.loads(TOPICS_FILE.read_text(encoding='utf-8'))


def save(data: dict):
    data["_comment"] = "Your personal watch topics. Edit directly or use: python manage_topics.py"
    TOPICS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "-", text)


def next_color(topics: list) -> str:
    used = {t.get("color") for t in topics}
    for c in COLOR_PALETTE:
        if c not in used:
            return c
    return COLOR_PALETTE[len(topics) % len(COLOR_PALETTE)]


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

def cmd_list():
    data   = load()
    topics = data.get("topics", [])
    if not topics:
        print("\n  No watch topics yet.  Run: python manage_topics.py add\n")
        return

    print(f"\n  {'STATUS':<8} {'ID':<28} {'NAME':<30} KEYWORDS")
    print("  " + "─" * 90)
    for t in topics:
        status  = "✓ Active" if t.get("active", True) else "⏸ Paused"
        kws     = ", ".join(t.get("keywords", [])[:4])
        if len(t.get("keywords", [])) > 4:
            kws += f" (+{len(t['keywords'])-4} more)"
        print(f"  {status:<8}  {t['id']:<28} {t['emoji']} {t['name']:<28} {kws}")
    print()


def cmd_show(topic_id: str):
    data  = load()
    topic = next((t for t in data["topics"] if t["id"] == topic_id), None)
    if not topic:
        print(f"\n  ❌  Topic '{topic_id}' not found. Run 'list' to see all topics.\n")
        return
    print(f"""
  {topic['emoji']}  {topic['name']}
  {"─" * 50}
  ID:       {topic['id']}
  Status:   {"Active ✓" if topic.get('active', True) else "Paused ⏸"}
  Color:    {topic['color']}
  Added:    {topic.get('added', '—')}
  Notes:    {topic.get('notes', '—')}
  Keywords: {", ".join(topic.get('keywords', []))}
""")


def cmd_add():
    data   = load()
    topics = data.get("topics", [])

    print("\n  ── Add a new watch topic ──────────────────────────\n")

    # Name
    while True:
        name = input("  Topic name (e.g. 'Quantum Computing'): ").strip()
        if name:
            break
        print("  Name cannot be empty.")

    topic_id = slugify(name)
    if any(t["id"] == topic_id for t in topics):
        print(f"\n  ⚠️  A topic with id '{topic_id}' already exists. Aborting.\n")
        return

    # Emoji
    print(f"\n  Emoji suggestions:")
    for i in range(0, len(EMOJI_SUGGESTIONS), 4):
        row = EMOJI_SUGGESTIONS[i:i+4]
        print("    " + "   ".join(f"{e}" for e in row))
    emoji = input(f"\n  Emoji (press Enter for 📌): ").strip() or "📌"
    # Take just the first character if they typed extra
    emoji = emoji.split()[0] if emoji else "📌"

    # Keywords
    print(f"\n  Keywords — comma-separated search terms that define this topic.")
    print(f"  Tip: include variations, abbreviations, key organisations/people.")
    kw_input = input(f"  Keywords: ").strip()
    keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
    if not keywords:
        keywords = [name.lower()]

    # Notes
    notes = input(f"\n  Notes / focus (optional, press Enter to skip): ").strip()

    # Confirm
    color = next_color(topics)
    print(f"""
  ── Summary ───────────────────────────────────────
  Name:     {emoji}  {name}
  ID:       {topic_id}
  Keywords: {", ".join(keywords)}
  Notes:    {notes or "(none)"}
  ──────────────────────────────────────────────────""")

    confirm = input("\n  Save this topic? [Y/n]: ").strip().lower()
    if confirm in ("n", "no"):
        print("  Cancelled.\n")
        return

    topics.append({
        "id":       topic_id,
        "name":     name,
        "emoji":    emoji,
        "color":    color,
        "keywords": keywords,
        "notes":    notes,
        "added":    datetime.date.today().isoformat(),
        "active":   True,
    })
    data["topics"] = topics
    save(data)
    print(f"\n  ✅  Topic '{name}' added. It will appear in your next briefing.\n")


def cmd_remove(topic_id: str):
    data   = load()
    topics = data.get("topics", [])
    match  = next((t for t in topics if t["id"] == topic_id), None)
    if not match:
        print(f"\n  ❌  Topic '{topic_id}' not found.\n")
        return
    confirm = input(f"\n  Remove '{match['name']}'? This cannot be undone. [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Cancelled.\n")
        return
    data["topics"] = [t for t in topics if t["id"] != topic_id]
    save(data)
    print(f"\n  ✅  '{match['name']}' removed.\n")


def cmd_toggle(topic_id: str):
    data   = load()
    topics = data.get("topics", [])
    match  = next((t for t in topics if t["id"] == topic_id), None)
    if not match:
        print(f"\n  ❌  Topic '{topic_id}' not found.\n")
        return
    match["active"] = not match.get("active", True)
    status = "Active ✓" if match["active"] else "Paused ⏸"
    save(data)
    print(f"\n  ✅  '{match['name']}' is now: {status}\n")


def usage():
    print("""
  Usage:
    python manage_topics.py list
    python manage_topics.py add
    python manage_topics.py remove <topic-id>
    python manage_topics.py toggle <topic-id>
    python manage_topics.py show   <topic-id>
""")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        usage()
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "add":
        cmd_add()
    elif args[0] == "remove" and len(args) >= 2:
        cmd_remove(args[1])
    elif args[0] == "toggle" and len(args) >= 2:
        cmd_toggle(args[1])
    elif args[0] == "show" and len(args) >= 2:
        cmd_show(args[1])
    else:
        print(f"\n  ❌  Unknown command: {' '.join(args)}")
        usage()
