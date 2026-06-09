"""
asx_announcements.py — ASX announcement monitor for watchlist tickers
----------------------------------------------------------------------
Uses Claude claude-haiku-4-5-20251001 with web_search to find ASX announcements
released in the last 24 hours for each ticker in the watchlist.

Called from briefing.py — results displayed in the Work Actions tab
in place of the Gmail Actions column.

No external API keys needed beyond ANTHROPIC_API_KEY.
"""

import os
import json
import datetime
import anthropic
from dotenv import load_dotenv

load_dotenv()

# Watchlist — must match the tickers in briefing.py
WATCHLIST = [
    ("GAS", "State Gas"),
    ("COI", "Comet Ridge"),
    ("BPT", "Beach Energy"),
    ("STO", "Santos"),
    ("ARA", "Arura"),
    ("BLU", "Blue Star Helium"),
]

MAX_RETRIES = 2


def get_asx_announcements(client: anthropic.Anthropic = None) -> dict:
    """
    Search for ASX announcements from the last 24 hours for each watchlist ticker.
    Returns {"announcements": [...], "generated_at": ISO string}
    Each announcement: {ticker, company, headline, summary, date, is_price_sensitive}
    """
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"announcements": [], "error": "ANTHROPIC_API_KEY not set"}
        client = anthropic.Anthropic(api_key=api_key)

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    ticker_list = ", ".join(f"{t} ({n})" for t, n in WATCHLIST)

    prompt = f"""Today is {today.strftime('%A %d %B %Y')}.

Search the web for ASX company announcements released in the last 24 hours 
(i.e. since {yesterday.strftime('%d %B %Y')}) for these ASX-listed companies:

{ticker_list}

Search for each company individually using queries like:
- "ASX GAS State Gas announcement {today.strftime('%d %B %Y')}"
- "ASX COI Comet Ridge announcement"
- etc.

Focus on finding official ASX market announcements — quarterly reports, 
trading halts, capital raises, exploration results, board changes, etc.

Return ONLY valid JSON with this structure:
{{
  "announcements": [
    {{
      "ticker": "GAS",
      "company": "State Gas",
      "headline": "exact announcement headline",
      "summary": "1-2 sentences: what the announcement says and why it matters to shareholders",
      "date": "{today.isoformat()}",
      "is_price_sensitive": true
    }}
  ]
}}

Only include announcements actually released in the last 24 hours.
If no announcement found for a ticker in the last 24 hours, do not include it.
If no announcements found for any ticker, return {{"announcements": []}}
Return ONLY the JSON — no markdown, no explanation."""

    for attempt in range(MAX_RETRIES):
        try:
            messages = [{"role": "user", "content": prompt}]

            # Keep calling until we get a text response (web_search may need multiple turns)
            for turn in range(5):
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=2000,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=messages,
                )

                # Collect text and check if done
                text_parts = []
                tool_uses = []
                for block in resp.content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        tool_uses.append(block)

                if resp.stop_reason == "end_turn" and text_parts:
                    raw = " ".join(text_parts).strip()
                    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    try:
                        data = json.loads(raw)
                        announcements = data.get("announcements", [])
                        print(f"   ✓ ASX announcements: {len(announcements)} found")
                        for a in announcements:
                            ps = " ⚡" if a.get("is_price_sensitive") else ""
                            print(f"     [{a['ticker']}] {a['headline'][:60]}{ps}")
                        return {
                            "announcements": announcements,
                            "generated_at": datetime.datetime.now().isoformat(),
                        }
                    except json.JSONDecodeError:
                        if attempt < MAX_RETRIES - 1:
                            break  # retry outer loop
                        return {"announcements": [], "error": f"Could not parse response: {raw[:100]}"}

                elif resp.stop_reason == "tool_use":
                    # Continue the conversation with tool results
                    messages.append({"role": "assistant", "content": resp.content})
                    tool_results = []
                    for tu in tool_uses:
                        # web_search results come back via the API automatically
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": "Search completed."
                        })
                    messages.append({"role": "user", "content": tool_results})
                else:
                    break

        except anthropic.APIError as e:
            if attempt < MAX_RETRIES - 1:
                continue
            return {"announcements": [], "error": f"API error: {e}"}
        except Exception as e:
            return {"announcements": [], "error": f"Error: {e}"}

    return {"announcements": [], "error": "Max retries reached"}


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
            print(f"\n{len(announcements)} announcement(s):")
            for a in announcements:
                ps = " ⚡ PRICE SENSITIVE" if a.get("is_price_sensitive") else ""
                print(f"\n  [{a['ticker']}] {a['company']}{ps}")
                print(f"  {a['headline']}")
                print(f"  {a['summary']}")
