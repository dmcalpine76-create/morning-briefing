"""
Morning Briefing Generator
---------------------------
Fetches RSS feeds across three topic categories, summarises the top 5 stories
per category using the Anthropic API, then generates a self-contained HTML page.

Usage:
    python briefing.py

Requirements:
    pip install feedparser anthropic requests

Environment variables:
    ANTHROPIC_API_KEY   Your Anthropic API key (required)

The output file (briefing.html) can be:
  - Opened directly in a browser
  - Served by any static file server (python -m http.server 8080)
  - Deployed to a host like Vercel, Netlify, or a VPS
"""

import os
import json
import re
import datetime
from dotenv import load_dotenv
load_dotenv()
import requests
import feedparser
import anthropic
from pathlib import Path

# Outlook email integration (optional — only active if OUTLOOK_CLIENT_ID is set)
try:
    import outlook_email as _outlook
    _OUTLOOK_AVAILABLE = True
except ImportError:
    _outlook = None
    _OUTLOOK_AVAILABLE = False

try:
    import outlook_calendar
    CALENDAR_ENABLED = True
except ImportError:
    CALENDAR_ENABLED = False


TOPICS_FILE = Path(__file__).parent / "topics.json"
SETTINGS_FILE = Path(__file__).parent / "briefing_settings.json"
TOP_N_TOPIC_STORIES = 5    # stories per topic column (overridden by settings file)

# ─────────────────────────────────────────────
# SETTINGS LOADER — reads briefing_settings.json
# ─────────────────────────────────────────────

def _load_settings():
    """
    Load briefing_settings.json if it exists and override the module-level
    config variables (CATEGORIES, MARKET_TICKERS, ASX_WATCHLIST, story counts).
    Called once at the bottom of the config block.
    """
    global CATEGORIES, MARKET_TICKERS, ASX_WATCHLIST
    global TOP_N_STORIES, TOP_N_TOPIC_STORIES, MAX_FEED_ITEMS

    if not SETTINGS_FILE.exists():
        return   # no settings file — use hardcoded defaults

    try:
        s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️  Could not read briefing_settings.json: {e}")
        return

    # Story counts
    sc = s.get("story_counts", {})
    if sc.get("top_n_stories"):       TOP_N_STORIES       = int(sc["top_n_stories"])
    if sc.get("top_n_topic_stories"): TOP_N_TOPIC_STORIES = int(sc["top_n_topic_stories"])
    if sc.get("max_feed_items"):      MAX_FEED_ITEMS      = int(sc["max_feed_items"])

    # Categories (name → {emoji, color, feeds})
    if s.get("categories"):
        CATEGORIES = {
            cat["name"]: {
                "emoji": cat.get("emoji", "📰"),
                "color": cat.get("color", "#1a1a1a"),
                "feeds": [f for f in cat.get("feeds", []) if f.strip()],
            }
            for cat in s["categories"]
            if cat.get("name") and cat.get("feeds")
        }

    # Market tickers
    if s.get("market_tickers"):
        MARKET_TICKERS = [
            t for t in s["market_tickers"]
            if t.get("sym") and t.get("label")
        ]

    # ASX watchlist
    if s.get("asx_watchlist"):
        ASX_WATCHLIST = [
            t for t in s["asx_watchlist"]
            if t.get("sym") and t.get("label")
        ]



# ─────────────────────────────────────────────
# CONFIG — edit feeds and story counts here
# ─────────────────────────────────────────────

CATEGORIES = {
    "International News": {
        "emoji": "🌍",
        "color": "#1a3a5c",
        "feeds": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "https://www.theguardian.com/world/rss",
            "https://feeds.reuters.com/reuters/worldNews",
            "https://www.aljazeera.com/xml/rss/all.xml",
        ],
    },
    "Australian News": {
        "emoji": "🦘",
        "color": "#1a4a2e",
        "feeds": [
            "https://www.abc.net.au/news/feed/2942460/rss.xml",
            "https://www.smh.com.au/rss/feed.xml",
            "https://www.theaustralian.com.au/feed",
            "https://feeds.skynews.com.au/feeds/news.xml",
            "https://www.theguardian.com/australia-news/rss",
        ],
    },
    "Australian Finance & Markets": {
        "emoji": "📈",
        "color": "#3a1a4a",
        "feeds": [
            # ABC Business — reliable, public, Australian
            "https://www.abc.net.au/news/business/rss.xml",
            # The Age business section (Nine Media)
            "https://www.theage.com.au/rss/business.xml",
            # News.com.au finance
            "https://www.news.com.au/content-feeds/latest-news-finance/",
            # Financial Newswire — Australian financial services industry
            "https://financialnewswire.com.au/feed",
            # Australian Financial News (afndaily)
            "https://afndaily.com.au/feed",
            # RBA media releases
            "https://www.rba.gov.au/rss/media-releases.xml",
            # Investing.com AU — Australian market news
            "https://au.investing.com/rss/news.rss",
            # ASX announcements
            "https://www.asx.com.au/asx/1/news/rss",
        ],
    },
}

TOP_N_STORIES = 5        # stories to summarise per category
MAX_FEED_ITEMS = 20      # fetch up to this many items per feed before filtering

ASX_WATCHLIST = [
    {"sym": "GAS.AX",  "label": "GAS"},
    {"sym": "COI.AX",  "label": "COI"},
    {"sym": "BPT.AX",  "label": "BPT"},
    {"sym": "STO.AX",  "label": "STO"},
]


# ─────────────────────────────────────────────
# RSS FETCHING
# ─────────────────────────────────────────────

def fetch_feed_items(feed_url: str, max_items: int = MAX_FEED_ITEMS) -> list[dict]:
    """Parse a single RSS feed and return a list of story dicts."""
    try:
        feed = feedparser.parse(feed_url)
        items = []
        for entry in feed.entries[:max_items]:
            title   = getattr(entry, "title",   "").strip()
            summary = getattr(entry, "summary", "").strip()
            link    = getattr(entry, "link",    "").strip()
            pub     = getattr(entry, "published", "")
            if title:
                items.append({
                    "title":   title,
                    "summary": summary[:500],   # truncate so we don't blow the prompt
                    "link":    link,
                    "published": pub,
                    "source":  feed.feed.get("title", feed_url),
                })
        return items
    except Exception as e:
        print(f"  ⚠️  Failed to fetch {feed_url}: {e}")
        return []


def fetch_category_items(feeds: list[str]) -> list[dict]:
    """Aggregate items from multiple feeds, deduplicating by title."""
    all_items = []
    seen_titles = set()
    for url in feeds:
        print(f"  → {url}")
        for item in fetch_feed_items(url):
            key = item["title"].lower()[:60]
            if key not in seen_titles:
                seen_titles.add(key)
                all_items.append(item)
    return all_items


# ─────────────────────────────────────────────
# AI SUMMARISATION
# ─────────────────────────────────────────────

def build_prompt(category_name: str, items: list[dict], top_n: int) -> str:
    stories_text = "\n\n".join(
        f"[{i+1}] {it['title']}\nSource: {it['source']}\n{it['summary']}"
        for i, it in enumerate(items[:50])   # cap at 50 to stay within token budget
    )
    return f"""You are a sharp, well-informed briefing editor. 
From the list of {len(items)} recent news items below, select the {top_n} most significant 
stories for the category "{category_name}".

For each selected story return a JSON array with objects containing:
  - "headline": a crisp, informative headline (max 10 words)
  - "summary": ONE sentence only — the single most important fact. Max 25 words. No fluff.
  - "source": the publication name
  - "link": the URL if available (use the one from the item)
  - "significance": one of ["critical", "major", "notable"]

Return ONLY the JSON array — no markdown fences, no extra text.

NEWS ITEMS:
{stories_text}
"""


def summarise_category(client: anthropic.Anthropic, category_name: str,
                        items: list[dict], top_n: int) -> list[dict]:
    """Call the Anthropic API to select and summarise top stories. Retries on connection error."""
    if not items:
        return []
    import time
    prompt = build_prompt(category_name, items, top_n)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model      = "claude-opus-4-6",
                max_tokens = 2048,
                messages   = [{"role": "user", "content": prompt}],
                timeout    = 120,
            )
            raw = message.content[0].text.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(raw)
        except Exception as e:
            print(f"  ⚠️  Attempt {attempt+1}/3 failed for {category_name}: {e}")
            if attempt < 2:
                time.sleep(5)
    return []



# ─────────────────────────────────────────────
# MARKET DATA  (fetched at generation time)
# ─────────────────────────────────────────────

MARKET_TICKERS = [
    # Broad market
    {"sym": "^AXJO",    "label": "ASX 200",  "fmt": "index"},
    {"sym": "AUDUSD=X", "label": "AUD/USD",  "fmt": "fx"},
    {"sym": "GC=F",     "label": "Gold",     "fmt": "price"},
    {"sym": "CL=F",     "label": "Oil",      "fmt": "price"},
    {"sym": "^GSPC",    "label": "S&P 500",  "fmt": "index"},
]


# Apply any overrides from briefing_settings.json (written by settings dashboard)
# Called here so ALL defaults (CATEGORIES, MARKET_TICKERS, ASX_WATCHLIST) exist first
_load_settings()



def _yf_price_and_change(ticker_obj):
    """
    Extract (price, prev_close) from a yfinance Ticker object robustly.
    Tries fast_info attribute names from both old and new yfinance versions,
    then falls back to history() if fast_info returns nothing useful.
    Returns (price, change_pct) or (None, None) on failure.
    """
    price, prev = None, None

    # Try fast_info — attribute names vary across yfinance versions
    try:
        fi = ticker_obj.fast_info
        for price_attr in ("last_price", "regularMarketPrice", "lastPrice"):
            val = getattr(fi, price_attr, None)
            if val:
                price = float(val)
                break
        for prev_attr in ("previous_close", "regularMarketPreviousClose", "previousClose"):
            val = getattr(fi, prev_attr, None)
            if val:
                prev = float(val)
                break
    except Exception:
        pass

    # Fall back to history() if fast_info gave nothing
    if price is None:
        try:
            hist = ticker_obj.history(period="2d", auto_adjust=True)
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
        except Exception:
            pass

    if price is None:
        return None, None

    chg = ((price - prev) / prev * 100) if prev else 0.0
    return price, chg


def fetch_market_data() -> list[dict]:
    """Fetch market prices via yfinance library or fall back to empty list."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    results = []
    for t in MARKET_TICKERS:
        try:
            price, chg = _yf_price_and_change(yf.Ticker(t["sym"]))
            if price is None:
                continue
            if t["fmt"] == "fx":
                fmt_price = f"{price:.4f}"
            elif t["fmt"] == "index":
                fmt_price = f"{price:,.0f}"
            else:
                fmt_price = f"${price:.2f}"
            results.append({
                "label":  t["label"],
                "price":  fmt_price,
                "change": f"{chg:+.2f}%",
                "up":     chg >= 0,
            })
        except Exception as e:
            print(f"  ⚠️  Ticker {t['sym']}: {e}")
            continue
    return results


def fetch_asx_watchlist() -> list[dict]:
    """Fetch ASX watchlist stock prices via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    results = []
    for t in ASX_WATCHLIST:
        try:
            price, chg = _yf_price_and_change(yf.Ticker(t["sym"]))
            if price is None:
                continue
            results.append({
                "label":  t["label"],
                "sym":    t["sym"],
                "price":  f"${price:.3f}",
                "change": f"{chg:+.2f}%",
                "up":     chg >= 0,
            })
        except Exception as e:
            print(f"  ⚠️  ASX {t['sym']}: {e}")
            continue
    return results


def asx_watchlist_html(asx_data: list[dict]) -> str:
    """Render ASX watchlist as widget-slot HTML with links to ASX company pages."""
    if not asx_data:
        return '<div class="widget-slot widget-slot-placeholder">📊 ASX stocks unavailable</div>'
    html = ""
    for s in asx_data:
        dir_class = "up" if s["up"] else "down"
        arrow     = "▲" if s["up"] else "▼"
        # Strip .AX suffix for the ASX URL (e.g. GAS.AX -> GAS)
        code = s["label"].upper()
        url  = f"https://www.asx.com.au/markets/company/{code}"
        html += (
            f'<a href="{url}" target="_blank" rel="noopener" class="widget-slot widget-asx-link">' +
            f'<span class="label">{s["label"]}</span>' +
            f'<span class="value">{s["price"]}</span>' +
            f'<span class="change {dir_class}">{arrow} {s["change"].lstrip("+").lstrip("-")}</span>' +
            f'</a>'
        )
    return html


def market_widgets_html(market_data: list[dict]) -> str:
    """Render market data as widget-slot HTML for embedding in the page."""
    if not market_data:
        return '<div class="widget-slot widget-slot-placeholder">📈 Install yfinance for live data</div>'
    html = ""
    for m in market_data:
        dir_class = "up" if m["up"] else "down"
        arrow     = "▲" if m["up"] else "▼"
        html += (
            f'<div class="widget-slot">' +
            f'<span class="label">{m["label"]}</span>' +
            f'<span class="value">{m["price"]}</span>' +
            f'<span class="change {dir_class}">{arrow} {m["change"].lstrip("+").lstrip("-")}</span>' +
            f'</div>'
        )
    return html


# ─────────────────────────────────────────────
# WEATHER  (Open-Meteo — no API key required)
# ─────────────────────────────────────────────

WEATHER_TZ = "Australia/Brisbane"

LOCATIONS = {
    "Brisbane":       {"lat": -27.4705, "lon": 153.0260, "days": 7},
    "Sunshine Beach": {"lat": -26.3935, "lon": 153.0950, "days": 7},
}

# WMO weather interpretation code → (emoji, short label)
WMO_ICONS = {
    0:  ("☀️",  "Clear"),
    1:  ("🌤️", "Mostly clear"),
    2:  ("⛅",  "Part cloud"),
    3:  ("☁️",  "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Icy fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"),
    63: ("🌧️", "Rain"),
    65: ("🌧️", "Heavy rain"),
    71: ("🌨️", "Light snow"),
    73: ("🌨️", "Snow"),
    75: ("❄️",  "Heavy snow"),
    80: ("🌦️", "Showers"),
    81: ("🌧️", "Rain showers"),
    82: ("⛈️",  "Heavy showers"),
    95: ("⛈️",  "Thunderstorm"),
    96: ("⛈️",  "T-storm/hail"),
    99: ("⛈️",  "T-storm/hail"),
}


def fetch_weather_for(name: str) -> list[dict]:
    """
    Fetch daily forecast for a named location from LOCATIONS dict.
    Returns list of {day, date, icon, desc, high, low, rain_prob}.
    """
    loc = LOCATIONS.get(name)
    if not loc:
        return []
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={loc['lat']}&longitude={loc['lon']}"
            "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&timezone={WEATHER_TZ}&forecast_days={loc['days']}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data  = resp.json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        codes = daily.get("weathercode", [])
        highs = daily.get("temperature_2m_max", [])
        lows  = daily.get("temperature_2m_min", [])
        probs = daily.get("precipitation_probability_max", [])
        result = []
        for i, date_str in enumerate(dates):
            dt         = datetime.date.fromisoformat(date_str)
            code       = int(codes[i]) if i < len(codes) else 0
            icon, desc = WMO_ICONS.get(code, ("🌡️", "Unknown"))
            result.append({
                "day":       "Today" if i == 0 else dt.strftime("%a"),
                "date":      f"{dt.day} {dt.strftime('%b')}",
                "icon":      icon,
                "desc":      desc,
                "high":      round(highs[i]) if i < len(highs) and highs[i] is not None else None,
                "low":       round(lows[i])  if i < len(lows)  and lows[i]  is not None else None,
                "rain_prob": int(probs[i])   if i < len(probs) and probs[i] is not None else None,
            })
        return result
    except Exception as e:
        print(f"  ⚠️  Weather fetch failed ({name}): {e}")
        return []


def fetch_weather() -> list[dict]:
    """Backward-compatible wrapper — returns Brisbane 7-day forecast."""
    return fetch_weather_for("Brisbane")


# ─────────────────────────────────────────────
# EAST COAST GAS PRICE
# ─────────────────────────────────────────────

def fetch_au_gas_price() -> dict:
    """
    Fetch Australian East Coast STTM gas spot price from NEMweb.
    Downloads CURRENTDAY.ZIP from www.nemweb.com.au/Reports/CURRENT/STTM/,
    extracts the ex ante market price CSV, and returns the Brisbane hub price.
    This is a public file updated daily around 6am AEST — no login required.
    Returns dict: {price, unit, hub, date, source} or {} on failure.
    """
    import datetime, io, zipfile, csv

    today = datetime.date.today().isoformat()
    url = "http://www.nemweb.com.au/Reports/CURRENT/STTM/CURRENTDAY.ZIP"

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            # Find the ex ante price file — named like int111_v4_exante_...csv
            price_files = [n for n in z.namelist()
                           if "exante" in n.lower() and n.lower().endswith(".csv")]
            # Fallback to any price-related file
            if not price_files:
                price_files = [n for n in z.namelist()
                               if "price" in n.lower() and n.lower().endswith(".csv")]
            if not price_files:
                return {}

            with z.open(price_files[0]) as f:
                # AEMO CSVs have a header structure: first row is "C,..." metadata,
                # then "I,..." column headers, then "D,..." data rows
                text = f.read().decode("utf-8", errors="replace")
                lines = text.splitlines()

                # Find column header row (starts with "I,")
                header_row = None
                data_rows = []
                for line in lines:
                    if line.startswith("I,"):
                        header_row = line
                    elif line.startswith("D,") and header_row:
                        data_rows.append(line)

                if not header_row or not data_rows:
                    # Try plain CSV fallback
                    reader = csv.DictReader(lines)
                    rows = list(reader)
                    if rows:
                        r = rows[0]
                        price_key = next((k for k in r if "price" in k.lower()), None)
                        if price_key and r[price_key]:
                            return {
                                "price": round(float(r[price_key]), 2),
                                "unit": "$/GJ", "hub": "STTM",
                                "date": today, "source": "NEMweb STTM"
                            }
                    return {}

                # Parse AEMO format: columns from header row, values from data rows
                cols = [c.strip() for c in header_row.split(",")]
                # Prefer Brisbane hub; fall back to any hub
                brisbane_row = None
                any_row = None
                for line in data_rows:
                    vals = [v.strip() for v in line.split(",")]
                    row = dict(zip(cols, vals))
                    hub_val = row.get("HUB_NAME", row.get("STTM_REGION", "")).upper()
                    if any_row is None:
                        any_row = row
                    if "BRISBANE" in hub_val or "QLD" in hub_val:
                        brisbane_row = row
                        break

                target = brisbane_row or any_row
                if not target:
                    return {}

                price_key = next(
                    (k for k in target if "price" in k.lower() and target.get(k, "").strip()),
                    None
                )
                hub_key = next(
                    (k for k in target if any(x in k.upper() for x in ["HUB", "REGION", "LOCATION"])),
                    None
                )
                date_key = next(
                    (k for k in target if "date" in k.lower() or "gasdate" in k.lower().replace("_","")),
                    None
                )

                if price_key and target[price_key].strip():
                    price_val = float(target[price_key].strip())
                    hub_name  = target[hub_key].strip() if hub_key else "STTM"
                    gas_date  = target[date_key].strip()[:10] if date_key else today
                    return {
                        "price":  round(price_val, 2),
                        "unit":   "$/GJ",
                        "hub":    hub_name or "STTM Brisbane",
                        "date":   gas_date,
                        "source": "NEMweb STTM",
                    }

    except Exception as e:
        print(f"   ⚠️  NEMweb STTM fetch error: {e}")

    return {}


# Keep old name as alias for backwards compat
def fetch_gas_price() -> dict:
    return fetch_au_gas_price()



def fetch_henry_hub_price() -> dict:
    """
    Fetch Henry Hub natural gas futures price via yfinance (NG=F).
    Returns dict: {price, unit, hub, date, source} or {} on failure.
    Unit is USD/MMBtu.
    """
    import datetime
    try:
        import yfinance as _yf
        t = _yf.Ticker("NG=F")
        fi = t.fast_info
        price_val = getattr(fi, "last_price", None) or getattr(fi, "regularMarketPrice", None)
        if price_val and float(price_val) > 0:
            return {
                "price":  round(float(price_val), 3),
                "unit":   "USD/MMBtu",
                "hub":    "Henry Hub",
                "date":   datetime.date.today().isoformat(),
                "source": "yfinance",
            }
    except Exception:
        pass
    return {}


def gas_price_html(au_gas: dict, hh_gas: dict = None) -> str:
    """
    Render gas price section: AU domestic spot + Henry Hub side by side.
    au_gas  — from fetch_au_gas_price(), unit $/GJ
    hh_gas  — from fetch_henry_hub_price(), unit USD/MMBtu
    """
    def _slot(label: str, price, unit: str, flag: str = "") -> str:
        fmt = f"{flag}{price:.2f}" if price is not None else "—"
        return f"""<div class="wx-slot wx-gas-slot">
            <span class="wx-day">{label}</span>
            <span class="wx-gas-price">{fmt}</span>
            <span class="wx-rain">{unit}</span>
        </div>"""

    slots = ""

    if au_gas and au_gas.get("price") is not None:
        hub = au_gas.get("hub", "East Coast")
        hub_short = hub.replace("Short Term Trading Market", "STTM").replace(" Hub", "").strip()
        if not hub_short or len(hub_short) > 14:
            hub_short = "AU East Coast"
        slots += _slot(hub_short, au_gas["price"], "$/GJ")
    else:
        slots += _slot("AU East Coast", None, "$/GJ")

    hh = hh_gas or {}
    if hh.get("price") is not None:
        slots += _slot("Henry Hub", hh["price"], "USD/MMBtu", "$")
    else:
        slots += _slot("Henry Hub", None, "USD/MMBtu")

    return f'''<div class="wx-section-label">⛽ Gas</div>
        {slots}'''  


def weather_bar_html(brisbane: list[dict], sunshine: list[dict] = None,
                     gas: dict = None, hh_gas: dict = None) -> str:
    """
    Render the combined info bar:
      Brisbane 7-day | Sunshine Beach 7-day | East Coast Gas price
    All three sections fill the full width with no blank gaps.
    """
    def slots_html(forecast, today_marker=True):
        out = ""
        for day in forecast:
            high      = f"{day['high']}°" if day['high'] is not None else "—"
            low       = f"{day['low']}°"  if day['low']  is not None else "—"
            rain      = f"{day['rain_prob']}%" if day['rain_prob'] is not None else ""
            rain_html = f'<span class="wx-rain">{rain}</span>' if rain else '<span class="wx-rain">&nbsp;</span>'
            today_cls = " wx-today" if (today_marker and day["day"] == "Today") else ""
            out += f"""<div class="wx-slot{today_cls}">
                <span class="wx-day">{day['day']}</span>
                <span class="wx-icon">{day['icon']}</span>
                <span class="wx-temps"><span class="wx-high">{high}</span><span class="wx-low">{low}</span></span>
                {rain_html}
            </div>"""
        return out

    brisbane_slots  = slots_html(brisbane) if brisbane else '<span class="weather-unavailable">Unavailable</span>'
    sunshine_slots  = slots_html(sunshine or []) if sunshine else '<span class="weather-unavailable">Unavailable</span>'
    gas_block       = gas_price_html(gas or {}, hh_gas or {})

    return f"""<div class="weather-bar" id="weather-bar">
    <div class="wx-section">
        <div class="wx-section-label">🌆 Brisbane</div>
        <div class="wx-slots">{brisbane_slots}</div>
    </div>
    <div class="wx-divider"></div>
    <div class="wx-section">
        <div class="wx-section-label">🏖️ Sunshine Beach</div>
        <div class="wx-slots">{sunshine_slots}</div>
    </div>
    <div class="wx-divider"></div>
    <div class="wx-section wx-section-gas">
        {gas_block}
    </div>
</div>"""



SIGNIFICANCE_BADGE = {
    "critical": '<span class="badge badge-critical">⚡ Critical</span>',
    "major":    '<span class="badge badge-major">● Major</span>',
    "notable":  '<span class="badge badge-notable">◦ Notable</span>',
}


def story_card_html(story: dict, index: int) -> str:
    sig   = story.get("significance", "notable").lower()
    badge = SIGNIFICANCE_BADGE.get(sig, SIGNIFICANCE_BADGE["notable"])
    link  = story.get("link", "").strip()
    src   = story.get("source", "")
    headline = story.get("headline", "")
    summary  = story.get("summary", "")
    # Only render as a link if we have a real http URL
    if link and link.startswith("http"):
        hl_html = f'<a href="{link}" target="_blank" rel="noopener noreferrer">{headline}</a>'
    else:
        hl_html = headline
    return f"""
            <article class="story-card" style="--delay: {index * 0.07}s">
                <div class="story-meta">
                    {badge}
                    <span class="story-source">{src}</span>
                </div>
                <h3 class="story-headline">{hl_html}</h3>
                <p class="story-summary">{summary}</p>
            </article>"""


def column_html(name: str, meta: dict, stories: list[dict]) -> str:
    cards = "".join(story_card_html(s, i) for i, s in enumerate(stories))
    emoji = meta["emoji"]
    color = meta["color"]
    count = len(stories)
    # Escape & for HTML
    display_name = name.replace("&", "&amp;")
    return f"""
    <div class="briefing-column">
        <div class="column-header" style="--section-color: {color}">
            <span class="column-emoji">{emoji}</span>
            <h2 class="column-title">{display_name}</h2>
            <span class="story-count">{count}</span>
        </div>
        <div class="stories-list">
            {cards}
        </div>
    </div>"""



def _build_topic_tab_views(topics: list[dict]) -> str:
    """Build hidden topic tab divs — content filled in by main() after story generation."""
    # This is called at HTML generation time; stories are passed via the global
    # all_topic_stories dict which is set in main() before generate_html is called.
    return "<!-- topic tab placeholders: populated by generate_html via all_topic_stories -->"


def _build_topic_tab_views_with_stories(topics: list[dict], all_stories: dict) -> str:
    """Build a single topics tab with all topics as narrow side-by-side columns."""
    if not topics:
        return ""

    columns = ""
    for topic in topics:
        tid     = topic["id"]
        stories = all_stories.get(tid, [])
        color   = topic.get("color", "#1a1a1a")
        emoji   = topic.get("emoji", "📌")
        name    = topic["name"].replace("&", "&amp;")
        cards   = ""
        for i, s in enumerate(stories):
            sig     = s.get("significance", "notable").lower()
            badge   = SIGNIFICANCE_BADGE.get(sig, SIGNIFICANCE_BADGE["notable"])
            link    = s.get("link", "#")
            src     = s.get("source", "")
            hl      = s.get("headline", "")
            summary = s.get("summary", "")
            cards += f"""
                <article class="story-card" style="--delay:{i*0.05}s">
                    <div class="story-meta">{badge}<span class="story-source">{src}</span></div>
                    <h3 class="story-headline">{"<a href='" + link + "' target='_blank' rel='noopener noreferrer'>" + hl + "</a>" if link and link.startswith("http") else hl}</h3>
                    <p class="story-summary">{summary}</p>
                </article>"""
        empty = '<div class="t-empty">No stories found today.</div>' if not cards else ""
        columns += f"""
        <div class="briefing-column">
            <div class="column-header" style="--section-color:{color}">
                <span class="column-emoji">{emoji}</span>
                <h2 class="column-title">{name}</h2>
                <span class="story-count">{len(stories)}</span>
            </div>
            <div class="stories-list">{cards}{empty}</div>
        </div>"""

    return f"""
<div id="view-topics" style="display:none">
    <div class="topics-tab-grid">
        {columns}
    </div>
</div>"""


def _build_email_tab(analysis: dict, empty_msg: str = "No email data available. Run: py outlook_email.py setup") -> str:
    """
    Build the email tab HTML to embed inside briefing.html.

    Each action and people card has two ways to add to Microsoft To Do:
      1. 📋 deep-link button — opens To Do with the task pre-filled.
         Works anywhere the HTML is opened: Gmail, phone, any device.
         No server or auth required.
      2. Checkbox + sticky footer Push button — bulk-pushes selected tasks
         via the local server (/push endpoint).  Local machine only.
    The digest column remains read-only.
    """
    import json as _json
    import html as _html

    digest  = analysis.get("digest", [])
    actions = analysis.get("actions", [])
    people  = analysis.get("people", [])

    PRIORITY_BADGE = {
        "action-required": '<span class="badge badge-critical">⚡ Action</span>',
        "important":       '<span class="badge badge-major">● Important</span>',
        "informational":   '<span class="badge badge-notable">◦ Info</span>',
    }
    URGENCY_BADGE = {
        "urgent": '<span class="badge badge-critical">⚡ Urgent</span>',
        "high":   '<span class="badge badge-major">● High</span>',
        "normal": '<span class="badge badge-notable">◦ Normal</span>',
    }

    # ── Build pushable task list (embedded as JSON) ──────────────────────────
    # Each entry becomes one Microsoft To Do task if the user ticks it.
    task_data = []

    for i, item in enumerate(actions):
        task_data.append({
            "id":       f"action_{i}",
            "title":    item.get("action", ""),
            "detail":   item.get("context", ""),
            "due":      datetime.date.today().isoformat(),   # due today by default
            "priority": item.get("priority", "normal"),
        })

    for i, item in enumerate(people):
        act   = item.get("action", "contact")
        label = {"schedule-meeting": "Schedule meeting",
                 "follow-up":        "Follow up",
                 "contact":          "Contact"}.get(act, act)
        name  = item.get("name", "")
        title = f"{label}: {name}" if name else label
        task_data.append({
            "id":       f"person_{i}",
            "title":    title,
            "detail":   item.get("reason", ""),
            "due":      datetime.date.today().isoformat(),
            "priority": "normal",
        })

    tasks_json = _json.dumps(task_data).replace("</script>", "<\\/script>")
    total_pushable = len(task_data)

    # ── Digest cards (read-only) ─────────────────────────────────────────────
    digest_html = ""
    for item in digest:
        badge    = PRIORITY_BADGE.get(item.get("priority", "informational"), PRIORITY_BADGE["informational"])
        is_read  = item.get("is_read", True)
        is_sent  = item.get("is_sent", False)
        action   = item.get("action", "")
        folder   = item.get("folder", "")
        unread   = " ep-card-unread" if not is_read and not is_sent else ""
        sent_tag = '<span class="ep-sent-tag">↑ Sent</span>' if is_sent else ""
        fold_tag = f'<span class="ep-folder-tag">{folder}</span>' if folder else ""
        act_tag  = f'<div class="ep-action-tag">→ {action}</div>' if action else ""
        digest_html += f"""
        <div class="ep-card{unread}">
            <div class="ep-meta">{badge}{sent_tag}{fold_tag}</div>
            <div class="ep-from">{_html.escape(item.get("from_name",""))}</div>
            <div class="ep-subject">{_html.escape(item.get("subject",""))}</div>
            <div class="ep-summary">{_html.escape(item.get("summary",""))}</div>
            {act_tag}
        </div>"""

    # ── Helper: build a Microsoft To Do deep-link for a task ────────────────
    def _todo_link(title: str, detail: str = "") -> str:
        """
        Returns a web URL that opens Microsoft To Do (web or app) with the
        task pre-filled. Uses the https://to-do.microsoft.com/tasks/add
        endpoint which works in any browser on any device — desktop, mobile,
        or the GitHub Pages briefing — with no app or protocol handler needed.
        """
        from urllib.parse import quote
        today = datetime.date.today().isoformat()
        # Append a trimmed context note to the body if available
        full_title = title[:255]
        params = f"title={quote(full_title)}&dueDate={today}"
        if detail:
            body_text = detail[:500].rstrip()
            params += f"&body={quote(body_text)}"
        return f"https://to-do.microsoft.com/tasks/add?{params}"

    # ── Action rows (with checkboxes + deep link) ────────────────────────────
    actions_html = ""
    for i, item in enumerate(actions):
        badge    = URGENCY_BADGE.get(item.get("priority", "normal"), URGENCY_BADGE["normal"])
        deadline = item.get("deadline", "")
        dl_tag   = f'<span class="ep-deadline">⏰ {deadline}</span>' if deadline else ""
        ref      = item.get("from_email", "")
        tid      = f"action_{i}"
        title    = item.get("action", "")
        detail   = item.get("context", "")
        todo_url = _todo_link(title, detail)
        actions_html += f"""
        <div class="ep-todo-card" id="card-{tid}">
            <div class="ep-todo-body">
                <div class="ep-action-title">{badge} {_html.escape(title)} {dl_tag}</div>
                <div class="ep-action-context">{_html.escape(detail)}</div>
                {f'<div class="ep-action-ref">Re: {_html.escape(ref)}</div>' if ref else ""}
            </div>
            <div class="ep-todo-result">
                <a class="ep-todo-deeplink" href="{todo_url}" target="_blank" rel="noopener" title="Add to Microsoft To Do">📋</a>
            </div>
        </div>"""

    # ── People cards (with checkboxes + deep link) ───────────────────────────
    people_html = ""
    for i, item in enumerate(people):
        act   = item.get("action", "contact")
        icon  = "📅" if act == "schedule-meeting" else ("🔄" if act == "follow-up" else "💬")
        label = {"schedule-meeting": "Schedule meeting",
                 "follow-up":        "Follow up",
                 "contact":          "Contact"}.get(act, act)
        timing     = item.get("suggested_timing", "")
        context    = item.get("context", "")
        email_addr = item.get("email", "")
        name       = item.get("name", "")
        tid        = f"person_{i}"
        p_title    = f"{label}: {name}" if name else label
        p_detail   = item.get("reason", "")
        todo_url   = _todo_link(p_title, p_detail)
        people_html += f"""
        <div class="ep-todo-card" id="card-{tid}">
            <div class="ep-todo-body">
                <div class="ep-person-name">
                    <span class="ep-person-icon">{icon}</span>
                    {_html.escape(name)}
                    <span class="ep-person-act">{label}</span>
                </div>
                {f'<div class="ep-person-email">{_html.escape(email_addr)}</div>' if email_addr else ""}
                <div class="ep-person-reason">{_html.escape(p_detail)}</div>
                {f'<div class="ep-person-timing">⏰ {_html.escape(timing)}</div>' if timing else ""}
                {f'<div class="ep-person-context">Re: {_html.escape(context)}</div>' if context else ""}
            </div>
            <div class="ep-todo-result">
                <a class="ep-todo-deeplink" href="{todo_url}" target="_blank" rel="noopener" title="Add to Microsoft To Do">📋</a>
            </div>
        </div>"""

    if not digest and not actions and not people:
        return f'<div style="padding:3rem;text-align:center;color:#888">{empty_msg}</div>'

    return f"""
<div class="email-view">
    <section>
        <div class="ep-panel-title">📧 Priority Digest <span class="ep-count">{len(digest)}</span></div>
        {digest_html or '<p class="ep-empty">No priority emails found.</p>'}
    </section>
    <section>
        <div class="ep-panel-title">
            ✅ Actions for Today
            <span class="ep-count">{len(actions)}</span>
        </div>
        {actions_html or '<p class="ep-empty">No actions identified.</p>'}
    </section>
    <section>
        <div class="ep-panel-title">
            👥 People &amp; Meetings
            <span class="ep-count">{len(people)}</span>
        </div>
        {people_html or '<p class="ep-empty">No contacts or meetings identified.</p>'}
    </section>
</div>"""

def generate_html(sections: dict, generated_at: datetime.datetime,
                   active_topics=None, email_analysis=None, market_data=None,
                   topic_stories=None, asx_data=None, weather_data=None,
                   sunshine_data=None, gas_data=None, hh_gas_data=None,
                   calendar_data=None) -> str:
    date_str      = generated_at.strftime("%A, %#d %B %Y")
    time_str      = generated_at.strftime("%H:%M AEST")
    columns       = "".join(
        column_html(name, CATEGORIES[name], stories)
        for name, stories in sections.items()
        if stories
    )
    market_html    = market_widgets_html(market_data or [])
    asx_html       = asx_watchlist_html(asx_data or [])
    weather_html   = weather_bar_html(weather_data or [], sunshine_data or [], gas_data or {}, hh_gas_data or {})
    email_count    = len((email_analysis or {}).get('digest', []))
    action_count   = len((email_analysis or {}).get('actions', []))
    email_tab_html  = _build_email_tab(email_analysis or {})
    # Calendar tab — built from pre-fetched calendar_data dict
    _cal = calendar_data or {}
    calendar_tab_html = _cal.get("_html", "") if _cal else ""
    cal_yest_count    = len(_cal.get("yesterday", []))
    cal_today_count   = len(_cal.get("today", []))
    cal_tmrw_count    = len(_cal.get("tomorrow", []))
    cal_total         = cal_yest_count + cal_today_count + cal_tmrw_count
    topic_tabs_html = _build_topic_tab_views_with_stories(active_topics or [], topic_stories or {})
    # topic_tabs_html produces a single #view-topics div
    # Build topic tab buttons for masthead
    has_topics     = bool(active_topics)
    topic_emojis   = " ".join(t["emoji"] for t in (active_topics or []))
    topic_tab_btns = (
        f'<button class="tab-btn" onclick="showTab(\'topics\')" id="tab-topics">📌 My Topics {topic_emojis}</button>'
        if has_topics else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Morning Briefing — {date_str}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Sans+3:wght@300;400;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

        :root {{
            --ink:          #1a1a1a;
            --ink-light:    #666;
            --paper:        #f5f2eb;
            --paper-2:      #edeae0;
            --rule:         #cdc8b5;
            --accent:       #c0392b;
            --white:        #ffffff;
            --font-display: 'Playfair Display', Georgia, serif;
            --font-body:    'Source Sans 3', 'Helvetica Neue', sans-serif;
        }}

        html {{ scroll-behavior: smooth; }}
        body {{ background: var(--paper); color: var(--ink); font-family: var(--font-body); font-size: 15px; line-height: 1.55; min-height: 100vh; }}

        /* ── MASTHEAD ── */
        .masthead {{
            background: var(--ink); color: var(--white);
            padding: 1rem 1.5rem; display: flex; align-items: baseline;
            gap: 1.25rem; border-bottom: 3px solid var(--accent);
            position: relative; overflow: hidden;
        }}
        .masthead::before {{
            content: ''; position: absolute; inset: 0;
            background: repeating-linear-gradient(0deg, transparent, transparent 19px, rgba(255,255,255,0.025) 19px, rgba(255,255,255,0.025) 20px);
            pointer-events: none;
        }}
        .masthead-title {{ font-family: var(--font-display); font-size: 1.9rem; font-weight: 900; letter-spacing: -0.02em; line-height: 1; white-space: nowrap; position: relative; }}
        .masthead-date {{ font-family: var(--font-display); font-style: italic; font-size: 0.9rem; color: rgba(255,255,255,0.5); position: relative; }}
        .masthead-generated {{ margin-left: auto; font-size: 0.62rem; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.25); white-space: nowrap; position: relative; }}

        /* ── TOP WIDGET BAR ── */
        .widget-bar {{
            background: var(--paper-2); border-bottom: 2px solid var(--rule);
            padding: 0.3rem 1rem; display: flex; align-items: center;
            gap: 0.3rem; min-height: 2.2rem;
            overflow-x: auto; overflow-y: visible;
            flex-wrap: nowrap;
        }}
        .widget-slot {{
            display: flex; align-items: center; gap: 0.3rem;
            background: var(--white); border: 1px solid var(--rule); border-radius: 3px;
            padding: 0.15rem 0.45rem; font-size: 0.62rem; white-space: nowrap; flex-shrink: 0;
        }}
        .widget-slot-placeholder {{ border-style: dashed; background: transparent; color: var(--rule); font-style: italic; }}
        .widget-slot .label {{ font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; font-size: 0.55rem; color: var(--ink-light); }}
        .widget-slot .value {{ font-weight: 600; color: var(--ink); font-size: 0.68rem; }}
        .widget-slot .change.up {{ color: #27ae60; font-size: 0.62rem; }}
        .widget-slot .change.down {{ color: #c0392b; font-size: 0.62rem; }}
        .widget-divider {{ width: 1px; height: 1.1rem; background: var(--rule); flex-shrink: 0; align-self: center; }}
        .widget-topic-link {{ text-decoration: none; transition: border-color 0.12s, background 0.12s; }}
        .widget-asx-link {{ text-decoration: none; transition: border-color 0.12s, background 0.12s; cursor: pointer; }}
        .widget-asx-link:hover {{ background: var(--ink); border-color: var(--ink); }}
        .widget-asx-link:hover .label {{ color: rgba(255,255,255,0.6); }}
        .widget-asx-link:hover .value {{ color: var(--white); }}
        .widget-asx-link:hover .change {{ opacity: 0.85; }}
        .widget-topic-link:hover {{ background: var(--ink); color: var(--white); border-color: var(--ink); }}
        .widget-topic-link:hover .label {{ color: rgba(255,255,255,0.6); }}
        .widget-topic-link:hover .value {{ color: var(--white); }}

        /* ── WEATHER / INFO BAR ── */
        .weather-bar {{
            background: var(--paper-2); border-bottom: 2px solid var(--rule);
            display: flex; align-items: stretch;
            min-height: 3.4rem; overflow: hidden;
        }}
        .wx-section {{
            display: flex; align-items: center; gap: 0;
            flex: 1; overflow: hidden; padding: 0 0.5rem;
        }}
        .wx-section-gas {{
            flex: 0 0 auto; min-width: 7rem;
            justify-content: center;
        }}
        .wx-divider {{
            width: 1px; background: var(--rule); flex-shrink: 0; align-self: stretch;
        }}
        .wx-section-label {{
            font-size: 0.55rem; font-weight: 700; letter-spacing: 0.12em;
            text-transform: uppercase; color: var(--ink-light);
            writing-mode: vertical-rl; text-orientation: mixed;
            transform: rotate(180deg);
            padding: 0.4rem 0.3rem; flex-shrink: 0;
            border-right: 1px solid var(--rule); margin-right: 0.35rem;
        }}
        .wx-slots {{
            display: flex; align-items: center; gap: 0; overflow-x: auto;
            scrollbar-width: none; flex: 1;
        }}
        .wx-slots::-webkit-scrollbar {{ display: none; }}
        .wx-slot {{
            display: flex; flex-direction: column; align-items: center;
            gap: 0.05rem; padding: 0.2rem 0.6rem;
            border-right: 1px solid var(--rule); flex-shrink: 0; min-width: 52px;
        }}
        .wx-slot:last-child {{ border-right: none; }}
        .wx-today {{ background: rgba(192,57,43,0.06); border-radius: 2px; }}
        .wx-day {{
            font-size: 0.55rem; font-weight: 700; letter-spacing: 0.07em;
            text-transform: uppercase; color: var(--ink-light); line-height: 1;
        }}
        .wx-today .wx-day {{ color: var(--accent); }}
        .wx-icon {{ font-size: 1.05rem; line-height: 1.3; }}
        .wx-temps {{ display: flex; gap: 0.25rem; align-items: baseline; }}
        .wx-high {{ font-size: 0.7rem; font-weight: 700; color: var(--ink); }}
        .wx-low  {{ font-size: 0.62rem; color: var(--ink-light); }}
        .wx-rain {{ font-size: 0.55rem; color: #2980b9; font-weight: 600; line-height: 1; min-height: 0.75rem; }}
        .wx-gas-slot {{ border-right: none; min-width: auto; padding: 0.3rem 0.5rem; }}
        .wx-gas-price {{ font-size: 1.05rem; font-weight: 700; color: var(--ink); line-height: 1.2; }}
        .weather-unavailable {{ font-size: 0.7rem; color: var(--ink-light); font-style: italic; padding: 0.3rem; }}

        /* ── MASTHEAD TABS ── */
        .masthead {{ flex-wrap: wrap; gap: 0.5rem; padding: 0.65rem 1.25rem; }}
        .masthead-left {{ display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; }}
        .masthead-title {{ font-size: 1.05rem; white-space: nowrap; }}
        .masthead-date {{ font-family: var(--font-display); font-style: italic; font-size: 0.68rem; color: rgba(255,255,255,0.45); position: relative; }}
        .masthead-tabs {{ display: flex; gap: 0.3rem; align-items: center; flex-wrap: wrap; flex: 1; justify-content: flex-end; }}
        .tab-btn {{
            font-family: var(--font-body); font-size: 0.62rem; font-weight: 700;
            letter-spacing: 0.03em; text-transform: uppercase;
            padding: 0.22rem 0.5rem; border-radius: 3px; cursor: pointer;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.55);
            transition: all 0.15s; white-space: nowrap;
        }}
        @media (max-width: 600px) {{
            .masthead-tabs {{ justify-content: flex-start; width: 100%; }}
            .tab-btn {{ font-size: 0.58rem; padding: 0.2rem 0.4rem; }}
        }}
        .tab-btn:hover {{ background: rgba(255,255,255,0.18); color: rgba(255,255,255,0.9); }}
        .tab-active {{ background: var(--accent) !important; color: var(--white) !important; border-color: var(--accent) !important; }}

        /* ── EMAIL TAB LAYOUT (mirrors email.html) ── */
        .email-view {{ max-width: 1200px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 1.5rem; align-items: start; }}
        .ep-panel-title {{ font-family: var(--font-display); font-size: 1rem; font-weight: 700; padding-bottom: 0.5rem; border-bottom: 3px solid var(--ink); margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem; }}
        .ep-count {{ font-size: 0.62rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-light); background: var(--paper-2); border: 1px solid var(--rule); padding: 0.12rem 0.4rem; border-radius: 2rem; margin-left: auto; }}
        /* email digest cards */
        .ep-card {{ background: var(--white); border: 1px solid var(--rule); border-radius: 3px; padding: 0.85rem 1rem; margin-bottom: 0.5rem; transition: box-shadow .12s; }}
        .ep-card:hover {{ box-shadow: 0 1px 5px rgba(0,0,0,.08); }}
        .ep-card-unread {{ border-left: 3px solid #2980b9; }}
        .ep-meta {{ display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem; flex-wrap: wrap; }}
        .ep-from {{ font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: var(--ink-light); margin-bottom: 0.2rem; }}
        .ep-subject {{ font-family: var(--font-display); font-size: 0.9rem; font-weight: 700; line-height: 1.3; margin-bottom: 0.3rem; }}
        .ep-summary {{ font-size: 0.78rem; color: #444; line-height: 1.6; }}
        .ep-action-tag {{ margin-top: 0.4rem; font-size: 0.68rem; font-weight: 700; color: var(--accent); }}
        .ep-folder-tag {{ font-size: 0.58rem; color: var(--ink-light); background: var(--paper-2); padding: 0.1rem 0.35rem; border-radius: 2px; margin-left: auto; }}
        .ep-sent-tag {{ font-size: 0.6rem; font-weight: 700; color: #888; background: #f0f0f0; padding: 0.1rem 0.35rem; border-radius: 2px; }}
        /* action rows */
        .ep-action-row {{ display: flex; gap: 0.75rem; padding: 0.75rem 0.9rem; background: var(--white); border: 1px solid var(--rule); border-radius: 3px; margin-bottom: 0.5rem; }}
        .ep-action-num {{ font-family: var(--font-display); font-size: 1.2rem; font-weight: 900; color: var(--rule); line-height: 1; padding-top: 0.1rem; flex-shrink: 0; width: 1.2rem; text-align: center; }}
        .ep-action-title {{ font-size: 0.85rem; font-weight: 600; line-height: 1.35; margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }}
        .ep-action-context {{ font-size: 0.75rem; color: #555; line-height: 1.55; }}
        .ep-action-ref {{ font-size: 0.65rem; color: var(--ink-light); margin-top: 0.2rem; font-style: italic; }}
        .ep-deadline {{ font-size: 0.6rem; font-weight: 700; color: #c0392b; background: #fde8e8; padding: 0.1rem 0.4rem; border-radius: 2px; }}
        /* people cards */
        .ep-person {{ display: flex; gap: 0.75rem; padding: 0.8rem 0.9rem; background: var(--white); border: 1px solid var(--rule); border-radius: 3px; margin-bottom: 0.5rem; }}
        .ep-person-icon {{ font-size: 1.3rem; line-height: 1; flex-shrink: 0; padding-top: 0.1rem; }}
        .ep-person-name {{ font-size: 0.88rem; font-weight: 700; margin-bottom: 0.15rem; display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }}
        .ep-person-act {{ font-size: 0.6rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--white); background: var(--ink); padding: 0.1rem 0.4rem; border-radius: 2px; opacity: 0.75; }}
        .ep-person-email {{ font-size: 0.68rem; color: var(--ink-light); margin-bottom: 0.25rem; }}
        .ep-person-reason {{ font-size: 0.78rem; color: #444; line-height: 1.55; }}
        .ep-person-timing {{ font-size: 0.68rem; font-weight: 700; color: #27ae60; margin-top: 0.25rem; }}
        .ep-person-context {{ font-size: 0.65rem; color: var(--ink-light); margin-top: 0.15rem; font-style: italic; }}
        .ep-empty {{ font-size: 0.85rem; color: var(--ink-light); padding: 1rem 0; }}
        @media (max-width: 900px) {{ .email-view {{ grid-template-columns: 1fr; }} }}

        /* ── TO DO CHECKBOX CARDS ── */
        .ep-todo-card {{
            display: flex; align-items: flex-start; gap: 0.6rem;
            background: var(--white); border: 1px solid var(--rule); border-radius: 3px;
            padding: 0.75rem 0.9rem; margin-bottom: 0.5rem;
            transition: border-color 0.12s, background 0.12s;
        }}
        .ep-todo-body {{ flex: 1; min-width: 0; }}
        .ep-todo-result {{
            font-size: 0.72rem; font-weight: 700; white-space: nowrap;
            padding-top: 0.15rem; flex-shrink: 0; text-align: right;
        }}
        .ep-todo-result .result-ok   {{ color: #27ae60; }}
        .ep-todo-result .result-fail {{ color: #c0392b; }}
        .ep-todo-deeplink {{
            font-size: 0.95rem; text-decoration: none; opacity: 0.45;
            transition: opacity 0.15s; display: block; line-height: 1;
            padding: 0.1rem;
        }}
        .ep-todo-deeplink:hover {{ opacity: 1; }}


        /* ── THREE-COLUMN LAYOUT ── */
        .columns-wrapper {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1px; background: var(--rule);
            min-height: calc(100vh - 8rem);
        }}
        .briefing-column {{ background: var(--paper); display: flex; flex-direction: column; min-width: 0; }}

        .column-header {{
            display: flex; align-items: center; gap: 0.5rem;
            padding: 0.75rem 0.9rem 0.6rem; background: var(--white);
            border-bottom: 3px solid var(--section-color, var(--ink));
            position: sticky; top: 0; z-index: 10;
        }}
        .column-emoji {{ font-size: 1rem; line-height: 1; flex-shrink: 0; }}
        .column-title {{ font-family: var(--font-display); font-size: 0.88rem; font-weight: 700; line-height: 1.2; flex: 1; min-width: 0; }}
        .story-count {{ font-size: 0.6rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-light); background: var(--paper-2); border: 1px solid var(--rule); padding: 0.12rem 0.4rem; border-radius: 2rem; flex-shrink: 0; }}

        /* ── STORY CARDS ── */
        .stories-list {{ display: flex; flex-direction: column; gap: 1px; background: var(--rule); flex: 1; }}
        .story-card {{ background: var(--white); padding: 0.85rem 0.9rem; animation: fadeUp 0.35s ease both; animation-delay: var(--delay, 0s); transition: background 0.12s; }}
        .story-card:hover {{ background: #fdfcf9; }}

        @keyframes fadeUp {{ from {{ opacity: 0; transform: translateY(5px); }} to {{ opacity: 1; transform: translateY(0); }} }}

        .story-meta {{ display: flex; align-items: center; gap: 0.45rem; margin-bottom: 0.3rem; flex-wrap: wrap; }}
        .badge {{ font-size: 0.58rem; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase; padding: 0.12rem 0.4rem; border-radius: 2px; flex-shrink: 0; }}
        .badge-critical {{ background: #fde8e8; color: #c0392b; }}
        .badge-major    {{ background: #fef3e2; color: #d35400; }}
        .badge-notable  {{ background: #e8f4e8; color: #27ae60; }}
        .story-source {{ font-size: 0.6rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--ink-light); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .story-headline {{ font-family: var(--font-display); font-size: 0.87rem; font-weight: 700; line-height: 1.3; margin-bottom: 0.3rem; }}
        .story-headline a {{ color: var(--ink); text-decoration: none; }}
        .story-headline a:hover {{ color: var(--accent); text-decoration: underline; }}
        .story-summary {{ font-size: 0.77rem; color: #484848; line-height: 1.6; }}

        /* ── TOPIC TAB HEADER ── */
        .topic-tab-header {{
            display: flex; align-items: center; gap: 0.6rem;
            padding: 0.7rem 1.25rem; background: var(--white);
            border-bottom: 3px solid var(--tc, var(--ink));
            font-family: var(--font-display); font-size: 1rem; font-weight: 700;
        }}
        .topic-tab-count {{ font-size: 0.62rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-light); background: var(--paper-2); border: 1px solid var(--rule); padding: 0.12rem 0.4rem; border-radius: 2rem; margin-left: auto; }}


        /* ── CALENDAR TAB ── */
        .cal-view {{ padding: 0; }}
        .cal-day-section {{ margin-bottom: 0; border-bottom: 1px solid var(--rule); }}
        .cal-day-header {{
            display: flex; align-items: baseline; gap: 0.75rem;
            padding: 0.65rem 1.25rem;
            background: var(--paper-2);
            border-bottom: 1px solid var(--rule);
            position: sticky; top: 0; z-index: 10;
        }}
        .cal-day-label {{
            font-family: var(--font-display); font-size: 0.75rem;
            font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase;
            color: var(--ink);
        }}
        .cal-day-date {{ font-size: 0.78rem; color: var(--ink-light); }}
        .cal-day-count {{
            margin-left: auto; font-size: 0.62rem; font-weight: 700;
            letter-spacing: 0.1em; text-transform: uppercase;
            color: var(--ink-light); background: var(--white);
            border: 1px solid var(--rule); padding: 0.12rem 0.5rem;
            border-radius: 2rem;
        }}
        .cal-events {{ padding: 0.5rem 0; }}
        .cal-event {{
            display: flex; gap: 1rem; align-items: flex-start;
            padding: 0.7rem 1.25rem;
            border-bottom: 1px solid var(--rule);
            transition: background 0.15s;
        }}
        .cal-event:last-child {{ border-bottom: none; }}
        .cal-event:hover {{ background: var(--paper-2); }}
        .cal-event-past {{ opacity: 0.45; }}
        .cal-event-time {{
            min-width: 90px; flex-shrink: 0;
            display: flex; flex-direction: column; gap: 2px;
            padding-top: 1px;
        }}
        .cal-start {{ font-size: 0.82rem; font-weight: 700; color: var(--ink); }}
        .cal-end   {{ font-size: 0.72rem; color: var(--ink-light); }}
        .cal-dur   {{
            font-size: 0.62rem; font-weight: 700; letter-spacing: 0.08em;
            text-transform: uppercase; color: var(--ink-light);
            background: var(--paper-2); border: 1px solid var(--rule);
            padding: 0.08rem 0.35rem; border-radius: 3px; align-self: flex-start;
            margin-top: 2px;
        }}
        .cal-all-day {{
            font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em;
            text-transform: uppercase; color: var(--ink-light);
        }}
        .cal-event-body  {{ flex: 1; min-width: 0; }}
        .cal-event-subject {{
            font-size: 0.88rem; font-weight: 700; color: var(--ink);
            line-height: 1.3; margin-bottom: 4px;
        }}
        .cal-event-meta {{
            font-size: 0.75rem; color: var(--ink-light); margin-bottom: 3px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }}
        .cal-join-link {{ color: #2980b9; text-decoration: none; font-weight: 600; }}
        .cal-join-link:hover {{ text-decoration: underline; }}
        .cal-event-preview {{
            font-size: 0.74rem; color: var(--ink-light);
            display: -webkit-box; -webkit-line-clamp: 2;
            -webkit-box-orient: vertical; overflow: hidden;
            margin-top: 3px; line-height: 1.4;
        }}
        .cal-badge {{
            display: inline-block; font-size: 0.58rem; font-weight: 700;
            letter-spacing: 0.08em; text-transform: uppercase;
            padding: 0.1rem 0.4rem; border-radius: 3px;
            vertical-align: middle; margin-left: 5px;
        }}
        .cal-badge-accepted  {{ background: #eafaf1; color: #27ae60; border: 1px solid #a9dfbf; }}
        .cal-badge-declined  {{ background: #fdecea; color: #c0392b; border: 1px solid #f1948a; }}
        .cal-badge-tentative {{ background: #fef9e7; color: #b7770d; border: 1px solid #f9e79f; }}
        .cal-badge-pending   {{ background: var(--paper-2); color: var(--ink-light); border: 1px solid var(--rule); }}
        .cal-badge-organizer {{ background: #eaf2ff; color: #2471a3; border: 1px solid #aed6f1; }}
        .cal-empty {{
            padding: 1.5rem 1.25rem; font-size: 0.82rem;
            color: var(--ink-light); font-style: italic;
        }}
        .cal-error {{ padding: 1.5rem 1.25rem; color: #c0392b; font-size: 0.85rem; }}
        .cal-error-hint {{ color: var(--ink-light); font-size: 0.8rem; margin-top: 0.5rem; }}
        .cal-error code {{
            background: var(--paper-2); padding: 0.1rem 0.3rem;
            border-radius: 3px; font-family: monospace; font-size: 0.85em;
        }}
        /* ── AI Briefing column ── */
        .cal-brief-col {{
            flex-shrink: 0; width: 240px;
            border-left: 2px solid var(--rule);
            padding: 0 0 0 0.9rem;
            align-self: stretch;
            display: flex; flex-direction: column; gap: 0.3rem;
        }}
        .cal-brief-label {{
            font-size: 0.58rem; font-weight: 800; letter-spacing: 0.1em;
            text-transform: uppercase; color: var(--ink-light);
            margin-bottom: 0.2rem;
        }}
        .cal-brief-list {{
            margin: 0; padding: 0 0 0 1rem; list-style: disc;
        }}
        .cal-brief-bullet {{
            font-size: 0.74rem; color: var(--ink); line-height: 1.45;
            margin-bottom: 0.25rem;
        }}
        .cal-brief-bullet:last-child {{ margin-bottom: 0; }}
        @media (max-width: 900px) {{
            .cal-brief-col {{ width: 100%; border-left: none;
                border-top: 1px solid var(--rule); padding: 0.6rem 0 0 0; }}
        }}

        @media (max-width: 700px) {{
            .cal-event {{ flex-direction: column; gap: 0.3rem; }}
            .cal-event-time {{ min-width: unset; flex-direction: row; align-items: center; gap: 0.5rem; }}
        }}

        /* ── FOOTER ── */
        footer {{ background: var(--ink); color: rgba(255,255,255,0.28); text-align: center; font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase; padding: 0.9rem 1.5rem; }}

        /* ── RESPONSIVE ── */
        @media (max-width: 860px) {{
            .columns-wrapper {{ grid-template-columns: 1fr; }}
            .column-header {{ position: relative; }}
        }}

        /* ── TOPICS TAB — responsive columns ── */
        .topics-tab-grid {{
            display: grid;
            grid-template-columns: repeat(5, minmax(180px, 1fr));
            gap: 1px;
            background: var(--rule);
            min-height: calc(100vh - 8rem);
        }}
        @media (max-width: 1200px) {{
            .topics-tab-grid {{ grid-template-columns: repeat(3, 1fr); }}
        }}
        @media (max-width: 860px) {{
            .topics-tab-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}
        @media (max-width: 540px) {{
            .topics-tab-grid {{ grid-template-columns: 1fr; }}
            .topics-tab-grid .column-header {{ position: relative; top: auto; }}
        }}
        @media (max-width: 600px) {{
            .masthead {{ flex-wrap: wrap; gap: 0.35rem; }}
            .masthead-generated {{ margin-left: 0; width: 100%; }}
        }}
        @media print {{
            .widget-bar, footer {{ display: none; }}
            .columns-wrapper {{ gap: 0; background: none; }}
            .story-card {{ break-inside: avoid; }}
        }}
    </style>
</head>
<body>

<header class="masthead">
    <div class="masthead-left">
        <h1 class="masthead-title">Doug's Morning Briefing</h1>
        <span class="masthead-date">{date_str} &mdash; {time_str}</span>
    </div>
    <nav class="masthead-tabs">
        <button class="tab-btn tab-active" onclick="showTab('news')" id="tab-news">📰 News</button>
        <button class="tab-btn" onclick="showTab('email')" id="tab-email">⚡ Work Actions{"" if not email_count else f" ({email_count})"}</button>
        <button class="tab-btn" onclick="showTab('calendar')" id="tab-calendar">📅 Calendar{"" if not cal_total else f" ({cal_today_count}✦{cal_tmrw_count})"}</button>
        {topic_tab_btns}
    </nav>
</header>

<!-- ── TOP WIDGET BAR ── -->
<div class="widget-bar" id="widget-bar">
    {market_html}
    <div class="widget-divider"></div>
    {asx_html}
</div>

<!-- ── WEATHER BAR ── -->
{weather_html}

<!-- ── NEWS TAB ── -->
<div id="view-news">
<div class="columns-wrapper">
    {columns}
</div>
</div>

<!-- ── EMAIL TAB ── -->
<div id="view-email" style="display:none">
{email_tab_html}
</div>

<!-- ── CALENDAR TAB ── -->
<div id="view-calendar" style="display:none">
{calendar_tab_html}
</div>


<!-- ── TOPIC TABS ── -->
{topic_tabs_html}

<footer>
    Morning Briefing &mdash; Generated automatically &mdash; {date_str}
</footer>

<script>
// ── Detect whether the local review server is running ──
// The /push endpoint only exists at localhost:8765-8767.
// Everywhere else (Gmail, GitHub Pages, phone, file://) hide the
// bulk Push footer and checkboxes — use the per-item 📋 deep links instead.
(function() {{
    var h = window.location.hostname, p = window.location.port;
    var isReviewServer = (h === 'localhost' || h === '127.0.0.1') &&
                         (p === '8765' || p === '8766' || p === '8767');
    if (!isReviewServer) {{
        function hidePush() {{
            var f = document.getElementById('todo-footer');
            if (f) f.style.display = 'none';
            [].forEach.call(document.querySelectorAll('.ep-todo-check'), function(el) {{ el.style.display='none'; }});
            [].forEach.call(document.querySelectorAll('.ep-sel-all'),    function(el) {{ el.style.display='none'; }});
        }}
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', hidePush);
        }} else {{
            hidePush();
        }}
    }}
}})();

// ── Tab switching ──
function showTab(tab) {{
    // Hide all views
    ['view-news','view-email','view-calendar'].forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }});
    const topicsView = document.getElementById('view-topics');
    if (topicsView) topicsView.style.display = 'none';
    // Deactivate all tab buttons
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('tab-active'));
    // Show selected view and activate button
    const view = document.getElementById('view-' + tab);
    if (view) view.style.display = '';
    const btn = document.getElementById('tab-' + tab);
    if (btn) btn.classList.add('tab-active');
    // Show To Do footer only on email tab AND only on local review server
    var h = window.location.hostname, p = window.location.port;
    var isReviewServer = (h === 'localhost' || h === '127.0.0.1') &&
                         (p === '8765' || p === '8766' || p === '8767');
    const footer = document.getElementById('todo-footer');
    if (footer) footer.style.display = (tab === 'email' && isReviewServer) ? 'flex' : 'none';
}}


// All market and stock data embedded at generation time
</script>

</body>
</html>"""



# ─────────────────────────────────────────────
# EMAIL PAGE
# ─────────────────────────────────────────────

PRIORITY_BADGE = {
    "action-required": '<span class="badge badge-critical">⚡ Action</span>',
    "important":       '<span class="badge badge-major">● Important</span>',
    "informational":   '<span class="badge badge-notable">◦ Info</span>',
}
URGENCY_BADGE = {
    "urgent": '<span class="badge badge-critical">⚡ Urgent</span>',
    "high":   '<span class="badge badge-major">● High</span>',
    "normal": '<span class="badge badge-notable">◦ Normal</span>',
}


def generate_email_page(analysis: dict, generated_at: datetime.datetime) -> str:
    date_str = generated_at.strftime("%A, %#d %B %Y")
    time_str = generated_at.strftime("%H:%M AEST")
    digest   = analysis.get("digest", [])
    actions  = analysis.get("actions", [])
    people   = analysis.get("people", [])

    # ── Digest cards ──
    digest_cards = ""
    for item in digest:
        priority  = item.get("priority", "informational").lower()
        badge     = PRIORITY_BADGE.get(priority, PRIORITY_BADGE["informational"])
        subject   = item.get("subject", "")
        from_name = item.get("from_name", item.get("from_email", ""))
        summary   = item.get("summary", "")
        action    = item.get("action", "")
        folder    = item.get("folder", "")
        is_sent   = item.get("is_sent", False)
        is_read   = item.get("is_read", True)
        action_html  = f'<div class="email-action-tag">→ {action}</div>' if action else ""
        folder_html  = f'<span class="email-folder-tag">{folder}</span>' if folder else ""
        sent_tag     = '<span class="sent-tag">↑ Sent</span>' if is_sent else ""
        unread_class = " card-unread" if not is_read and not is_sent else ""
        digest_cards += f"""
        <div class="email-card{unread_class}">
            <div class="card-meta">{badge}{sent_tag}{folder_html}</div>
            <div class="card-from">{from_name}</div>
            <div class="card-subject">{subject}</div>
            <div class="card-summary">{summary}</div>
            {action_html}
        </div>"""

    # ── Action items ──
    action_rows = ""
    for i, item in enumerate(actions):
        urgency  = item.get("priority", "normal").lower()
        badge    = URGENCY_BADGE.get(urgency, URGENCY_BADGE["normal"])
        action   = item.get("action", "")
        context  = item.get("context", "")
        deadline = item.get("deadline", "")
        ref      = item.get("from_email", "")
        deadline_html = f'<span class="deadline-tag">⏰ {deadline}</span>' if deadline else ""
        action_rows += f"""
        <div class="action-row">
            <div class="action-num">{i+1}</div>
            <div class="action-body">
                <div class="action-title">{badge} {action} {deadline_html}</div>
                <div class="action-context">{context}</div>
                {f'<div class="action-ref">Re: {ref}</div>' if ref else ""}
            </div>
        </div>"""

    # ── People / meetings ──
    people_cards = ""
    for item in people:
        name    = item.get("name", "")
        email   = item.get("email", "")
        act     = item.get("action", "contact")
        reason  = item.get("reason", "")
        timing  = item.get("suggested_timing", "")
        context = item.get("context", "")
        icon    = "📅" if act == "schedule-meeting" else ("🔄" if act == "follow-up" else "💬")
        act_label = {"schedule-meeting": "Schedule meeting", "follow-up": "Follow up", "contact": "Contact"}.get(act, act)
        people_cards += f"""
        <div class="person-card">
            <div class="person-icon">{icon}</div>
            <div class="person-body">
                <div class="person-name">{name} <span class="person-act-tag">{act_label}</span></div>
                {f'<div class="person-email">{email}</div>' if email else ""}
                <div class="person-reason">{reason}</div>
                {f'<div class="person-timing">⏰ {timing}</div>' if timing else ""}
                {f'<div class="person-context">Re: {context}</div>' if context else ""}
            </div>
        </div>"""

    counts = f"{len(digest)} emails · {len(actions)} actions · {len(people)} contacts/meetings"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email — Morning Brief {date_str}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Sans+3:wght@300;400;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        :root {{
            --ink: #1a1a1a; --ink-light: #666; --paper: #f5f2eb; --paper-2: #edeae0;
            --rule: #cdc8b5; --accent: #1a3050; --white: #fff;
            --font-display: 'Playfair Display', Georgia, serif;
            --font-body: 'Source Sans 3', sans-serif;
        }}
        body {{ background: var(--paper); color: var(--ink); font-family: var(--font-body); font-size: 15px; line-height: 1.6; }}

        /* masthead */
        .masthead {{ background: var(--ink); color: var(--white); padding: 1rem 1.5rem; display: flex; align-items: center; gap: 1.25rem; border-bottom: 3px solid var(--accent); position: relative; overflow: hidden; }}
        .masthead::before {{ content:''; position:absolute; inset:0; background: repeating-linear-gradient(0deg,transparent,transparent 19px,rgba(255,255,255,.025) 19px,rgba(255,255,255,.025) 20px); pointer-events:none; }}
        .back-link {{ font-size:.75rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:rgba(255,255,255,.45); text-decoration:none; position:relative; white-space:nowrap; }}
        .back-link:hover {{ color:rgba(255,255,255,.8); }}
        .masthead-title {{ font-family:var(--font-display); font-size:1.9rem; font-weight:900; letter-spacing:-.02em; line-height:1; position:relative; }}
        .masthead-right {{ margin-left:auto; display:flex; flex-direction:column; align-items:flex-end; gap:.15rem; position:relative; }}
        .masthead-date {{ font-family:var(--font-display); font-style:italic; font-size:.85rem; color:rgba(255,255,255,.5); }}
        .masthead-counts {{ font-size:.6rem; letter-spacing:.1em; text-transform:uppercase; color:rgba(255,255,255,.25); }}

        /* three panel layout */
        main {{ max-width: 1200px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 1.5rem; align-items: start; }}
        .panel-title {{ font-family:var(--font-display); font-size:1rem; font-weight:700; padding-bottom:.5rem; border-bottom:3px solid var(--accent); margin-bottom:1rem; display:flex; align-items:center; gap:.5rem; }}
        .panel-count {{ font-size:.62rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-light); background:var(--paper-2); border:1px solid var(--rule); padding:.12rem .4rem; border-radius:2rem; margin-left:auto; }}

        /* email digest cards */
        .email-card {{ background:var(--white); border:1px solid var(--rule); border-radius:3px; padding:.85rem 1rem; margin-bottom:.5rem; transition:box-shadow .12s; }}
        .email-card:hover {{ box-shadow:0 1px 5px rgba(0,0,0,.08); }}
        .card-unread {{ border-left:3px solid #2980b9; }}
        .card-meta {{ display:flex; align-items:center; gap:.4rem; margin-bottom:.3rem; flex-wrap:wrap; }}
        .card-from {{ font-size:.65rem; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:var(--ink-light); margin-bottom:.2rem; }}
        .card-subject {{ font-family:var(--font-display); font-size:.9rem; font-weight:700; line-height:1.3; margin-bottom:.3rem; }}
        .card-summary {{ font-size:.78rem; color:#444; line-height:1.6; }}
        .email-action-tag {{ margin-top:.4rem; font-size:.68rem; font-weight:700; color:var(--accent); }}
        .email-folder-tag {{ font-size:.58rem; color:var(--ink-light); background:var(--paper-2); padding:.1rem .35rem; border-radius:2px; margin-left:auto; }}
        .sent-tag {{ font-size:.6rem; font-weight:700; color:#888; background:#f0f0f0; padding:.1rem .35rem; border-radius:2px; }}

        /* action rows */
        .action-row {{ display:flex; gap:.75rem; padding:.75rem .9rem; background:var(--white); border:1px solid var(--rule); border-radius:3px; margin-bottom:.5rem; }}
        .action-num {{ font-family:var(--font-display); font-size:1.2rem; font-weight:900; color:var(--rule); line-height:1; padding-top:.1rem; flex-shrink:0; width:1.2rem; text-align:center; }}
        .action-title {{ font-size:.85rem; font-weight:600; line-height:1.35; margin-bottom:.25rem; display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; }}
        .action-context {{ font-size:.75rem; color:#555; line-height:1.55; }}
        .action-ref {{ font-size:.65rem; color:var(--ink-light); margin-top:.2rem; font-style:italic; }}
        .deadline-tag {{ font-size:.6rem; font-weight:700; color:#c0392b; background:#fde8e8; padding:.1rem .4rem; border-radius:2px; }}

        /* people cards */
        .person-card {{ display:flex; gap:.75rem; padding:.8rem .9rem; background:var(--white); border:1px solid var(--rule); border-radius:3px; margin-bottom:.5rem; }}
        .person-icon {{ font-size:1.3rem; line-height:1; flex-shrink:0; padding-top:.1rem; }}
        .person-name {{ font-size:.88rem; font-weight:700; margin-bottom:.15rem; display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; }}
        .person-act-tag {{ font-size:.6rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase; color:var(--white); background:var(--accent); padding:.1rem .4rem; border-radius:2px; opacity:.85; }}
        .person-email {{ font-size:.68rem; color:var(--ink-light); margin-bottom:.25rem; }}
        .person-reason {{ font-size:.78rem; color:#444; line-height:1.55; }}
        .person-timing {{ font-size:.68rem; font-weight:700; color:#27ae60; margin-top:.25rem; }}
        .person-context {{ font-size:.65rem; color:var(--ink-light); margin-top:.15rem; font-style:italic; }}

        /* shared badges */
        .badge {{ font-size:.58rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase; padding:.12rem .4rem; border-radius:2px; flex-shrink:0; }}
        .badge-critical {{ background:#fde8e8; color:#c0392b; }}
        .badge-major    {{ background:#fef3e2; color:#d35400; }}
        .badge-notable  {{ background:#e8f4e8; color:#27ae60; }}

        footer {{ background:var(--ink); color:rgba(255,255,255,.28); text-align:center; font-size:.65rem; letter-spacing:.1em; text-transform:uppercase; padding:.9rem 1.5rem; }}

        @media (max-width:900px) {{ main {{ grid-template-columns:1fr; }} }}
        @media (max-width:600px) {{ .masthead {{ flex-wrap:wrap; }} .masthead-right {{ margin-left:0; }} }}
    </style>
</head>
<body>

<header class="masthead">
    <a href="briefing.html" class="back-link">← Morning Brief</a>
    <h1 class="masthead-title">📬 Email</h1>
    <div class="masthead-right">
        <span class="masthead-date">{date_str}</span>
        <span class="masthead-counts">{counts}</span>
    </div>
</header>

<main>

    <!-- ── COLUMN 1: Priority Digest ── -->
    <section>
        <div class="panel-title">
            📧 Priority Digest
            <span class="panel-count">{len(digest)}</span>
        </div>
        {digest_cards if digest_cards else '<p style="color:var(--ink-light);font-size:.85rem">No priority emails found.</p>'}
    </section>

    <!-- ── COLUMN 2: Actions ── -->
    <section>
        <div class="panel-title">
            ✅ Actions for Today
            <span class="panel-count">{len(actions)}</span>
        </div>
        {action_rows if action_rows else '<p style="color:var(--ink-light);font-size:.85rem">No actions identified.</p>'}
    </section>

    <!-- ── COLUMN 3: People & Meetings ── -->
    <section>
        <div class="panel-title">
            👥 People &amp; Meetings
            <span class="panel-count">{len(people)}</span>
        </div>
        {people_cards if people_cards else '<p style="color:var(--ink-light);font-size:.85rem">No contacts or meetings identified.</p>'}
    </section>

</main>

<footer>
    Email Analysis — Morning Briefing — {date_str} — Generated {time_str}
</footer>

</body>
</html>"""

# ─────────────────────────────────────────────
# WATCH TOPICS
# ─────────────────────────────────────────────

# Generic broad feeds used to search for topic-specific stories
TOPIC_SEARCH_FEEDS = [
    # General news
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/science/rss",
    "https://www.theguardian.com/business/rss",
    "https://www.theguardian.com/environment/rss",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/reuters/technologyNews",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.abc.net.au/news/feed/2942460/rss.xml",
    "https://www.smh.com.au/rss/feed.xml",
    "https://www.afr.com/rss/feed",
    # Sport
    "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "https://www.theguardian.com/football/rss",
    "https://www.skysports.com/rss/12040",
    # Gaming
    "https://www.ign.com/rss/articles",
    "https://kotaku.com/rss",
    "https://www.eurogamer.net/?format=rss",
    "https://www.gamespot.com/feeds/mashup/",
    "https://www.pcgamer.com/rss/",
    "https://www.rockpapershotgun.com/feed",
    # Australian energy & resources
    "https://www.energymagazine.com.au/feed/",
    "https://www.naturalgasworld.com/rss",
    "https://www.upstreamonline.com/rss",
    "https://oilprice.com/rss/main",
    "https://feeds.reuters.com/reuters/energy",
    "https://www.theguardian.com/environment/energy/rss",
    "https://www.abc.net.au/news/business/rss.xml",
    # ASX & Australian markets
    "https://www.proactiveinvestors.com.au/rss/articles/latest.rss",
    "https://stockhead.com.au/feed/",
    "https://www.miningweekly.com/rss",
    "https://www.resourceworld.com/feed/",
    "https://au.investing.com/rss/news_285.rss",    # Investing.com AU energy
    "https://au.investing.com/rss/news_25.rss",     # Investing.com AU metals/mining
    # Queensland & Australian business
    "https://www.brisbanetimes.com.au/rss/feed.xml",
    "https://www.couriermail.com.au/feed",
    "https://www.theaustralian.com.au/feed",
    "https://www.businessnews.com.au/rssfeed/latest",
]


def load_topics() -> list[dict]:
    """Load active watch topics from topics.json."""
    if not TOPICS_FILE.exists():
        return []
    # Try utf-8 first, fall back to system encoding
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            data = json.loads(TOPICS_FILE.read_text(encoding=enc))
            return [t for t in data.get("topics", []) if t.get("active", True)]
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except Exception as e:
            print(f"  ⚠️  Could not load topics.json: {e}")
            return []
    print("  ⚠️  Could not decode topics.json — try re-saving it as UTF-8")
    return []


def item_matches_topic(item: dict, keywords: list[str]) -> bool:
    """Return True if the item title or summary contains any keyword (case-insensitive)."""
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    return any(kw.lower() in text for kw in keywords)


def fetch_topic_items(keywords: list[str]) -> list[dict]:
    """Pull items from broad feeds and filter to those matching the topic keywords."""
    all_items  = []
    seen       = set()
    for url in TOPIC_SEARCH_FEEDS:
        for item in fetch_feed_items(url, max_items=30):
            key = item["title"].lower()[:60]
            if key not in seen and item_matches_topic(item, keywords):
                seen.add(key)
                all_items.append(item)
    return all_items


def build_topic_prompt(topic_name: str, keywords: list[str],
                        items: list[dict], top_n: int) -> str:
    stories_text = "\n\n".join(
        f"[{i+1}] {it['title']}\nSource: {it['source']}\n{it['summary']}"
        for i, it in enumerate(items[:60])
    )
    kw_str = ", ".join(keywords[:8])
    return f"""You are a specialist editor for the topic: "{topic_name}".
Key terms: {kw_str}

From the articles below, select the {top_n} most relevant and interesting stories.
Prioritise recency and direct relevance to the topic.

For each selected story return a JSON array with:
  - "headline":     crisp headline (max 10 words)
  - "summary":      ONE sentence only — the single most important fact. Max 25 words. Be direct.
  - "source":       publication name
  - "link":         URL from the item
  - "significance": one of ["critical", "major", "notable"]

Return ONLY the JSON array — no markdown fences, no extra text.

ARTICLES:
{stories_text}
"""


def summarise_topic(client: anthropic.Anthropic, topic: dict,
                     items: list[dict]) -> list[dict]:
    """Summarise top stories for a single watch topic."""
    if not items:
        return []
    import time
    prompt = build_topic_prompt(
        topic["name"], topic.get("keywords", []), items, TOP_N_TOPIC_STORIES
    )
    for attempt in range(3):
        try:
            message = client.messages.create(
                model      = "claude-opus-4-6",
                max_tokens = 3000,
                messages   = [{"role": "user", "content": prompt}],
                timeout    = 120,
            )
            raw = message.content[0].text.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(raw)
        except Exception as e:
            print(f"  ⚠️  Attempt {attempt+1}/3 failed for topic '{topic['name']}': {e}")
            if attempt < 2:
                time.sleep(5)
    return []


# ─────────────────────────────────────────────
# UNIFIED TOPICS PAGE
# ─────────────────────────────────────────────

def topic_story_card_html(story: dict, index: int) -> str:
    sig      = story.get("significance", "notable").lower()
    badge    = SIGNIFICANCE_BADGE.get(sig, SIGNIFICANCE_BADGE["notable"])
    link     = story.get("link", "#")
    src      = story.get("source", "")
    headline = story.get("headline", "")
    summary  = story.get("summary", "")
    return f"""
            <article class="t-card" style="--delay: {index * 0.06}s">
                <div class="t-meta">{badge}<span class="t-source">{src}</span></div>
                <h3 class="t-headline"><a href="{link}" target="_blank" rel="noopener">{headline}</a></h3>
                <p class="t-summary">{summary}</p>
            </article>"""


def generate_topics_page(topics: list[dict], all_stories: dict,
                          generated_at: datetime.datetime) -> str:
    date_str = generated_at.strftime("%A, %#d %B %Y")
    time_str = generated_at.strftime("%H:%M AEST")

    columns = ""
    for topic in topics:
        stories   = all_stories.get(topic["id"], [])
        color     = topic.get("color", "#1a1a1a")
        emoji     = topic.get("emoji", "📌")
        name      = topic["name"].replace("&", "&amp;")
        cards     = "".join(topic_story_card_html(s, i) for i, s in enumerate(stories))
        empty     = '<div class="t-empty">No stories found today</div>' if not cards else ""
        columns += f"""
    <div class="t-column">
        <div class="t-col-header" style="--col-color: {color}">
            <span class="t-col-emoji">{emoji}</span>
            <h2 class="t-col-title">{name}</h2>
            <span class="t-col-count">{len(stories)}</span>
        </div>
        <div class="t-cards">
            {cards}{empty}
        </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My Topics — Morning Brief {date_str}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Sans+3:wght@300;400;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        :root {{
            --ink: #1a1a1a; --ink-light: #666; --paper: #f5f2eb; --paper-2: #edeae0;
            --rule: #cdc8b5; --accent: #c0392b; --white: #fff;
            --font-display: 'Playfair Display', Georgia, serif;
            --font-body: 'Source Sans 3', sans-serif;
        }}
        html {{ scroll-behavior: smooth; }}
        body {{ background: var(--paper); color: var(--ink); font-family: var(--font-body); font-size: 14px; line-height: 1.55; }}

        /* masthead */
        .masthead {{ background: var(--ink); color: var(--white); padding: 1rem 1.5rem; display: flex; align-items: center; gap: 1.25rem; border-bottom: 3px solid var(--accent); position: relative; overflow: hidden; }}
        .masthead::before {{ content:''; position: absolute; inset: 0; background: repeating-linear-gradient(0deg, transparent, transparent 19px, rgba(255,255,255,.025) 19px, rgba(255,255,255,.025) 20px); pointer-events: none; }}
        .back-link {{ font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: rgba(255,255,255,.45); text-decoration: none; position: relative; white-space: nowrap; }}
        .back-link:hover {{ color: rgba(255,255,255,.8); }}
        .masthead-title {{ font-family: var(--font-display); font-size: 1.9rem; font-weight: 900; letter-spacing: -.02em; line-height: 1; position: relative; }}
        .masthead-right {{ margin-left: auto; display: flex; flex-direction: column; align-items: flex-end; position: relative; }}
        .masthead-date {{ font-family: var(--font-display); font-style: italic; font-size: .85rem; color: rgba(255,255,255,.5); }}
        .masthead-gen {{ font-size: .6rem; letter-spacing: .1em; text-transform: uppercase; color: rgba(255,255,255,.25); }}

        /* five-column grid */
        /* ── TOPICS GRID — 5 columns desktop, stacks on mobile ── */
        .topics-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 1px;
            background: var(--rule);
        }}

        .t-column {{ background: var(--paper); display: flex; flex-direction: column; min-width: 0; }}

        .t-col-header {{
            display: flex; align-items: center; gap: .45rem;
            padding: .7rem .85rem .55rem;
            background: var(--white);
            border-bottom: 3px solid var(--col-color, var(--ink));
            position: sticky; top: 0; z-index: 10;
        }}
        .t-col-emoji {{ font-size: .95rem; flex-shrink: 0; }}
        .t-col-title {{ font-family: var(--font-display); font-size: .82rem; font-weight: 700; line-height: 1.2; flex: 1; min-width: 0; }}
        .t-col-count {{ font-size: .58rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-light); background: var(--paper-2); border: 1px solid var(--rule); padding: .1rem .35rem; border-radius: 2rem; flex-shrink: 0; }}

        .t-cards {{ display: flex; flex-direction: column; gap: 1px; background: var(--rule); flex: 1; }}

        .t-card {{ background: var(--white); padding: .75rem .85rem; animation: fadeUp .3s ease both; animation-delay: var(--delay, 0s); transition: background .12s; }}
        .t-card:hover {{ background: #fdfcf9; }}
        @keyframes fadeUp {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}

        .t-meta {{ display: flex; align-items: center; gap: .4rem; margin-bottom: .25rem; flex-wrap: wrap; }}
        .badge {{ font-size: .56rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; padding: .1rem .35rem; border-radius: 2px; flex-shrink: 0; }}
        .badge-critical {{ background: #fde8e8; color: #c0392b; }}
        .badge-major    {{ background: #fef3e2; color: #d35400; }}
        .badge-notable  {{ background: #e8f4e8; color: #27ae60; }}
        .t-source {{ font-size: .58rem; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-light); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .t-headline {{ font-family: var(--font-display); font-size: .82rem; font-weight: 700; line-height: 1.3; margin-bottom: .25rem; }}
        .t-headline a {{ color: var(--ink); text-decoration: none; }}
        .t-headline a:hover {{ color: var(--accent); text-decoration: underline; }}
        .t-summary {{ font-size: .73rem; color: #484848; line-height: 1.55; }}
        .t-empty {{ padding: 1.5rem .85rem; font-size: .75rem; color: var(--ink-light); font-style: italic; }}

        footer {{ background: var(--ink); color: rgba(255,255,255,.28); text-align: center; font-size: .65rem; letter-spacing: .1em; text-transform: uppercase; padding: .9rem 1.5rem; }}

        /* ── Mobile responsive — stack topics vertically in portrait ── */
        @media (max-width: 900px) {{
            .topics-grid {{ grid-template-columns: 1fr; }}
            .t-col-header {{ position: relative; top: auto; }}
        }}
        @media print {{ .t-card {{ break-inside: avoid; }} }}
    </style>
</head>
<body>

<header class="masthead">
    <a href="briefing.html" class="back-link">← Morning Brief</a>
    <h1 class="masthead-title">📌 My Topics</h1>
    <div class="masthead-right">
        <span class="masthead-date">{date_str}</span>
        <span class="masthead-gen">Generated {time_str}</span>
    </div>
</header>

<div class="topics-grid">
    {columns}
</div>

<footer>My Topics — Morning Briefing &mdash; {date_str}</footer>

</body>
</html>"""


# ─────────────────────────────────────────────
# EMAIL WIDGET HTML
# ─────────────────────────────────────────────

PRIORITY_BADGE = {
    "action-required": '<span class="badge badge-critical">⚡ Action</span>',
    "important":       '<span class="badge badge-major">● Important</span>',
    "informational":   '<span class="badge badge-notable">◦ Info</span>',
}

def email_widget_html(digest: list[dict]) -> str:
    """Generate the email digest section for the widget bar panel below the masthead."""
    if not digest:
        return ""

    cards = ""
    for item in digest:
        priority  = item.get("priority", "informational").lower()
        badge     = PRIORITY_BADGE.get(priority, PRIORITY_BADGE["informational"])
        subject   = item.get("subject", "")
        from_name = item.get("from_name", item.get("from_email", ""))
        summary   = item.get("summary", "")
        action    = item.get("action", "")
        folder    = item.get("folder", "")
        is_read   = item.get("is_read", True)
        action_html = f'<span class="email-action">→ {action}</span>' if action else ""
        unread_dot  = '<span class="email-unread-dot" title="Unread">●</span>' if not is_read else ""
        folder_html = f'<span class="email-folder">{folder}</span>' if folder and folder.lower() != "inbox" else ""
        cards += f"""
        <div class="email-card{"  email-card-unread" if not is_read else ""}">
            <div class="email-meta">{badge}{unread_dot}<span class="email-from">{from_name}</span>{folder_html}</div>
            <div class="email-subject">{subject}</div>
            <div class="email-summary">{summary}{action_html}</div>
        </div>"""

    count = len(digest)
    action_count = sum(1 for i in digest if i.get("priority") == "action-required")
    counter_html = f'<span class="email-count">{count} items'
    if action_count:
        counter_html += f' &mdash; <strong>{action_count} need action</strong>'
    counter_html += '</span>'

    return f"""
<div class="email-digest-panel">
    <div class="email-digest-header">
        <span class="email-digest-title">📬 Email Digest</span>
        {counter_html}
    </div>
    <div class="email-cards-row">
        {cards}
    </div>
</div>"""


# ─────────────────────────────────────────────
# BRIEFING.HTML: add topic links to widget bar
# ─────────────────────────────────────────────

def topics_widget_html(topics: list[dict]) -> str:
    """Generate topic pill links for the main briefing widget bar."""
    if not topics:
        return ""
    divider = '<div class="widget-divider"></div>'
    links = "".join(
        f'<a href="topic_{t["id"]}.html" class="widget-slot widget-topic-link">'
        f'{t["emoji"]} <span class="label">Watch</span>'
        f'<span class="value">{t["name"]}</span></a>'
        for t in topics
    )
    return divider + links


# ─────────────────────────────────────────────
# LOCAL REVIEW SERVER  (briefing.py review)
# ─────────────────────────────────────────────

def serve_briefing(out_dir: Path):
    """
    Serve the briefing on localhost and handle /push requests to create
    tasks in Microsoft To Do.  Called automatically after generation, and
    also by `py briefing.py review` to re-open a previous briefing.

    Requires outlook_email.py to be available and authenticated with
    Tasks.ReadWrite scope.  Stays open until Ctrl+C or the Close link.
    """
    import threading
    import webbrowser
    from http.server import HTTPServer, BaseHTTPRequestHandler

    briefing_file = out_dir / "briefing.html"
    if not briefing_file.exists():
        raise SystemExit(f"❌  No briefing found at {briefing_file}\n"
                         f"    Run briefing.py first to generate one.")

    if not _OUTLOOK_AVAILABLE:
        raise SystemExit("❌  outlook_email.py not found — needed for To Do push.")

    try:
        token = _outlook.get_access_token()
    except RuntimeError as e:
        raise SystemExit(f"❌  Outlook auth error: {e}\n"
                         f"    Run: py outlook_email.py setup")

    # Resolve To Do default list
    try:
        lists_data = _outlook._graph_get(token, "/me/todo/lists")
        todo_list  = next(
            (l for l in lists_data.get("value", [])
             if l.get("wellknownListName") == "defaultList"),
            (lists_data.get("value") or [None])[0],
        )
        if not todo_list:
            raise RuntimeError("No To Do lists found.")
        list_id = todo_list["id"]
        print(f"   📋  To Do list: {todo_list.get('displayName', list_id)}")
    except Exception as e:
        raise SystemExit(f"❌  Could not retrieve To Do lists: {e}\n"
                         f"    Ensure Tasks.ReadWrite is granted and re-run: py outlook_email.py setup")

    briefing_html = briefing_file.read_text(encoding="utf-8")
    shutdown_event = threading.Event()

    def _create_task(title: str, detail: str, due: str, priority: str) -> bool:
        importance = {"high": "high", "urgent": "high",
                      "normal": "normal", "low": "low"}.get(priority, "normal")
        body = {
            "title":      title,
            "importance": importance,
            "body":       {"contentType": "text", "content": detail},
            "dueDateTime": {"dateTime": f"{due}T00:00:00", "timeZone": "UTC"},
        }
        try:
            resp = requests.post(
                f"https://graph.microsoft.com/v1.0/me/todo/lists/{list_id}/tasks",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            return bool(resp.json().get("id"))
        except Exception as e:
            print(f"   ⚠️  Task create failed '{title[:50]}': {e}")
            return False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = briefing_html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/shutdown":
                body = b"Closing..."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                shutdown_event.set()
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            if self.path == "/push":
                import json as _json
                length  = int(self.headers.get("Content-Length", 0))
                payload = _json.loads(self.rfile.read(length))
                tasks   = payload.get("tasks", [])
                results = []
                for t in tasks:
                    ok = _create_task(
                        t.get("title", ""),
                        t.get("detail", ""),
                        t.get("due", str(datetime.date.today())),
                        t.get("priority", "normal"),
                    )
                    results.append({"id": t["id"], "success": ok})
                    print(f"   {chr(10003) if ok else chr(10007)}  {t.get('title','')[:60]}")

                ok_count   = sum(1 for r in results if r["success"])
                fail_count = len(results) - ok_count
                resp = _json.dumps({
                    "results":    results,
                    "ok_count":   ok_count,
                    "fail_count": fail_count,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

                # Shut down cleanly after response reaches the browser
                if fail_count == 0:
                    print(f"\n   \u2705  {ok_count} task(s) pushed to To Do \u2014 shutting down.")
                    threading.Timer(1.5, shutdown_event.set).start()
                else:
                    print(f"\n   \u26a0\ufe0f  {ok_count} pushed, {fail_count} failed \u2014 server still running.")
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, fmt, *args):
            pass   # suppress HTTP log noise

    for port in (8765, 8766, 8767):
        try:
            server = HTTPServer(("localhost", port), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit("❌  No free port found (tried 8765–8767).")

    url = f"http://localhost:{port}"
    print(f"\n   \U0001f310  Opening briefing at {url}")
    print(f"       Go to the Work Actions tab, tick tasks, then click Push to To Do.")
    print(f"       The server will close automatically once tasks are pushed.\n")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        while not shutdown_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        print("\n   Cancelled.")
    finally:
        server.server_close()
        print("   Server closed. Returning to command prompt.\n")




def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("❌  Set ANTHROPIC_API_KEY environment variable first.")

    print(f"   API key loaded: {api_key[:12]}...{api_key[-4:]} ({len(api_key)} chars)")
    client      = anthropic.Anthropic(api_key=api_key)
    generated   = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=10)))  # AEST
    all_sections = {}

    print(f"\n📰 Morning Briefing Generator — {generated.strftime('%A %#d %B %Y')}")
    print("=" * 55)

    # ── Standard categories ──
    for category_name, meta in CATEGORIES.items():
        print(f"\n{meta['emoji']}  Fetching: {category_name}")
        items = fetch_category_items(meta["feeds"])
        print(f"   {len(items)} stories fetched")

        if items:
            print(f"   Summarising top {TOP_N_STORIES}…")
            stories = summarise_category(client, category_name, items, TOP_N_STORIES)
            print(f"   ✓ {len(stories)} summaries generated")
        else:
            stories = []

        all_sections[category_name] = stories

    # ── Watch topics ──
    active_topics = load_topics()
    if active_topics:
        print(f"\n🔍  Processing {len(active_topics)} watch topic(s)…")

    all_topic_stories = {}
    for topic in active_topics:
        print(f"\n{topic['emoji']}  Topic: {topic['name']}")
        items = fetch_topic_items(topic["keywords"])
        print(f"   {len(items)} matching articles found")
        if items:
            print(f"   Summarising top {TOP_N_TOPIC_STORIES}…")
            stories = summarise_topic(client, topic, items)
            print(f"   ✓ {len(stories)} summaries generated")
        else:
            stories = []
        all_topic_stories[topic["id"]] = stories

    # ── Outlook full email analysis ──
    email_analysis = {}
    if _OUTLOOK_AVAILABLE and os.environ.get("OUTLOOK_CLIENT_ID"):
        print("\n📬  Fetching Outlook email analysis…")
        email_analysis = _outlook.get_email_analysis(client)


    # ── Create output directory ──
    # In GitHub Actions (CI=true), use a fixed path the workflow can find
    # Locally, use a timestamped subfolder to keep history
    if os.environ.get("CI"):
        out_dir = Path("output") / generated.strftime("%Y-%m-%d_%H-%M")
    else:
        out_dir = Path("output") / generated.strftime("%Y-%m-%d_%H-%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📁  Output folder: {out_dir.resolve()}")

    # ── Market data ──
    print("\n📈  Fetching market data…")
    market_data = fetch_market_data()
    asx_data    = fetch_asx_watchlist()
    if market_data:
        print(f"   ✓ {len(market_data)}/{len(MARKET_TICKERS)} market instruments: "
              f"{', '.join(m['label'] + ' ' + m['price'] for m in market_data)}")
    else:
        print("   ⚠️  No market data — check yfinance is installed and up to date:")
        print("       py -m pip install --upgrade yfinance")
    if asx_data:
        print(f"   ✓ {len(asx_data)}/{len(ASX_WATCHLIST)} ASX stocks: "
              f"{', '.join(s['label'] + ' ' + s['price'] for s in asx_data)}")
    elif market_data is not None:
        print(f"   ⚠️  No ASX watchlist data ({len(ASX_WATCHLIST)} stocks tried)")

    # ── Weather ──
    print("\n🌤️   Fetching weather forecasts…")
    weather_data  = fetch_weather_for("Brisbane")
    sunshine_data = fetch_weather_for("Sunshine Beach")
    if weather_data:
        t = weather_data[0]
        print(f"   ✓ Brisbane: {t['icon']} {t['desc']} {t['high']}°/{t['low']}°")
    if sunshine_data:
        t = sunshine_data[0]
        print(f"   ✓ Sunshine Beach: {t['icon']} {t['desc']} {t['high']}°/{t['low']}°")

    # ── Gas price ──
    print("\n⛽   Fetching east coast gas price…")
    gas_data = fetch_au_gas_price()
    if gas_data:
        print(f"   ✓ AU gas: {gas_data.get('hub','East Coast')} ${gas_data['price']:.2f} {gas_data.get('unit','$/GJ')}")
    else:
        print("   ⚠️  AU gas price unavailable")

    hh_gas_data = fetch_henry_hub_price()
    if hh_gas_data:
        print(f"   ✓ Henry Hub: ${hh_gas_data['price']:.3f} {hh_gas_data.get('unit','USD/MMBtu')}")
    else:
        print("   ⚠️  Henry Hub price unavailable")

    # ── Calendar ──
    calendar_data = {}
    if CALENDAR_ENABLED:
        print("\n📅  Fetching calendar events…")
        try:
            cal = outlook_calendar.fetch_calendar_events()
            t_count  = len(cal.get("today", []))
            tm_count = len(cal.get("tomorrow", []))
            if cal.get("error"):
                print(f"   ⚠️  Calendar: {cal['error']}")
            else:
                print(f"   ✓ Calendar: {t_count} today, {tm_count} tomorrow")

            # AI briefings — cross-reference events against fetched emails
            all_events = cal.get("today", []) + cal.get("tomorrow", [])  # yesterday excluded from briefings
            briefings  = {}
            if all_events:
                print("   🤖  Generating calendar briefings…")
                # Pass full raw email list for richer cross-referencing
                # email_analysis["_raw_emails"] is set by get_email_analysis if available,
                # otherwise fall back to building context from digest + actions
                raw_emails = email_analysis.get("_raw_emails", []) if email_analysis else []
                if not raw_emails and email_analysis:
                    for d in email_analysis.get("digest", []):
                        subj = d.get("subject", "")
                        if subj:
                            raw_emails.append({
                                "subject":      subj,
                                "from":         d.get("from_name", d.get("from_email", "")),
                                "body_preview": d.get("summary", d.get("action", "")),
                            })
                    for a in email_analysis.get("actions", []):
                        raw_emails.append({
                            "subject":      a.get("action", ""),
                            "from":         a.get("from_email", ""),
                            "body_preview": a.get("context", ""),
                        })
                briefings = outlook_calendar.analyse_calendar_events(all_events, raw_emails)
                if briefings.get("_error"):
                    print(f"   ⚠️  Briefing error: {briefings['_error']}")
                else:
                    print(f"   ✓ Briefings generated for {len(briefings)} events")

            cal["_html"] = outlook_calendar.build_calendar_tab_html(cal, briefings)
            calendar_data = cal
        except Exception as e:
            print(f"   ⚠️  Calendar error: {e}")
            calendar_data = {"_html": f'<div class="cal-error">Calendar unavailable: {e}</div>'}
    else:
        print("\n📅  Calendar: outlook_calendar.py not found — skipping")

    # ── Main briefing page ──
    print("\n✍️  Generating main briefing page…")
    html = generate_html(all_sections, generated, active_topics, email_analysis,
                         market_data, all_topic_stories, asx_data,
                         weather_data, sunshine_data, gas_data, hh_gas_data,
                         calendar_data=calendar_data)
    (out_dir / "briefing.html").write_text(html, encoding="utf-8")
    print(f"\n✅  Briefing saved to: {out_dir.resolve()}")

    # ── Email briefing to yourself ──
    if _OUTLOOK_AVAILABLE and os.environ.get("OUTLOOK_CLIENT_ID") and os.environ.get("BRIEFING_EMAIL_TO"):
        print("\n✉️   Sending briefing email…")
        _outlook.send_briefing_email(html, out_dir.name, generated, all_sections, email_analysis)

    # ── Serve immediately so the Push to To Do button works ──
    # The server stays open until you press Ctrl+C or click Close in the page.
    # Running in CI (GitHub Actions) skips this — no interactive session there.
    if not os.environ.get("CI"):
        serve_briefing(out_dir)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "review":
        # Re-open a previously generated briefing without re-running the pipeline.
        output_root = Path("output")
        if not output_root.exists():
            raise SystemExit("❌  No output/ folder found. Run briefing.py first.")
        folders = sorted(
            [d for d in output_root.iterdir() if d.is_dir()],
            reverse=True,
        )
        if not folders:
            raise SystemExit("❌  No generated briefings found in output/.")
        latest = folders[0]
        print(f"\n📂  Re-opening briefing from: {latest}")
        serve_briefing(latest)
    else:
        main()
