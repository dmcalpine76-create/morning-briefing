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
    # require: at least one of these must also appear, or the item is dropped.
    #   Several of these names are ordinary English or belong to something
    #   famous elsewhere - "State Gas" matches Ohio state gas regulators,
    #   "Santos" matches a Brazilian football club. Listing the unambiguous
    #   forms of the name alongside the geography means a headline that says
    #   "State Gas Limited" passes on its own, while a bare "state gas" needs
    #   Queensland or the ASX nearby.
    # exclude: any match drops the item outright.
    "companies": [
        {"name": "State Gas",      "code": "GAS",
         "aliases": ["State Gas Limited", "Rolleston West", "Reid's Dome"],
         "require": ["State Gas Limited", "Rolleston", "Reid's Dome", "Queensland",
                      "Australian", "Australia", "ASX", "GAS.AX"],
         "exclude": ["Ohio", "Texas", "Pennsylvania", "state gas tax"]},
        {"name": "Comet Ridge",    "code": "COI",
         "aliases": ["Comet Ridge Limited", "Mahalo"], "require": [], "exclude": []},
        {"name": "Beach Energy",   "code": "BPT",
         "aliases": ["Beach Energy Limited"], "require": [], "exclude": []},
        {"name": "Santos",         "code": "STO",
         "aliases": ["Santos Limited", "GLNG"],
         "require": ["Santos Limited", "GLNG", "Barossa", "Cooper Basin", "LNG",
                      "ASX", "Australia", "Australian", "Darwin", "Papua"],
         "exclude": ["Santos FC", "football", "Neymar", "Sao Paulo", "Brazil"]},
        {"name": "Blue Energy",    "code": "BLU",
         "aliases": ["Blue Energy Limited"],
         "require": ["Blue Energy Limited", "Bowen Basin", "Sapphire", "Lancewood",
                      "Queensland", "ASX", "Australia", "Australian"],
         "exclude": []},
        {"name": "Senex Energy",   "code": "",
         "aliases": ["Senex", "Atlas gas", "Roma North"], "require": [], "exclude": []},
        {"name": "Origin Energy",  "code": "ORG",
         "aliases": ["Origin Energy Limited", "APLNG"], "require": [], "exclude": []},
        {"name": "Shell QGC",      "code": "",
         "aliases": ["QGC", "Arrow Energy"],
         "require": ["QGC", "Arrow Energy", "Queensland", "Surat", "Curtis Island",
                      "LNG", "Australia", "Australian"],
         "exclude": []},
    ],
    "topics": [
        {"name": "East Coast Gas Supply",
         "keywords": ["east coast gas", "gas shortfall", "ADGSM", "gas market review"],
         "require": ["Australia", "Australian", "ACCC", "AEMO", "east coast"],
         "exclude": []},
        {"name": "Queensland Gas Policy",
         "keywords": ["Queensland gas", "ATP tender", "gas acreage release"],
         "require": [], "exclude": []},
    ],
    "mute": [],
}

# Context vocabulary for weak aliases. Kept narrow on purpose: "energy" and
# "oil" appear in far too much unrelated copy to qualify anything, and letting
# them in is how "Iraq, Pakistan Strike Energy Deals With Iran" got through.
DEFAULT_CONTEXT = [
    "gas", "LNG", "petroleum", "oil and gas", "CSG", "coal seam gas",
    "gas field", "gas project", "gasfield", "pipeline", "wellhead",
    "Queensland", "Surat", "Bowen Basin", "Cooper Basin", "Perth Basin",
    "Galilee Basin", "Beetaloo Sub-basin", "Amadeus Basin",
]

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


def _dedupe_terms(terms: list) -> list:
    """
    Drop repeats, keeping order. The company name is prepended to its own
    aliases, so a list that also spells the name out produces a query with the
    same phrase twice - harmless but it wastes one of the six OR slots Google
    is given.
    """
    seen, out = set(), []
    for t in terms:
        key = _norm(t)
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _entities(cfg: dict) -> list:
    """Companies and topics flattened into one list, companies first."""
    out = []
    for c in cfg.get("companies", []) or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        terms = _dedupe_terms([name] + [a for a in (c.get("aliases") or []) if str(a).strip()])
        code  = (c.get("code") or "").strip().upper()
        # A headline carrying the ticker is unambiguously about the company,
        # whatever else it says. "Blue Energy" needs context; "ASX:BLU" does
        # not, and analyst copy leans on the ticker far more than on basins.
        if code:
            terms += [f"ASX:{code}", f"{code}.AX"]
            terms = _dedupe_terms(terms)
        weak  = _dedupe_terms([w for w in (c.get("weak_aliases") or []) if str(w).strip()])
        # a term listed both ways is weak - the cautious reading wins
        terms = [t for t in terms if _norm(t) not in {_norm(w) for w in weak}]
        out.append({"kind": "company", "name": name,
                    "code": code,
                    "terms": terms, "weak_terms": weak,
                    "require": [r for r in (c.get("require") or []) if str(r).strip()],
                    "exclude": [x for x in (c.get("exclude") or []) if str(x).strip()]})
    for t in cfg.get("topics", []) or []:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        terms = _dedupe_terms([k for k in (t.get("keywords") or []) if str(k).strip()] or [name])
        weak  = _dedupe_terms([w for w in (t.get("weak_keywords") or []) if str(w).strip()])
        terms = [t2 for t2 in terms if _norm(t2) not in {_norm(w) for w in weak}]
        out.append({"kind": "topic", "name": name, "code": "", "terms": terms,
                    "weak_terms": weak,
                    "require": [r for r in (t.get("require") or []) if str(r).strip()],
                    "exclude": [x for x in (t.get("exclude") or []) if str(x).strip()]})
    return out


# ── fetching ─────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def _hit(term: str, blob: str) -> bool:
    """
    Whole-phrase match on normalised text, so "GAS" does not match "gasoline"
    and "Origin" does not match "originally". Substring matching was the first
    cut and it is not safe for short tickers or common words.
    """
    t = _norm(term)
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", blob) is not None


def _relevant(item: dict, entity: dict) -> bool:
    """
    Is this item really about this entity?

    The first design had one flat alias list plus a "require" list satisfied by
    any one term. It failed badly in practice, for a reason worth recording:
    the ambiguous aliases were themselves in the require list, so they
    satisfied their own gate. "Barossa" was both an alias for Santos and one of
    Santos's context terms, so a Barossa Valley wine column passed both checks.
    Same for Atlas (Senex), Sapphire (Blue Energy), Norwest (Mineral Resources)
    and Amadeus (Central Petroleum).

    So aliases are now split by how much weight they can carry alone:

      aliases       strong. Unambiguous on their own - "State Gas Limited",
                    "Comet Ridge", "ASX:STX". A hit is enough.
      weak_aliases  project, field and asset names, and any company name that
                    is also ordinary English. "Barossa", "Atlas", "Odin",
                    "Sapphire", "Scarborough". A hit only counts when a
                    context term appears as well.
      require       the context terms. Falls back to DEFAULT_CONTEXT, which is
                    deliberately industry-and-geography specific: "energy" and
                    "oil" are too common to qualify anything.
      exclude       kills the item outright, wherever it matches.

    Aliases are matched against the headline and summary only. The publisher is
    checked for exclusions but never for aliases - a Barossa Valley local paper
    was matching Santos on its masthead alone.
    """
    text = _norm("{} {}".format(item.get("headline", ""), item.get("summary", "")))
    if not text:
        return False

    with_pub = _norm("{} {}".format(text, item.get("publisher", "")))
    if any(_hit(x, with_pub) for x in entity.get("exclude", [])):
        return False

    if any(_hit(t, text) for t in entity.get("terms", [])):
        return True

    weak = entity.get("weak_terms", [])
    if weak and any(_hit(t, text) for t in weak):
        context = entity.get("require") or DEFAULT_CONTEXT
        return any(_hit(c, text) for c in context)

    return False


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


def _google_query(terms: list, hours: int, require: list = None) -> str:
    """
    (alias OR alias OR ...) (context OR context ...) when:Nd

    Juxtaposition is AND in Google's syntax, so the context group narrows the
    result set at the source rather than us pulling Ohio down the wire and
    discarding it here.
    """
    quoted = " OR ".join('"{}"'.format(str(t).replace('"', "")) for t in terms[:6])
    quoted = "({})".format(quoted)
    if require:
        ctx = " OR ".join('"{}"'.format(str(r).replace('"', "")) for r in require[:6])
        quoted = "{} ({})".format(quoted, ctx)
    # ceil, not round: a 30h look-back must ask Google for 2 days or it
    # silently returns 24h and the extra 6 hours are never searched. The
    # precise cutoff is applied to the results afterwards.
    days   = max(1, -(-int(hours) // 24))
    return urllib.parse.quote_plus("{} when:{}d".format(quoted, days))


def _one_query(terms: list, context: list, entity: dict, cfg: dict) -> tuple:
    """Fetch one Google News query. Returns (kept, dropped_headlines)."""
    if feedparser is None or not terms:
        return [], []
    url = GOOGLE_NEWS.format(query=_google_query(
        terms, cfg.get("lookback_hours", 30), context))
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"   !  {entity['name']}: search failed ({e})")
        return [], []

    cutoff = datetime.datetime.now(AEST_OFFSET) - datetime.timedelta(
        hours=cfg.get("lookback_hours", 30))
    kept, dropped = [], []
    for entry in feed.entries[: cfg.get("max_per_entity", 8) * 4]:
        when = _entry_dt(entry)
        if when < cutoff:
            continue
        raw_title = html.unescape(getattr(entry, "title", "") or "").strip()
        if not raw_title:
            continue
        headline, publisher = _split_publisher(raw_title)
        snippet = re.sub(r"<[^>]+>", " ", getattr(entry, "summary", "") or "")
        snippet = html.unescape(_WS.sub(" ", snippet)).strip()[:300]
        item = {"headline": headline, "publisher": publisher or "Google News",
                "summary": snippet, "url": getattr(entry, "link", "") or "",
                "when": when, "layer": "search"}
        # A phrase match is not a subject match, and Google's AND group is a
        # coarse instrument - the local gate is what actually decides.
        if _relevant(item, entity):
            kept.append(item)
        else:
            dropped.append(headline)
    return kept, dropped


def _fetch_google(entity: dict, cfg: dict) -> list:
    """
    Up to two queries per entity.

    Strong aliases are searched unconstrained: "Santos Limited appoints CFO"
    is about Santos whether or not the headline also says Australia. Weak
    aliases are searched with the context group attached, because "Barossa" or
    "Atlas" alone returns wine columns and robots. Running them as one query
    would force the context requirement onto the strong aliases too and lose
    real corporate news.
    """
    strong  = entity.get("terms", [])
    weak    = entity.get("weak_terms", [])
    context = entity.get("require") or DEFAULT_CONTEXT

    items, dropped = _one_query(strong, None, entity, cfg)
    if weak:
        w_items, w_dropped = _one_query(weak, context, entity, cfg)
        items += w_items
        dropped += w_dropped

    seen, out = set(), []
    for i in items:
        key = _norm(i["headline"])[:90]
        if key and key not in seen:
            seen.add(key)
            out.append(i)

    if dropped:
        print(f"   .  {entity['name']}: {len(dropped)} off-subject result(s) filtered")
    entity["_dropped"] = dropped
    return out


def _match_pool(entities: list, pool_items: list) -> dict:
    """Match the existing feed pool against each entity's aliases."""
    by_entity = {e["name"]: [] for e in entities}
    for raw in pool_items or []:
        item = {
            "headline":  html.unescape(raw.get("title", "") or "").strip(),
            "publisher": raw.get("source", "") or "",
            "summary":   html.unescape(raw.get("summary", "") or "").strip()[:300],
            "url":       raw.get("link", "") or "",
            "when":      datetime.datetime.now(AEST_OFFSET),
            "layer":     "feed",
        }
        if not item["headline"]:
            continue
        for entity in entities:
            if _relevant(item, entity):
                by_entity[entity["name"]].append(item)
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


def audit_config(cfg: dict = None) -> list:
    """
    Structural faults that silently destroy precision. Returns a list of
    problems, empty when clean.

    The first one is the bug that broke the original design: a weak alias that
    also appears in its own context list satisfies its own gate, so the gate
    does nothing. "Barossa" was an alias for Santos and a Santos context term,
    so a Barossa Valley wine column passed both checks.
    """
    cfg = cfg or load_cfg()
    problems = []
    for e in _entities(cfg):
        context = e.get("require") or DEFAULT_CONTEXT
        for w in e.get("weak_terms", []):
            # One-directional on purpose. The fault is a context term CONTAINED
            # IN the weak alias, because then matching the alias guarantees
            # matching the context: "State Gas" always contains "gas". The
            # reverse is safe - "Amadeus" as an alias with "Amadeus Basin" as
            # context is exactly the narrowing we want.
            if any(_hit(c, _norm(w)) for c in context):
                problems.append(
                    f"{e['name']}: weak alias '{w}' also appears in its own "
                    f"context list - it satisfies its own gate")
        for x in e.get("exclude", []):
            for t in e.get("terms", []) + e.get("weak_terms", []):
                if _hit(x, _norm(t)):
                    problems.append(
                        f"{e['name']}: exclude '{x}' matches its own alias '{t}'")
        if not e.get("terms") and not e.get("weak_terms"):
            problems.append(f"{e['name']}: no aliases or keywords")
        if e.get("weak_terms") and not e.get("terms") and not e.get("require"):
            problems.append(
                f"{e['name']}: only weak aliases and no context list - "
                f"relies entirely on DEFAULT_CONTEXT")
    return problems


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
                             "count": len(merged),
                             "dropped": e.get("_dropped", [])})

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

    # One call for everything overran max_tokens at 126 items and the reply came
    # back as truncated JSON - every summary lost, including the 40 that had
    # already been written. Chunked, so a failure costs one chunk.
    CHUNK = 30
    done = 0
    for start in range(0, len(items), CHUNK):
        batch = items[start:start + CHUNK]
        blob = "\n".join(
            "{}. [{}] {} ({})".format(n, i.get("publisher", ""), i["headline"],
                                      i.get("summary", "") or "")[:400]
            for n, i in enumerate(batch, 1))
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
                if isinstance(n, int) and 1 <= n <= len(batch):
                    batch[n - 1]["line"]  = str(row.get("line", ""))[:200]
                    batch[n - 1]["grade"] = (row.get("grade") or "notable").lower()
                    done += 1
        except Exception as e:
            print(f"   !  summaries skipped for items "
                  f"{start + 1}-{start + len(batch)} ({e})")
    if done < len(items):
        print(f"   .  graded {done}/{len(items)}")
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
    faults = audit_config(cfg)
    if faults:
        print(f"\nCONFIG FAULTS ({len(faults)}) - precision will suffer until these are fixed:")
        for f in faults:
            print(f"   !  {f}")
    else:
        print("config     : clean")
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

    noisy = [e for e in result["entities"] if e.get("dropped")]
    if noisy:
        print("\nFiltered as off-subject (check none of these should have been kept —")
        print("if one should, loosen that entity's 'Must also mention' list):")
        for e in noisy:
            for h in e["dropped"][:4]:
                print(f"   [{e['name'][:18]:<18}] {h[:64]}")

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
