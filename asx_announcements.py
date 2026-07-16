"""
asx_announcements.py  —  Overnight ASX announcements for the watchlist (D4)
----------------------------------------------------------------------------
Fetches recent company announcements for each watchlist ticker from
HotCopper's per-stock announcement pages (the old public ASX JSON API is
dead — see note above _fetch_for_code), flags price-sensitive items, and uses Claude
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
import re
import html as html_mod
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

# Source: HotCopper's per-stock announcement pages (server-rendered HTML).
# The old asx.com.au JSON API (/asx/1/company/{code}/announcements) is dead —
# ASX now 404s it and TLS-fingerprints/blocks Python requests from server IPs.
# HotCopper republishes the full ASX announcement feed per ticker, including
# the price-sensitive flag, and serves plain HTML that parses with regex.
# Every call is wrapped so a change on their side degrades to an empty
# column, never a failed briefing run.
HOTCOPPER_URL = "https://hotcopper.com.au/asx/{code}/announcements/"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}

_THREAD_RE = re.compile(
    r'href="(?:https?://hotcopper\.com\.au)?/threads/(\d+)/?"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
_DATE_RE = re.compile(r">\s*(\d{2}/\d{2}/\d{2})\s*<")
_TIME_RE = re.compile(r">\s*(\d{1,2}:\d{2})\s*<")
_TAG_RE  = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Strip tags/entities and collapse whitespace from an HTML fragment."""
    return " ".join(html_mod.unescape(_TAG_RE.sub(" ", text)).split())


def _fetch_for_code(code: str) -> list[dict]:
    """Fetch recent announcements for one ticker from HotCopper.
    Returns [] on any failure."""
    try:
        resp = requests.get(
            HOTCOPPER_URL.format(code=code.lower()),
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        page = resp.text
    except Exception as e:
        print(f"   ⚠️  HotCopper {code}: {e}")
        return []

    now    = datetime.datetime.now(AEST_OFFSET)
    cutoff = now - datetime.timedelta(hours=HOURS_BACK)

    out, seen = [], set()
    # Announcements are table rows; each appears twice (desktop + mobile
    # markup), so dedupe on the HotCopper thread id.
    for row in re.findall(r"<tr[^>]*>.*?</tr>", page, re.DOTALL):
        m = _THREAD_RE.search(row)
        if not m:
            continue
        thread_id = m.group(1)
        if thread_id in seen:
            continue
        headline = _clean(m.group(2))
        if not headline:
            continue
        seen.add(thread_id)

        # Same-day rows show a time ("09:33"); older rows show "DD/MM/YY".
        # Date-only rows are pinned to midday AEST so the HOURS_BACK window
        # cleanly includes yesterday and excludes the day before.
        rel = None
        dm = _DATE_RE.search(row)
        if dm:
            try:
                d = datetime.datetime.strptime(dm.group(1), "%d/%m/%y")
                rel = d.replace(hour=12, tzinfo=AEST_OFFSET)
            except ValueError:
                pass
        else:
            tm = _TIME_RE.search(row)
            if tm:
                try:
                    hh, mm = tm.group(1).split(":")
                    rel = now.replace(hour=int(hh), minute=int(mm),
                                      second=0, microsecond=0)
                except ValueError:
                    pass

        if rel and rel < cutoff:
            # Page is newest-first — everything after this is older still.
            break

        out.append({
            "ticker":             code,
            "company":            COMPANY_NAMES.get(code, code),
            "headline":           headline,
            "is_price_sensitive": "PRICE SENSITIVE" in row.upper(),
            "date":               (rel or now).isoformat(),
            "url":                f"https://hotcopper.com.au/threads/{thread_id}/",
            "summary":            "",
        })
        if len(out) >= MAX_PER_STOCK:
            break
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
