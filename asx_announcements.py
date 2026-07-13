"""
asx_announcements.py  —  Overnight ASX announcements for the watchlist (D4)
----------------------------------------------------------------------------
Fetches recent company announcements for each watchlist ticker from the
public ASX JSON endpoint, flags price-sensitive items, and uses Claude
(Haiku) to write a one-line plain-English summary for each headline.

briefing.py already imports this module and renders the results as the
"ASX Announcements" column on the Work Actions tab — this file completes
that integration.

Returns (from get_asx_announcements):
    {
      "announcements": [
          {ticker, company, headline, summary, is_price_sensitive,
           date, url}
      ],
      "generated_at": "<iso timestamp>",
      "error": "" | "<message>",
    }

Usage:
    py asx_announcements.py test    — print announcements to console
    import asx_announcements        — used by briefing.py
"""

import os
import json
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# Watchlist — kept in sync with briefing.py's ASX_WATCHLIST plus the
# extra tickers Doug tracks. Edit here or override via briefing_settings.json
# (briefing.py passes nothing in, so this list is the source of truth).
WATCHLIST = ["GAS", "COI", "BPT", "STO", "ARA", "BLU"]

COMPANY_NAMES = {
    "GAS": "State Gas",
    "COI": "Comet Ridge",
    "BPT": "Beach Energy",
    "STO": "Santos",
    "ARA": "Ariadne Australia",
    "BLU": "Blue Energy",
}

MAX_PER_STOCK   = 6      # fetch up to this many recent announcements per code
HOURS_BACK      = 30     # include announcements from the last N hours
REQUEST_TIMEOUT = 12
AEST_OFFSET     = datetime.timezone(datetime.timedelta(hours=10))

# The ASX website's public JSON API. Unofficial but long-standing; every
# call is wrapped so a change on their side degrades to an empty column,
# never a failed briefing run.
ASX_API = "https://www.asx.com.au/asx/1/company/{code}/announcements"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


def _fetch_for_code(code: str) -> list[dict]:
    """Fetch recent announcements for one ticker. Returns [] on any failure."""
    try:
        resp = requests.get(
            ASX_API.format(code=code),
            params={"count": MAX_PER_STOCK, "market_sensitive": "false"},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"   ⚠️  ASX {code}: {e}")
        return []

    cutoff = datetime.datetime.now(AEST_OFFSET) - datetime.timedelta(hours=HOURS_BACK)
    out = []
    for a in data.get("data", []):
        # release_date format e.g. "2026-07-13T08:31:00+1000"
        raw_date = a.get("document_release_date", a.get("release_date", ""))
        try:
            rel = datetime.datetime.fromisoformat(raw_date)
            if rel.tzinfo is None:
                rel = rel.replace(tzinfo=AEST_OFFSET)
        except Exception:
            rel = None
        if rel and rel < cutoff:
            continue
        out.append({
            "ticker":             code,
            "company":            COMPANY_NAMES.get(code, code),
            "headline":           (a.get("header") or a.get("headline") or "").strip(),
            "is_price_sensitive": bool(a.get("market_sensitive")
                                       or a.get("price_sensitive")),
            "date":               (rel or datetime.datetime.now(AEST_OFFSET)).isoformat(),
            "url":                a.get("url", ""),
            "summary":            "",
        })
    return out


def _summarise(client, announcements: list[dict]) -> None:
    """One Haiku call writes a one-liner per headline. Mutates in place."""
    if not announcements or not _ANTHROPIC_AVAILABLE or client is None:
        return
    lines = "\n".join(
        f"[{i+1}] {a['ticker']} ({a['company']}): {a['headline']}"
        f"{' [PRICE SENSITIVE]' if a['is_price_sensitive'] else ''}"
        for i, a in enumerate(announcements)
    )
    prompt = f"""You are a sharp equities analyst briefing Doug McAlpine, who works at
State Gas (GAS.AX), a junior Queensland gas explorer focused on the Taroom Trough.

For each ASX announcement headline below, write ONE plain-English sentence
(max 20 words) explaining what it likely means for the company or, where
relevant, for Queensland gas / the Taroom Trough. Base the summary ONLY on
the headline text — do not invent specifics that aren't implied by it.

ANNOUNCEMENTS:
{lines}

Respond ONLY as a JSON array of objects: [{{"index": 1, "summary": "..."}}]
No markdown fences, no extra text."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
            timeout=60,
        )
        raw = resp.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        for item in json.loads(raw):
            idx = int(item.get("index", 0)) - 1
            if 0 <= idx < len(announcements):
                announcements[idx]["summary"] = str(item.get("summary", ""))[:200]
    except Exception as e:
        print(f"   ⚠️  Announcement summaries failed (headlines still shown): {e}")


def get_asx_announcements(client=None) -> dict:
    """Main entry point — called by briefing.py."""
    all_anns = []
    for code in WATCHLIST:
        anns = _fetch_for_code(code)
        if anns:
            print(f"   → {code}: {len(anns)} announcement(s)")
        all_anns.extend(anns)

    if not all_anns:
        return {
            "announcements": [],
            "generated_at":  datetime.datetime.now(AEST_OFFSET).isoformat(),
            "error":         "",
        }

    # Price-sensitive first, then newest first
    all_anns.sort(key=lambda a: (not a["is_price_sensitive"], a["date"]), reverse=False)
    all_anns.sort(key=lambda a: a["date"], reverse=True)
    all_anns.sort(key=lambda a: not a["is_price_sensitive"])

    _summarise(client, all_anns)

    return {
        "announcements": all_anns,
        "generated_at":  datetime.datetime.now(AEST_OFFSET).isoformat(),
        "error":         "",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        client = None
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and _ANTHROPIC_AVAILABLE:
            client = _anthropic.Anthropic(api_key=api_key)
        print(f"Fetching ASX announcements for: {', '.join(WATCHLIST)}")
        result = get_asx_announcements(client)
        anns = result["announcements"]
        print(f"\n{len(anns)} announcement(s) in the last {HOURS_BACK}h:\n")
        for a in anns:
            flag = "⚡ " if a["is_price_sensitive"] else "   "
            print(f"{flag}[{a['ticker']}] {a['headline']}")
            if a["summary"]:
                print(f"      → {a['summary']}")
        print("\nDone.")
    else:
        print("Usage: py asx_announcements.py test")
