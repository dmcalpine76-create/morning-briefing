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
    _OUTLOOK_AVAILABLE = False

# Gmail personal inbox (optional — set GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET in .env)
try:
    import gmail_email as _gmail
    _GMAIL_AVAILABLE = True
except ImportError:
    _GMAIL_AVAILABLE = False


TOPICS_FILE = Path(__file__).parent / "topics.json"
TOP_N_TOPIC_STORIES = 5    # stories per topic column

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

# ASX stocks of personal interest — shown separately in widget bar
ASX_WATCHLIST = [
    {"sym": "GAS.AX",  "label": "GAS"},
    {"sym": "COI.AX",  "label": "COI"},
    {"sym": "BPT.AX",  "label": "BPT"},
    {"sym": "STO.AX",  "label": "STO"},
]


def fetch_market_data() -> list[dict]:
    """Fetch market prices via yfinance library or fall back to empty list."""
    try:
        import yfinance as yf
        results = []
        for t in MARKET_TICKERS:
            try:
                ticker = yf.Ticker(t["sym"])
                info   = ticker.fast_info
                price  = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
                prev   = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
                if price is None:
                    continue
                chg     = ((price - prev) / prev * 100) if prev else 0
                if t["fmt"] == "fx":
                    fmt_price = f"{price:.4f}"
                elif t["fmt"] == "index":
                    fmt_price = f"{price:,.0f}"
                else:
                    fmt_price = f"${price:.2f}"
                results.append({
                    "label": t["label"],
                    "price": fmt_price,
                    "change": f"{chg:+.2f}%",
                    "up": chg >= 0,
                })
            except Exception:
                continue
        return results
    except ImportError:
        return []   # yfinance not installed — ticker will show placeholder


def fetch_asx_watchlist() -> list[dict]:
    """Fetch ASX watchlist stock prices via yfinance."""
    try:
        import yfinance as yf
        results = []
        for t in ASX_WATCHLIST:
            try:
                ticker = yf.Ticker(t["sym"])
                info   = ticker.fast_info
                price  = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
                prev   = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
                if price is None:
                    continue
                chg = ((price - prev) / prev * 100) if prev else 0
                results.append({
                    "label":  t["label"],
                    "sym":    t["sym"],
                    "price":  f"${price:.3f}",
                    "change": f"{chg:+.2f}%",
                    "up":     chg >= 0,
                })
            except Exception:
                continue
        return results
    except ImportError:
        return []


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
# HTML GENERATION
# ─────────────────────────────────────────────

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
    """Build the email tab HTML to embed inside briefing.html."""
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

    # Digest cards
    digest_html = ""
    for item in digest:
        badge    = PRIORITY_BADGE.get(item.get("priority","informational"), PRIORITY_BADGE["informational"])
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
            <div class="ep-from">{item.get("from_name","")}</div>
            <div class="ep-subject">{item.get("subject","")}</div>
            <div class="ep-summary">{item.get("summary","")}</div>
            {act_tag}
        </div>"""

    # Action rows
    actions_html = ""
    for i, item in enumerate(actions):
        badge    = URGENCY_BADGE.get(item.get("priority","normal"), URGENCY_BADGE["normal"])
        deadline = item.get("deadline","")
        dl_tag   = f'<span class="ep-deadline">⏰ {deadline}</span>' if deadline else ""
        ref      = item.get("from_email","")
        actions_html += f"""
        <div class="ep-action-row">
            <div class="ep-action-num">{i+1}</div>
            <div>
                <div class="ep-action-title">{badge} {item.get("action","")} {dl_tag}</div>
                <div class="ep-action-context">{item.get("context","")}</div>
                {f'<div class="ep-action-ref">Re: {ref}</div>' if ref else ""}
            </div>
        </div>"""

    # People cards
    people_html = ""
    for item in people:
        act   = item.get("action","contact")
        icon  = "📅" if act == "schedule-meeting" else ("🔄" if act == "follow-up" else "💬")
        label = {"schedule-meeting":"Schedule meeting","follow-up":"Follow up","contact":"Contact"}.get(act,act)
        timing  = item.get("suggested_timing","")
        context = item.get("context","")
        email_addr = item.get("email","")
        people_html += f"""
        <div class="ep-person">
            <div class="ep-person-icon">{icon}</div>
            <div>
                <div class="ep-person-name">{item.get("name","")} <span class="ep-person-act">{label}</span></div>
                {f'<div class="ep-person-email">{email_addr}</div>' if email_addr else ""}
                <div class="ep-person-reason">{item.get("reason","")}</div>
                {f'<div class="ep-person-timing">⏰ {timing}</div>' if timing else ""}
                {f'<div class="ep-person-context">Re: {context}</div>' if context else ""}
            </div>
        </div>"""

    if not digest and not actions and not people:
        return f'<div style="padding:3rem;text-align:center;color:#888">{empty_msg}</div>'

    return f"""<div class="email-view">
    <section>
        <div class="ep-panel-title">📧 Priority Digest <span class="ep-count">{len(digest)}</span></div>
        {digest_html or '<p class="ep-empty">No priority emails found.</p>'}
    </section>
    <section>
        <div class="ep-panel-title">✅ Actions for Today <span class="ep-count">{len(actions)}</span></div>
        {actions_html or '<p class="ep-empty">No actions identified.</p>'}
    </section>
    <section>
        <div class="ep-panel-title">👥 People &amp; Meetings <span class="ep-count">{len(people)}</span></div>
        {people_html or '<p class="ep-empty">No contacts or meetings identified.</p>'}
    </section>
</div>"""

def generate_html(sections: dict, generated_at: datetime.datetime,
                   active_topics=None, email_analysis=None, market_data=None,
                   topic_stories=None, gmail_analysis=None, asx_data=None) -> str:
    date_str      = generated_at.strftime("%A, %#d %B %Y")
    time_str      = generated_at.strftime("%H:%M AEST")
    columns       = "".join(
        column_html(name, CATEGORIES[name], stories)
        for name, stories in sections.items()
        if stories
    )
    market_html    = market_widgets_html(market_data or [])
    asx_html       = asx_watchlist_html(asx_data or [])
    email_count    = len((email_analysis or {}).get('digest', []))
    action_count   = len((email_analysis or {}).get('actions', []))
    email_tab_html  = _build_email_tab(email_analysis or {})
    gmail_count     = len((gmail_analysis or {}).get('digest', []))
    gmail_actions   = len((gmail_analysis or {}).get('actions', []))
    gmail_tab_html  = _build_email_tab(gmail_analysis or {}, empty_msg='No Gmail data. Add GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to .env then run: py gmail_email.py setup')
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
            flex-wrap: wrap;       /* wrap to second line if needed */
            overflow: visible;
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
        <button class="tab-btn" onclick="showTab('gmail')" id="tab-gmail">📬 Personal Actions{"" if not gmail_count else f" ({gmail_count})"}</button>
        {topic_tab_btns}
    </nav>
</header>

<!-- ── TOP WIDGET BAR ── -->
<div class="widget-bar" id="widget-bar">
    {market_html}
    <div class="widget-divider"></div>
    {asx_html}
</div>

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


<!-- ── GMAIL TAB ── -->
<div id="view-gmail" style="display:none">
{gmail_tab_html}
</div>

<!-- ── TOPIC TABS ── -->
{topic_tabs_html}

<footer>
    Morning Briefing &mdash; Generated automatically &mdash; {date_str}
</footer>

<script>
// ── Tab switching ──
function showTab(tab) {{
    // Hide all views
    ['view-news','view-email'].forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }});
    const topicsView = document.getElementById('view-topics');
    if (topicsView) topicsView.style.display = 'none';
    const gmailView = document.getElementById('view-gmail');
    if (gmailView) gmailView.style.display = 'none';
    // Deactivate all tab buttons
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('tab-active'));
    // Show selected view and activate button
    const view = document.getElementById('view-' + tab);
    if (view) view.style.display = '';
    const btn = document.getElementById('tab-' + tab);
    if (btn) btn.classList.add('tab-active');
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
# MAIN
# ─────────────────────────────────────────────

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("❌  Set ANTHROPIC_API_KEY environment variable first.")

    print(f"   API key loaded: {api_key[:12]}...{api_key[-4:]} ({len(api_key)} chars)")
    client      = anthropic.Anthropic(api_key=api_key)
    generated   = datetime.datetime.now()
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

    # ── Gmail personal inbox analysis ──
    gmail_analysis = {}
    if _GMAIL_AVAILABLE and os.environ.get('GMAIL_CLIENT_ID'):
        print('\n📨  Fetching Gmail personal inbox analysis…')
        try:
            gmail_analysis = _gmail.get_gmail_analysis(client)
        except Exception as e:
            print(f'   ⚠️  Gmail skipped: {e}')

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
        print(f"   ✓ {len(market_data)} market instruments")
    if asx_data:
        print(f"   ✓ {len(asx_data)} ASX stocks: {', '.join(s['label'] for s in asx_data)}")
    if not market_data and not asx_data:
        print("   ⚠️  yfinance not installed — run: py -m pip install yfinance")

    # ── Main briefing page ──
    print("\n✍️  Generating main briefing page…")
    html = generate_html(all_sections, generated, active_topics, email_analysis, market_data, all_topic_stories, gmail_analysis, asx_data)
    (out_dir / "briefing.html").write_text(html, encoding="utf-8")
    print(f"\n✅  All files saved to: {out_dir.resolve()}")

    # ── Email briefing to yourself ──
    if _OUTLOOK_AVAILABLE and os.environ.get("OUTLOOK_CLIENT_ID") and os.environ.get("BRIEFING_EMAIL_TO"):
        print("\n✉️   Sending briefing email…")
        _outlook.send_briefing_email(html, out_dir.name, generated, all_sections, email_analysis)

    print(f"\nServe with:  py -m http.server 8080 --directory {out_dir}")
    print(f"Then open:   http://localhost:8080/briefing.html")


if __name__ == "__main__":
    main()
