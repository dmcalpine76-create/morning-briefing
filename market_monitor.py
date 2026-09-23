"""
market_monitor.py
-----------------
Granular overnight coverage of named companies and topics, for the
"Market Watch" tab.

Why this exists: the 36 feeds in topic_search_feeds are publisher front
pages. They carry whatever that masthead chose to push, usually truncated
to a headline, and they cannot be queried by company. A competitor
announcement covered only by the AFR never appears. This module closes
that gap with three layers, strongest first:

  1. ASX announcements  — the statutory record, per company code. The only
                          layer where "every announcement" is literally
                          achievable. Delegated to asx_announcements.py.
  2. Per-entity search  — one Google News RSS query per company and per
                          topic, scoped to Australia and the last N hours.
                          This sweeps every indexed outlet rather than the
                          six we happen to subscribe to.
  3. The existing pool  — topic_search_feeds, matched against the same
                          aliases, for anything the first two missed.

Everything is keyed on ALIASES, which is why they are editable from the
settings dashboard. "Senex", "Senex Energy" and "POSCO International" are
one entity to Doug and three strings to a matcher, and getting that list
right is most of the difference between a useful page and a noisy one.

Absence is rendered explicitly. An entity with nothing overnight says so,
rather than silently disappearing — otherwise a broken fetch and a quiet
night look identical.

Self-test:
    py market_monitor.py --test
    py market_monitor.py --test "Comet Ridge"
"""

import os
import re
import json
import html
import datetime
import urllib.parse
import concurrent.futures
from pathlib import Path

import requests

try:
    import feedparser
except ImportError:
    feedparser = None

AEST_OFFSET  = datetime.timezone(datetime.timedelta(hours=10))
SETTINGS     = Path(__file__).parent / "briefing_settings.json"
REQUEST_TIMEOUT = 12

GOOGLE_NEWS = ("https://news.google.com/rss/search?q={query}"
               "&hl=en-AU&gl=AU&ceid=AU:en")

DEFAULT_CFG = {
    "enabled":         True,
    "lookback_hours":  30,
    "max_per_entity":  8,
    "use_google_news": True,
    "use_feed_pool":   True,
    "include_asx":     True,
    "summarise":       True,
    "companies": [
        {"name": "State Gas",      "code": "GAS",
         "aliases": ["State Gas Limited", "Rolleston West", "Reid's Dome"]},
        {"name": "Comet Ridge",    "code": "COI", "aliases": ["Comet Ridge Limited", "Mahalo"]},
        {"name": "Beach Energy",   "code": "BPT", "aliases": ["Beach Energy Limited"]},
        {"name": "Santos",         "code": "STO", "aliases": ["Santos Limited", "GLNG"]},
        {"name": "Blue Energy",    "code": "BLU", "aliases": ["Blue Energy Limited"]},
        {"name": "Senex Energy",   "code": "",    "aliases": ["Senex", "Atlas gas", "Roma North"]},
        {"name": "Origin Energy",  "code": "ORG", "aliases": ["Origin Energy Limited", "APLNG"]},
        {"name": "Shell QGC",      "code": "",    "aliases": ["QGC", "Arrow Energy"]},
    ],
    "topics": [
        {"name": "East Coast Gas Supply",
         "keywords": ["east coast gas", "gas shortfall", "ADGSM", "gas market review"]},
        {"name": "Queensland Gas Policy",
         "keywords": ["Queensland gas", "ATP tender", "gas acreage release"]},
    ],
    "mute": [],
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS    = re.compile(r"\s+")


# ── config ───────────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if SETTINGS.exists():
        try:
            raw = json.loads(SETTINGS.read_text(encoding="utf-8"))
            cfg.update(raw.get("market_monitor", {}) or {})
        except Exception as e:
            print(f"  WARNING: could not read market_monitor settings: {e}")
    return cfg


def _entities(cfg: dict) -> list:
    """Companies and topics flattened into one list, companies first."""
    out = []
    for c in cfg.get("companies", []) or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        terms = [name] + [a for a in (c.get("aliases") or []) if str(a).strip()]
        out.append({"kind": "company", "name": name,
                    "code": (c.get("code") or "").strip().upper(),
                    "terms": terms})
    for t in cfg.get("topics", []) or []:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        terms = [k for k in (t.get("keywords") or []) if str(k).strip()] or [name]
        out.append({"kind": "topic", "name": name, "code": "", "terms": terms})
    return out


# ── fetching ─────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def _entry_dt(entry) -> datetime.datetime:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return datetime.datetime.now(AEST_OFFSET)
    try:
        return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc).astimezone(AEST_OFFSET)
    except Exception:
        return datetime.datetime.now(AEST_OFFSET)


def _split_publisher(title: str, fallback: str = "") -> tuple:
    """Google News titles read 'Headline - Publisher'."""
    if " - " in title:
        head, _, pub = title.rpartition(" - ")
        if head.strip() and len(pub) < 60:
            return head.strip(), pub.strip()
    return title.strip(), fallback


def _google_query(terms: list, hours: int) -> str:
    quoted = " OR ".join('"{}"'.format(str(t).replace('"', "")) for t in terms[:6])
    # ceil, not round: a 30h look-back must ask Google for 2 days or it
    # silently returns 24h and the extra 6 hours are never searched. The
    # precise cutoff is applied to the results afterwards.
    days   = max(1, -(-int(hours) // 24))
    return urllib.parse.quote_plus("{} when:{}d".format(quoted, days))


def _fetch_google(entity: dict, cfg: dict) -> list:
    if feedparser is None:
        return []
    url = GOOGLE_NEWS.format(query=_google_query(entity["terms"],
                                                 cfg.get("lookback_hours", 30)))
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"   !  {entity['name']}: search failed ({e})")
        return []

    cutoff = datetime.datetime.now(AEST_OFFSET) - datetime.timedelta(
        hours=cfg.get("lookback_hours", 30))
    items = []
    for entry in feed.entries[: cfg.get("max_per_entity", 8) * 3]:
        when = _entry_dt(entry)
        if when < cutoff:
            continue
        raw_title = html.unescape(getattr(entry, "title", "") or "").strip()
        if not raw_title:
            continue
        headline, publisher = _split_publisher(raw_title)
        items.append({
            "headline":  headline,
            "publisher": publisher or "Google News",
            "url":       getattr(entry, "link", "") or "",
            "when":      when,
            "layer":     "search",
        })
    return items


def _match_pool(entities: list, pool_items: list) -> dict:
    """Match the existing feed pool against each entity's aliases."""
    by_entity = {e["name"]: [] for e in entities}
    prepared  = [(e, [_norm(t) for t in e["terms"] if _norm(t)]) for e in entities]
    for item in pool_items or []:
        blob = _norm("{} {}".format(item.get("title", ""), item.get("summary", "")))
        if not blob:
            continue
        for entity, terms in prepared:
            if any(t and t in blob for t in terms):
                by_entity[entity["name"]].append({
                    "headline":  html.unescape(item.get("title", "")).strip(),
                    "publisher": item.get("source", "") or "",
                    "url":       item.get("link", "") or "",
                    "when":      datetime.datetime.now(AEST_OFFSET),
                    "layer":     "feed",
                })
                break
    return by_entity


def _fetch_announcements(entities: list, client=None, already: dict = None) -> dict:
    """
    ASX announcements keyed by entity name, via asx_announcements.py.

    'already' is the announcement payload briefing.py fetched for the Work
    Actions tab. Its codes are reused rather than re-scraped: HotCopper sits
    behind bot detection that will not thank us for hitting it twice a run,
    and every re-fetch is a second Haiku summarising pass for the same text.
    Only codes the earlier fetch never covered are requested here.
    """
    codes = [e["code"] for e in entities if e.get("code")]
    if not codes:
        return {}
    try:
        import asx_announcements
    except ImportError:
        return {}

    covered = set()
    if already is not None:
        covered = {c.strip().upper() for c in getattr(asx_announcements, "WATCHLIST", [])}
    missing = [c for c in codes if c not in covered]

    reused = list((already or {}).get("announcements", []) or [])
    fetched = []
    if missing:
        try:
            try:
                data = asx_announcements.get_asx_announcements(client, codes=missing)
            except TypeError:
                # older signature without a codes parameter
                data = asx_announcements.get_asx_announcements(client)
            fetched = data.get("announcements", []) or []
        except Exception as e:
            print(f"   !  announcements unavailable: {e}")

    by_code = {e["code"]: e["name"] for e in entities if e.get("code")}
    out = {}
    for a in reused + fetched:
        name = by_code.get((a.get("ticker") or "").upper())
        if not name:
            continue
        out.setdefault(name, []).append({
            "headline":   a.get("headline", ""),
            "publisher":  "ASX announcement",
            "url":        a.get("url", ""),
            "when":       datetime.datetime.now(AEST_OFFSET),
            "layer":      "asx",
            "summary":    a.get("summary", ""),
            "sensitive":  bool(a.get("is_price_sensitive")),
            "date_label": a.get("date", ""),
        })
    return out


# ── assembly ─────────────────────────────────────────────────────────────────

def _dedupe(items: list) -> list:
    """Same story from three layers collapses to one, strongest layer wins."""
    rank = {"asx": 0, "search": 1, "feed": 2}
    items = sorted(items, key=lambda i: rank.get(i.get("layer"), 9))
    seen, out = set(), []
    for item in items:
        key = _norm(item["headline"])[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _muted(item: dict, mute: list) -> bool:
    blob = _norm("{} {}".format(item.get("headline", ""), item.get("publisher", "")))
    return any(_norm(m) and _norm(m) in blob for m in mute or [])


def collect(cfg: dict = None, pool_items: list = None, client=None,
            announcements: dict = None) -> dict:
    """
    Returns
      {"entities": [{name, kind, code, items: [...], count}],
       "total": int, "generated_at": iso, "errors": [str]}
    """
    cfg = cfg or load_cfg()
    errors = []
    if not cfg.get("enabled", True):
        return {"entities": [], "total": 0, "errors": [],
                "generated_at": datetime.datetime.now(AEST_OFFSET).isoformat()}

    entities = _entities(cfg)
    if not entities:
        return {"entities": [], "total": 0,
                "errors": ["no companies or topics configured"],
                "generated_at": datetime.datetime.now(AEST_OFFSET).isoformat()}

    searched = {e["name"]: [] for e in entities}
    if cfg.get("use_google_news", True):
        if feedparser is None:
            errors.append("feedparser not installed - per-entity search skipped")
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(_fetch_google, e, cfg): e for e in entities}
                for fut in concurrent.futures.as_completed(futures):
                    e = futures[fut]
                    try:
                        searched[e["name"]] = fut.result()
                    except Exception as exc:
                        errors.append(f"{e['name']}: {exc}")

    pooled = _match_pool(entities, pool_items) if cfg.get("use_feed_pool", True) else {}
    anns   = (_fetch_announcements(entities, client, announcements)
              if cfg.get("include_asx", True) else {})

    mute = cfg.get("mute", []) or []
    out_entities, total = [], 0
    for e in entities:
        merged = (anns.get(e["name"], []) + searched.get(e["name"], [])
                  + pooled.get(e["name"], []))
        merged = [i for i in merged if not _muted(i, mute)]
        merged = _dedupe(merged)
        merged.sort(key=lambda i: (i.get("layer") != "asx", -i["when"].timestamp()))
        merged = merged[: cfg.get("max_per_entity", 8)]
        total += len(merged)
        out_entities.append({"name": e["name"], "kind": e["kind"],
                             "code": e["code"], "items": merged,
                             "count": len(merged)})

    return {"entities": out_entities, "total": total, "errors": errors,
            "generated_at": datetime.datetime.now(AEST_OFFSET).isoformat()}


# ── summarising ──────────────────────────────────────────────────────────────

PROMPT = """You are briefing the Managing Director of State Gas (ASX:GAS), a
Queensland gas explorer, on overnight coverage of competitors and policy.

For each numbered item write one line of plain English saying what actually
happened and why it matters to an Australian gas producer. Under 22 words.
Never speculate beyond the headline. If a headline is vague, say what it
appears to concern rather than inventing detail.

Also grade each item:
  "material"   - a competitor transaction, resource or reserve change, project
                 decision, regulatory ruling or policy change with direct
                 commercial consequence
  "notable"    - genuine industry news, no direct consequence
  "background" - commentary, market colour, passing mention

Return ONLY a JSON array, one object per item, in the same order:
  "n":     the item number
  "line":  the one-line summary
  "grade": material | notable | background

No markdown fences.

ITEMS:
{items}"""


def summarise(result: dict, api_key: str = "", client=None) -> dict:
    """Adds 'line' and 'grade' to each item. Never load-bearing."""
    items = [i for e in result.get("entities", []) for i in e["items"]]
    if not items:
        return result
    if client is None:
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return result
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        except Exception:
            return result

    blob = "\n".join(
        "{}. [{}] {} ({})".format(n, i.get("publisher", ""), i["headline"],
                                  i.get("summary", "") or "")[:400]
        for n, i in enumerate(items, 1))

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{"role": "user", "content": PROMPT.format(items=blob)}],
            timeout=120,
        )
        raw = msg.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        for row in json.loads(raw):
            n = row.get("n")
            if isinstance(n, int) and 1 <= n <= len(items):
                items[n - 1]["line"]  = str(row.get("line", ""))[:200]
                items[n - 1]["grade"] = (row.get("grade") or "notable").lower()
    except Exception as e:
        print(f"   !  market summaries skipped ({e})")
    return result


def get_market_monitor(pool_items: list = None, api_key: str = "", client=None,
                       announcements: dict = None) -> dict:
    cfg = load_cfg()
    result = collect(cfg, pool_items, client, announcements)
    if cfg.get("summarise", True):
        result = summarise(result, api_key, client)
    return result


# ── rendering ────────────────────────────────────────────────────────────────

GRADE_COLOR = {"material": "#b3261e", "notable": "#8a6d1f", "background": "#9a968c"}
GRADE_LABEL = {"material": "Material", "notable": "Notable", "background": "Background"}

CSS = """
<style>
.mw-wrap{max-width:1180px;margin:0 auto;padding:1.25rem 1rem 2.5rem}
.mw-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:0.6rem;
  border-bottom:3px solid var(--ink,#1a1a17);padding-bottom:0.5rem;margin-bottom:0.35rem}
.mw-title{font-family:var(--font-display,Georgia,serif);font-size:1.25rem;font-weight:700}
.mw-sub{font-size:0.72rem;color:var(--ink-light,#6b6862)}
.mw-legend{display:flex;gap:0.75rem;font-size:0.62rem;color:var(--ink-light,#6b6862);
  margin:0 0 1.1rem;flex-wrap:wrap}
.mw-legend span{display:flex;align-items:center;gap:0.3rem}
.mw-dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:0 0 auto}
.mw-sec{font-size:0.66rem;font-weight:700;letter-spacing:0.09em;text-transform:uppercase;
  color:var(--ink-light,#6b6862);margin:1.4rem 0 0.6rem;padding-bottom:0.25rem;
  border-bottom:1px solid rgba(0,0,0,0.14)}
.mw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:0.9rem}
.mw-card{border:1px solid rgba(0,0,0,0.13);border-radius:5px;background:#fff;
  padding:0.7rem 0.8rem 0.55rem}
.mw-card-h{display:flex;align-items:center;gap:0.4rem;margin-bottom:0.5rem;
  padding-bottom:0.4rem;border-bottom:1px solid rgba(0,0,0,0.09)}
.mw-name{font-weight:700;font-size:0.86rem}
.mw-code{font-size:0.58rem;font-weight:700;letter-spacing:0.06em;background:#1a1a17;
  color:#fff;padding:0.08rem 0.32rem;border-radius:3px}
.mw-n{margin-left:auto;font-size:0.62rem;color:var(--ink-light,#6b6862)}
.mw-item{padding:0.4rem 0;border-top:1px dotted rgba(0,0,0,0.1)}
.mw-item:first-of-type{border-top:none}
.mw-h{display:flex;gap:0.4rem;align-items:flex-start}
.mw-h a{color:inherit;text-decoration:none;font-weight:600;font-size:0.78rem;line-height:1.32}
.mw-h a:hover{text-decoration:underline}
.mw-line{font-size:0.71rem;color:#3d3a34;line-height:1.4;margin:0.18rem 0 0 0.68rem}
.mw-meta{font-size:0.6rem;color:var(--ink-light,#6b6862);margin:0.2rem 0 0 0.68rem}
.mw-ps{font-size:0.55rem;font-weight:700;color:#fff;background:#b3261e;
  padding:0.05rem 0.28rem;border-radius:2px;letter-spacing:0.04em}
.mw-quiet{margin-top:1.4rem;padding:0.6rem 0.8rem;border:1px dashed rgba(0,0,0,0.18);
  border-radius:5px;font-size:0.68rem;color:var(--ink-light,#6b6862);line-height:1.5}
.mw-quiet b{color:#3d3a34;font-weight:600}
.mw-empty{padding:2rem;text-align:center;color:var(--ink-light,#6b6862);font-style:italic}
.mw-err{margin-top:1rem;font-size:0.63rem;color:#8a6d1f}
@media(max-width:640px){.mw-grid{grid-template-columns:1fr}}
</style>
"""


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def _item_html(item: dict) -> str:
    grade = item.get("grade", "notable")
    color = GRADE_COLOR.get(grade, GRADE_COLOR["notable"])
    dot   = '<span class="mw-dot" style="background:{};margin-top:0.35rem"></span>'.format(color)
    url   = _esc(item.get("url", ""))
    head  = _esc(item.get("headline", ""))
    link  = '<a href="{}" target="_blank" rel="noopener">{}</a>'.format(url, head) if url else head
    badge = ' <span class="mw-ps">PRICE SENSITIVE</span>' if item.get("sensitive") else ""

    line = item.get("line") or item.get("summary") or ""
    line_html = '<div class="mw-line">{}</div>'.format(_esc(line)) if line else ""

    when = item.get("when")
    stamp = when.strftime("%a %I:%M %p").replace(" 0", " ") if hasattr(when, "strftime") else ""
    if item.get("layer") == "asx":
        stamp = item.get("date_label") or stamp
    meta = " &middot; ".join(p for p in [_esc(item.get("publisher", "")), _esc(stamp)] if p)

    return ('<div class="mw-item"><div class="mw-h">{dot}<div>{link}{badge}</div></div>'
            '{line}<div class="mw-meta">{meta}</div></div>').format(
        dot=dot, link=link, badge=badge, line=line_html, meta=meta)


def _card_html(entity: dict) -> str:
    code = ('<span class="mw-code">{}</span>'.format(_esc(entity["code"]))
            if entity.get("code") else "")
    items = "".join(_item_html(i) for i in entity["items"])
    return ('<div class="mw-card"><div class="mw-card-h">'
            '<span class="mw-name">{name}</span>{code}'
            '<span class="mw-n">{n}</span></div>{items}</div>').format(
        name=_esc(entity["name"]), code=code, n=entity["count"], items=items)


def build_tab(result: dict, now: datetime.datetime = None) -> str:
    """The Market Watch tab body. Returns '' when there is nothing configured."""
    if not result:
        return ""
    entities = result.get("entities", []) or []
    if not entities:
        return ""

    now   = now or datetime.datetime.now(AEST_OFFSET)
    loud  = [e for e in entities if e["count"]]
    quiet = [e for e in entities if not e["count"]]

    companies = [e for e in loud if e["kind"] == "company"]
    topics    = [e for e in loud if e["kind"] == "topic"]

    legend = '<div class="mw-legend">' + "".join(
        '<span><i class="mw-dot" style="background:{}"></i>{}</span>'.format(
            GRADE_COLOR[g], GRADE_LABEL[g])
        for g in ("material", "notable", "background")
    ) + '<span>Sources: ASX announcements, news search, subscribed feeds</span></div>'

    body = ""
    if companies:
        body += '<div class="mw-sec">Companies</div><div class="mw-grid">'
        body += "".join(_card_html(e) for e in companies) + "</div>"
    if topics:
        body += '<div class="mw-sec">Topics</div><div class="mw-grid">'
        body += "".join(_card_html(e) for e in topics) + "</div>"
    if not companies and not topics:
        body = ('<div class="mw-empty">Nothing overnight across any monitored '
                'company or topic.</div>')

    quiet_html = ""
    if quiet:
        names = ", ".join(_esc(e["name"]) for e in quiet)
        quiet_html = ('<div class="mw-quiet">Checked and quiet overnight: '
                      '<b>{}</b>.</div>').format(names)

    errs = result.get("errors") or []
    err_html = ('<div class="mw-err">Note: ' + "; ".join(_esc(e) for e in errs)
                + '</div>') if errs else ""

    # CSS is concatenated, never formatted - its braces would be read as
    # format fields and blow up on the first selector.
    return CSS + ('<div class="mw-wrap"><div class="mw-head">'
            '<span class="mw-title">Market Watch</span>'
            '<span class="mw-sub">{total} item{s} across {n} monitored '
            'compan{ies} and topics &middot; last {hrs} hours</span></div>'
            '{legend}{body}{quiet}{err}</div>').format(
        total=result.get("total", 0),
        s="" if result.get("total") == 1 else "s",
        n=len(entities),
        ies="y" if len(entities) == 1 else "ies",
        hrs=load_cfg().get("lookback_hours", 30),
        legend=legend, body=body, quiet=quiet_html, err=err_html)


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test(only: str = "") -> int:
    print("Market monitor self-test")
    print("=" * 74)
    cfg = load_cfg()
    ents = _entities(cfg)
    if only:
        ents = [e for e in ents if only.lower() in e["name"].lower()]
        cfg = dict(cfg)
        cfg["companies"] = [c for c in cfg.get("companies", [])
                            if only.lower() in (c.get("name", "") or "").lower()]
        cfg["topics"] = [t for t in cfg.get("topics", [])
                         if only.lower() in (t.get("name", "") or "").lower()]
    print(f"entities   : {len(ents)}  "
          f"({sum(1 for e in ents if e['kind'] == 'company')} companies, "
          f"{sum(1 for e in ents if e['kind'] == 'topic')} topics)")
    print(f"lookback   : {cfg.get('lookback_hours')}h   "
          f"search={cfg.get('use_google_news')}  pool={cfg.get('use_feed_pool')}  "
          f"asx={cfg.get('include_asx')}")
    print(f"feedparser : {'yes' if feedparser else 'MISSING - pip install feedparser'}")
    print()

    result = collect(cfg)
    for e in result.get("errors", []):
        print(f"  note: {e}")

    print(f"\n{'entity':<26} {'n':>3}  headline")
    print("-" * 96)
    for e in result["entities"]:
        if not e["count"]:
            print(f"{e['name'][:26]:<26} {'-':>3}  (quiet)")
            continue
        for n, i in enumerate(e["items"]):
            label = e["name"][:26] if n == 0 else ""
            cnt   = str(e["count"]) if n == 0 else ""
            print(f"{label:<26} {cnt:>3}  [{i['layer']:<6}] {i['headline'][:56]}")
    print(f"\ntotal: {result['total']} items")

    if result["total"]:
        print("\n-- writing one-line summaries --")
        summarise(result)
        graded = [i for e in result["entities"] for i in e["items"] if i.get("grade")]
        print(f"   graded {len(graded)}/{result['total']}")
        for i in graded[:6]:
            print(f"   ({i['grade']}) {i.get('line', '')}")

    tab = build_tab(result)
    checks = [
        ("tab HTML produced", bool(tab)),
        ("every entity appears (loud or quiet)",
         all(html.escape(e["name"], quote=True) in tab for e in result["entities"])),
        ("no POSIX-only strftime in this module",
         not re.search(r"%-[dmHIjMSyU]",
                       Path(__file__).read_text(encoding="utf-8"))),
        ("quiet entities are stated, not dropped",
         (not any(not e["count"] for e in result["entities"])) or "quiet" in tab),
    ]
    print()
    passed = 0
    for label, ok in checks:
        print(("ok   " if ok else "FAIL ") + label)
        passed += bool(ok)
    print(f"\n{passed}/{len(checks)} passed")
    print("\nCheck the alias lists are catching the right stories. Anything in the")
    print("wrong card means an alias is too loose; anything missing means one is")
    print("absent. Both are fixed in the settings dashboard.")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    arg = ""
    for a in sys.argv[1:]:
        if a != "--test":
            arg = a
    sys.exit(_self_test(arg))
