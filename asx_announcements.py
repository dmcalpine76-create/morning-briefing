"""
asx_announcements.py — ASX announcement monitor for watchlist tickers
----------------------------------------------------------------------
Uses Claude claude-haiku-4-5-20251001 with web_search to find ASX announcements
released in the last 24 hours for each ticker in the watchlist.

Called from briefing.py — results displayed in the Work Actions tab.
No external API keys needed beyond ANTHROPIC_API_KEY.
"""

import os
import json
import datetime
import anthropic
from dotenv import load_dotenv

load_dotenv()

WATCHLIST = [
    ("GAS", "State Gas"),
    ("COI", "Comet Ridge"),
    ("BPT", "Beach Energy"),
    ("STO", "Santos"),
    ("ARA", "Arura"),
    ("BLU", "Blue Star Helium"),
]


def get_asx_announcements(client: anthropic.Anthropic = None) -> dict:
    """
    Search for ASX announcements from the last 24 hours for each watchlist ticker.
    Returns {"announcements": [...], "generated_at": ISO string}
    """
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"announcements": [], "error": "ANTHROPIC_API_KEY not set"}
        client = anthropic.Anthropic(api_key=api_key)

    today     = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    # ── Step 1: Search for announcements (free-form, with web search) ─────────
    search_prompt = f"""Search the web for ASX company announcements released in the last 24 hours for these companies:

{chr(10).join(f'- ASX:{t} ({n})' for t, n in WATCHLIST)}

Today is {today.strftime('%A %d %B %Y')}. Search for announcements from {yesterday.strftime('%d %B')} and {today.strftime('%d %B %Y')}.

For each company, search: "ASX [ticker] announcement {today.strftime('%B %Y')}"

Collect all announcements you find and summarise what each one says."""

    try:
        # Step 1 — let Claude search freely
        search_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": search_prompt}],
        )

        # Collect the full assistant response including tool results
        search_text = ""
        for block in search_resp.content:
            if hasattr(block, "text"):
                search_text += block.text

        if not search_text.strip():
            # Model only did tool calls — extract what it found
            search_text = "Search completed."

        # ── Step 2: Format as JSON (no web search, just formatting) ──────────
        format_prompt = f"""Based on this research about ASX announcements:

{search_text}

Now return ONLY a JSON object listing any ASX announcements found from the last 24 hours 
(since {yesterday.strftime('%d %B %Y')}) for these tickers: {', '.join(t for t, _ in WATCHLIST)}

Use this exact format:
{{
  "announcements": [
    {{
      "ticker": "GAS",
      "company": "State Gas",
      "headline": "exact announcement title",
      "summary": "1-2 sentences explaining what the announcement says and why it matters",
      "date": "{today.isoformat()}",
      "is_price_sensitive": true
    }}
  ]
}}

Rules:
- Only include announcements actually from the last 24 hours
- If none found for a ticker, do not include it
- If no announcements found at all, return {{"announcements": []}}
- Return ONLY the JSON — no explanation, no markdown fences"""

        format_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": format_prompt}],
        )

        raw = ""
        for block in format_resp.content:
            if hasattr(block, "text"):
                raw += block.text

        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        data = json.loads(raw)
        announcements = data.get("announcements", [])

        print(f"   ✓ ASX announcements: {len(announcements)} found")
        for a in announcements:
            ps = " ⚡" if a.get("is_price_sensitive") else ""
            print(f"     [{a['ticker']}] {a['headline'][:65]}{ps}")

        return {
            "announcements": announcements,
            "generated_at":  datetime.datetime.now().isoformat(),
        }

    except json.JSONDecodeError as e:
        return {"announcements": [], "error": f"Could not parse JSON response: {e}"}
    except Exception as e:
        return {"announcements": [], "error": f"Error: {e}"}


if __name__ == "__main__":
    print("Fetching ASX announcements…")
    result = get_asx_announcements()

    if result.get("error"):
        print(f"⚠️  {result['error']}")
    else:
        announcements = result.get("announcements", [])
        if not announcements:
            print("No announcements found in the last 24 hours for watchlist tickers")
        else:
            print(f"\n{len(announcements)} announcement(s) found:")
            for a in announcements:
                ps = " ⚡ PRICE SENSITIVE" if a.get("is_price_sensitive") else ""
                print(f"\n  [{a['ticker']}] {a['company']}{ps}")
                print(f"  {a['headline']}")
                print(f"  {a['summary']}")
