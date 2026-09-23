"""
asx_announcements.py  —  Overnight ASX announcements for the watchlist (D4)
----------------------------------------------------------------------------
Fetches recent company announcements for each watchlist ticker from the
ASX website's Markit Digital JSON API, falling back to HotCopper's per-stock
announcement pages (the old /asx/1/ ASX API is dead), flags price-sensitive items, and uses Claude
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
# the price-sensitive flag.
#
# NOTE: HotCopper also sits behind Cloudflare-style bot detection which can
# block plain python-requests by its TLS fingerprint (regardless of the
# User-Agent header). curl_cffi impersonates a real Chrome TLS fingerprint
# and gets through where requests can't, so it is used when installed:
#     pip install curl_cffi
# (add it to requirements.txt / the GitHub Actions install step).
# Plain requests remains as the fallback. Every call is wrapped so a change
# on their side degrades to an empty column, never a failed briefing run.
HOTCOPPER_URL = "https://hotcopper.com.au/asx/{code}/announcements/"

try:
    from curl_cffi import requests as _cf_requests
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _cf_requests = None
    _CURL_CFFI_AVAILABLE = False

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

_THREAD_RE = re.compile(
    r'href="(?:https?://hotcopper\.com\.au)?/threads/(\d+)/?"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
_DATE_RE = re.compile(r">\s*(\d{2}/\d{2}/\d{2})\s*<")
_TIME_RE = re.compile(r">\s*(\d{1,2}:\d{2})\s*<")
_TAG_RE  = re.compile(r"<[^>]+>")

_BLOCK_MARKERS = ("just a moment", "cf-challenge", "cf-turnstile",
                  "attention required", "cloudflare", "access denied",
                  "verify you are human")


def _looks_blocked(status: int, body: str) -> bool:
    """Heuristic: did we get a bot-detection challenge instead of the page?"""
    if status in (403, 429, 503):
        return True
    head = body[:3000].lower()
    return any(m in head for m in _BLOCK_MARKERS) and "/threads/" not in body


def _http_get(url: str, debug: bool = False):
    """
    GET with curl_cffi Chrome impersonation when available (defeats
    Cloudflare TLS fingerprinting), falling back to plain requests.
    Returns (html, via) or (None, reason).
    """
    attempts = []
    if _CURL_CFFI_AVAILABLE:
        attempts.append(("curl_cffi/chrome",
                         lambda: _cf_requests.get(url, impersonate="chrome",
                                                  timeout=REQUEST_TIMEOUT,
                                                  headers={"Accept-Language": "en-AU,en;q=0.9"})))
    attempts.append(("requests",
                     lambda: requests.get(url, headers=_HEADERS,
                                          timeout=REQUEST_TIMEOUT)))

    last_reason = "no attempt made"
    for via, fn in attempts:
        try:
            resp = fn()
            status = getattr(resp, "status_code", 0)
            body = resp.text or ""
            if debug:
                print(f"      [{via}] HTTP {status}, {len(body)} bytes, "
                      f"thread links: {len(_THREAD_RE.findall(body))}")
            if _looks_blocked(status, body):
                last_reason = f"{via}: blocked by bot detection (HTTP {status})"
                continue
            if status >= 400:
                last_reason = f"{via}: HTTP {status}"
                continue
            return body, via
        except Exception as e:
            last_reason = f"{via}: {e}"
            continue
    return None, last_reason


def _clean(text: str) -> str:
    """Strip tags/entities and collapse whitespace from an HTML fragment."""
    return " ".join(html_mod.unescape(_TAG_RE.sub(" ", text)).split())


def _parse_when(chunk: str, now: datetime.datetime):
    """
    Extract the announcement timestamp from a row/chunk of HTML.
    Same-day rows show a time ("09:33" AEST); older rows show "DD/MM/YY".
    Returns (aware datetime | None, is_date_only).
    """
    dm = _DATE_RE.search(chunk)
    if dm:
        try:
            d = datetime.datetime.strptime(dm.group(1), "%d/%m/%y")
            return d.replace(hour=12, tzinfo=AEST_OFFSET), True
        except ValueError:
            pass
    tm = _TIME_RE.search(chunk)
    if tm:
        try:
            hh, mm = tm.group(1).split(":")
            return now.replace(hour=int(hh), minute=int(mm),
                               second=0, microsecond=0), False
        except ValueError:
            pass
    return None, False


def _is_price_sensitive(chunk: str) -> bool:
    c = chunk.upper()
    return "PRICE SENSITIVE" in c or "PRICE-SENSITIVE" in c or "PRICE_SENSITIVE" in c


# Tier 1 — the JSON API the asx.com.au website itself calls (Markit Digital).
# Tried first; HotCopper (below) is the fallback. Any failure returns None so
# the HotCopper path runs exactly as before.
ASX_MARKIT_URL = ("https://asx.api.markitdigital.com/asx-research/1.0/"
                  "companies/{code}/announcements?count=20")
ASX_ANN_PAGE   = "https://www.asx.com.au/markets/trade-our-cash-market/announcements.{code}"


def _fetch_from_asx_api(code: str, debug: bool = False):
    """Returns a list (possibly empty) on success, or None if the API failed."""
    url = ASX_MARKIT_URL.format(code=code.lower())
    try:
        if _CURL_CFFI_AVAILABLE:
            resp = _cf_requests.get(url, impersonate="chrome", timeout=REQUEST_TIMEOUT)
        else:
            resp = requests.get(url, headers={**_HEADERS, "Accept": "application/json"},
                                timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 400:
            print(f"   ⚠️  ASX API {code}: HTTP {resp.status_code} — trying HotCopper")
            return None
        data  = resp.json().get("data") or {}
        items = data.get("items") or []
    except Exception as e:
        print(f"   ⚠️  ASX API {code}: {e} — trying HotCopper")
        return None

    now    = datetime.datetime.now(AEST_OFFSET)
    cutoff = now - datetime.timedelta(hours=HOURS_BACK)
    out = []
    for it in items:
        headline = (it.get("headline") or it.get("header") or "").strip()
        raw_date = it.get("date") or it.get("releaseDate") or ""
        if not headline or not raw_date:
            continue
        try:
            when = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
            when = when.astimezone(AEST_OFFSET)
        except ValueError:
            continue
        if when < cutoff:
            continue
        ps = bool(it.get("isPriceSensitive") or it.get("marketSensitive"))
        if debug:
            print(f"      {'PS ' if ps else '   '}{when.isoformat()}  {headline[:60]}")
        out.append({
            "ticker":             code,
            "company":            COMPANY_NAMES.get(code, code),
            "headline":           headline,
            "is_price_sensitive": ps,
            "date":               when.isoformat(),
            "url":                ASX_ANN_PAGE.format(code=code.lower()),
            "summary":            "",
        })
        if len(out) >= MAX_PER_STOCK:
            break
    if debug:
        print(f"      [asx api] {len(items)} item(s) returned, {len(out)} in window")
    return out


def _fetch_for_code(code: str, debug: bool = False) -> list[dict]:
    """Fetch recent announcements for one ticker — ASX API first, then
    HotCopper. Returns [] on any failure."""
    api_result = _fetch_from_asx_api(code, debug=debug)
    if api_result is not None:
        return api_result

    page, via = _http_get(HOTCOPPER_URL.format(code=code.lower()), debug=debug)
    if page is None:
        print(f"   ⚠️  HotCopper {code}: {via}")
        return []

    now         = datetime.datetime.now(AEST_OFFSET)
    cutoff      = now - datetime.timedelta(hours=HOURS_BACK)
    cutoff_date = cutoff.date()

    # Primary: table rows. Fallback: if the markup isn't <tr>-based, split
    # the page into chunks anchored on each /threads/ link and look for the
    # date and price-sensitive marker in the text that follows each anchor.
    chunks = re.findall(r"<tr[^>]*>.*?</tr>", page, re.DOTALL)
    if not any(_THREAD_RE.search(c) for c in chunks):
        anchors = list(_THREAD_RE.finditer(page))
        chunks = []
        for i, m in enumerate(anchors):
            chunk_end = anchors[i + 1].start() if i + 1 < len(anchors) else m.end() + 1200
            chunks.append(page[m.start():min(chunk_end, m.end() + 1200)])

    out, seen = [], set()
    # Each announcement appears twice on the page (desktop + mobile markup),
    # so dedupe on the HotCopper thread id.
    for chunk in chunks:
        m = _THREAD_RE.search(chunk)
        if not m:
            continue
        thread_id = m.group(1)
        if thread_id in seen:
            continue
        headline = _clean(m.group(2))
        if not headline:
            continue
        seen.add(thread_id)

        rel, date_only = _parse_when(chunk, now)

        # Window filter. Date-only rows carry no time-of-day, so they are
        # compared at *calendar date* granularity — yesterday's announcements
        # always count while HOURS_BACK >= 24, no matter what time of day the
        # briefing (or a manual test) runs. Timed rows compare exactly.
        if rel is not None:
            if date_only:
                if rel.date() < cutoff_date:
                    continue
            elif rel < cutoff:
                continue

        if debug:
            when = rel.isoformat() if rel else "(no date found)"
            flag = "PS " if _is_price_sensitive(chunk) else "   "
            print(f"      {flag}{when}  {headline[:60]}")

        out.append({
            "ticker":             code,
            "company":            COMPANY_NAMES.get(code, code),
            "headline":           headline,
            "is_price_sensitive": _is_price_sensitive(chunk),
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


def get_asx_announcements(client=None, codes=None) -> dict:
    """
    Main entry point — called by briefing.py.

    codes: optional list of ASX codes to fetch instead of WATCHLIST. The
    Market Watch tab passes the codes from its own company list, which Doug
    edits in the settings dashboard, so the two no longer have to be kept in
    sync by hand.
    """
    wanted = [c.strip().upper() for c in (codes or WATCHLIST) if str(c).strip()]
    all_anns = []
    for code in dict.fromkeys(wanted):
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
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "debug":
        # Verbose single-code diagnostics: HTTP transport used, status,
        # body size, thread-link count, and every parsed row with its date.
        code = (sys.argv[2] if len(sys.argv) > 2 else "GAS").upper()
        print(f"curl_cffi installed: {_CURL_CFFI_AVAILABLE}"
              + ("" if _CURL_CFFI_AVAILABLE else
                 "   <-- install it:  py -m pip install curl_cffi"))
        print(f"Fetching {HOTCOPPER_URL.format(code=code.lower())}")
        anns = _fetch_for_code(code, debug=True)
        print(f"\n{len(anns)} announcement(s) within the last {HOURS_BACK}h window.")

    elif mode == "test":
        client = None
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and _ANTHROPIC_AVAILABLE:
            client = _anthropic.Anthropic(api_key=api_key)
        print(f"curl_cffi installed: {_CURL_CFFI_AVAILABLE}"
              + ("" if _CURL_CFFI_AVAILABLE else
                 "   <-- install it:  py -m pip install curl_cffi"))
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
        print("       py asx_announcements.py debug [CODE]")
