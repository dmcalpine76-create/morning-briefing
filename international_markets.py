"""
international_markets.py
------------------------
The Global Markets tab: an overnight round-up of the offshore indices, the US
sector rotation, and where ASX-listed international ETFs have not yet caught up
with what their underlying market did while Australia slept.

Ported from the Global Markets Dashboard in the stock picker folder, with four
deliberate changes:

  1. EODHD is gone. The briefing already runs yfinance in every GitHub Actions
     run, so there is no new paid key, no new secret, and no local CORS proxy -
     the proxy existed only because a browser cannot call those APIs directly.

  2. The ASX is out. ASX 200 and All Ords are in the widget bar, and Australian
     market news has its own column. This tab is what happened elsewhere.

  3. Australian-tracking ETFs are out, and not only because of (2). The signal
     compares an ETF's five-day trend against its underlying's OVERNIGHT move.
     For STW, A200, IOZ, VAS and QRE the underlying is the ASX 200, which trades
     in the same session as the ETF - there is no overnight gap to find, so the
     comparison was structurally meaningless for those five.

  4. Eight of the twenty ETFs could never produce a signal in the original,
     because their underlying index was never fetched: WORLD, ASIA, EMERGING,
     BONDS and ASX300 are referenced by ETFs but absent from the index list, so
     the overnight change was always None and the signal stayed 'neutral'
     forever. Each now maps to a US-listed proxy that trades overnight - URTH
     for developed world, AAXJ for Asia ex-Japan, EEM for emerging markets.
     IAF (Australian bonds) is dropped: there is no overnight underlying for it.

Self-test:
    py international_markets.py --probe    # check every symbol resolves
    py international_markets.py --test     # render against live data
"""

import json
import datetime

AEST_OFFSET = datetime.timezone(datetime.timedelta(hours=10))

# ── instruments ─────────────────────────────────────────────────────────────

INDICES = [
    {"id": "SP500",   "sym": "^GSPC",     "name": "S&P 500",       "region": "us",     "flag": "\U0001F1FA\U0001F1F8"},
    {"id": "NASDAQ",  "sym": "^IXIC",     "name": "Nasdaq",        "region": "us",     "flag": "\U0001F1FA\U0001F1F8"},
    {"id": "DOW",     "sym": "^DJI",      "name": "Dow Jones",     "region": "us",     "flag": "\U0001F1FA\U0001F1F8"},
    {"id": "STOXX50", "sym": "^STOXX50E", "name": "Euro Stoxx 50", "region": "europe", "flag": "\U0001F1EA\U0001F1FA"},
    {"id": "FTSE",    "sym": "^FTSE",     "name": "FTSE 100",      "region": "europe", "flag": "\U0001F1EC\U0001F1E7"},
    {"id": "DAX",     "sym": "^GDAXI",    "name": "DAX",           "region": "europe", "flag": "\U0001F1E9\U0001F1EA"},
    {"id": "NIKKEI",  "sym": "^N225",     "name": "Nikkei 225",    "region": "asia",   "flag": "\U0001F1EF\U0001F1F5"},
    {"id": "HANGSENG","sym": "^HSI",      "name": "Hang Seng",     "region": "asia",   "flag": "\U0001F1ED\U0001F1F0"},
    {"id": "SHANGHAI","sym": "000001.SS", "name": "Shanghai Comp", "region": "asia",   "flag": "\U0001F1E8\U0001F1F3"},
    {"id": "GOLD",    "sym": "GC=F",      "name": "Gold",          "region": "commodities", "flag": "\U0001F947"},
    {"id": "OIL",     "sym": "CL=F",      "name": "WTI Crude",     "region": "commodities", "flag": "\U0001F6E2️"},
    {"id": "AUDUSD",  "sym": "AUDUSD=X",  "name": "AUD/USD",       "region": "commodities", "flag": "\U0001F4B1"},
    # proxies for ETF underlyings that have no headline index; not shown in the
    # index table, only used to give those ETFs an overnight reference
    {"id": "WORLD",   "sym": "URTH",      "name": "MSCI World",        "region": "_proxy", "flag": ""},
    {"id": "ASIAXJ",  "sym": "AAXJ",      "name": "Asia ex-Japan",     "region": "_proxy", "flag": ""},
    {"id": "EM",      "sym": "EEM",       "name": "Emerging Markets",  "region": "_proxy", "flag": ""},
]

REGIONS = [
    {"id": "us",          "name": "United States", "flag": "\U0001F1FA\U0001F1F8"},
    {"id": "europe",      "name": "Europe",        "flag": "\U0001F1EA\U0001F1FA"},
    {"id": "asia",        "name": "Asia",          "flag": "\U0001F30F"},
    {"id": "commodities", "name": "Commodities & FX", "flag": "\U0001F6E2️"},
]

US_SECTORS = [
    ("XLK",  "Technology"),        ("XLF",  "Financials"),
    ("XLV",  "Health Care"),       ("XLC",  "Comm Services"),
    ("XLY",  "Cons Discretionary"),("XLP",  "Cons Staples"),
    ("XLI",  "Industrials"),       ("XLE",  "Energy"),
    ("XLB",  "Materials"),         ("XLRE", "Real Estate"),
    ("XLU",  "Utilities"),
]

# ASX-listed ETFs whose underlying trades while Australia is closed.
ETFS = [
    {"code": "IVV",  "name": "iShares S&P 500",            "under": "SP500",   "mer": 0.03},
    {"code": "VTS",  "name": "Vanguard US Total Market",   "under": "SP500",   "mer": 0.03},
    {"code": "NDQ",  "name": "BetaShares Nasdaq 100",      "under": "NASDAQ",  "mer": 0.22},
    {"code": "FANG", "name": "BetaShares FANG+",           "under": "NASDAQ",  "mer": 0.35},
    {"code": "VGS",  "name": "Vanguard MSCI World ex-AU",  "under": "WORLD",   "mer": 0.18},
    {"code": "IWLD", "name": "iShares Core MSCI World",    "under": "WORLD",   "mer": 0.09},
    {"code": "IOO",  "name": "iShares Global 100",         "under": "WORLD",   "mer": 0.40},
    {"code": "IEU",  "name": "iShares Europe",             "under": "STOXX50", "mer": 0.60},
    {"code": "HJPN", "name": "BetaShares Japan Hedged",    "under": "NIKKEI",  "mer": 0.56},
    {"code": "VAE",  "name": "Vanguard FTSE Asia ex-Japan","under": "ASIAXJ",  "mer": 0.40},
    {"code": "IEM",  "name": "iShares MSCI Emerging Mkts", "under": "EM",      "mer": 0.68},
    {"code": "EMKT", "name": "VanEck MSCI Multifactor EM", "under": "EM",      "mer": 0.69},
    {"code": "GOLD", "name": "Global X Physical Gold",     "under": "GOLD",    "mer": 0.15},
    {"code": "OOO",  "name": "BetaShares Crude Oil",       "under": "OIL",     "mer": 0.69},
]

# signal thresholds, unchanged from the dashboard
WEEK_MOVE   = 1.5     # % over five sessions that counts as a trend
NIGHT_MOVE  = 0.3     # % overnight in the underlying that counts as a move

SIGNALS = {
    "reversal":      {"label": "Reversal opportunity", "rank": 100, "col": "#1a6b3c"},
    "momentum":      {"label": "Momentum",             "rank":  50, "col": "#1a3a5c"},
    "watch":         {"label": "Watch",                "rank":  30, "col": "#5c5a52"},
    "neutral":       {"label": "Neutral",              "rank":  10, "col": "#8c887b"},
    "pullback-risk": {"label": "Pullback risk",        "rank": -10, "col": "#8a5a1f"},
    "avoid":         {"label": "Avoid",                "rank": -50, "col": "#8a2b22"},
}


# ── data ────────────────────────────────────────────────────────────────────

def _asx(code: str) -> str:
    return f"{code}.AX"


def all_symbols() -> list:
    """Every Yahoo symbol this module needs, in one list."""
    return ([i["sym"] for i in INDICES]
            + [f"{c}" for c, _ in US_SECTORS]
            + [_asx(e["code"]) for e in ETFS])


def _history(symbols: list, days: int = 12) -> dict:
    """
    {symbol: [rows oldest-first]} via one batched download.

    Each row is (date, close, high, low). A symbol Yahoo cannot serve simply
    does not appear, so every caller must treat a missing symbol as normal.
    """
    import yfinance as yf
    out = {}
    try:
        df = yf.download(symbols, period=f"{days}d", interval="1d",
                         group_by="ticker", auto_adjust=False,
                         progress=False, threads=True)
    except Exception as e:
        print(f"   !  market history download failed: {e}")
        return out

    for sym in symbols:
        try:
            sub = df[sym] if len(symbols) > 1 else df
            sub = sub.dropna(subset=["Close"])
            rows = [(idx.date(), float(r["Close"]), float(r["High"]), float(r["Low"]))
                    for idx, r in sub.iterrows()]
            if len(rows) >= 2:
                out[sym] = rows
        except Exception:
            continue
    return out


def _quote(rows: list) -> dict:
    last, prev = rows[-1], rows[-2]
    return {"close": last[1], "prev": prev[1], "date": last[0],
            "change_pct": 100.0 * (last[1] - prev[1]) / prev[1] if prev[1] else 0.0}


def fetch_markets() -> dict:
    """
    Returns {"indices": {id: quote}, "sectors": [...], "etfs": [...],
             "missing": [...], "asof": date, "error": str|None}
    """
    hist = _history(all_symbols())
    if not hist:
        return {"indices": {}, "sectors": [], "etfs": [], "missing": all_symbols(),
                "asof": None, "error": "no market data returned"}

    missing = [s for s in all_symbols() if s not in hist]

    indices = {}
    for spec in INDICES:
        rows = hist.get(spec["sym"])
        if rows:
            indices[spec["id"]] = {**spec, **_quote(rows)}

    sectors = []
    for code, name in US_SECTORS:
        rows = hist.get(code)
        if rows:
            q = _quote(rows)
            sectors.append({"code": code, "name": name, **q})
    sectors.sort(key=lambda s: -s["change_pct"])

    etfs = []
    for spec in ETFS:
        rows = hist.get(_asx(spec["code"]))
        if not rows:
            continue
        q = _quote(rows)
        closes = [r[1] for r in rows]
        trend5 = (100.0 * (closes[-1] - closes[-6]) / closes[-6]) if len(closes) >= 6 else None
        week_hi = max(r[2] for r in rows[-6:]) if len(rows) >= 6 else None
        week_lo = min(r[3] for r in rows[-6:]) if len(rows) >= 6 else None
        under = indices.get(spec["under"])
        night = under["change_pct"] if under else None

        signal, note = "neutral", "No overnight reference"
        if trend5 is not None and night is not None:
            w_bear, w_bull = trend5 < -WEEK_MOVE, trend5 > WEEK_MOVE
            w_flat = abs(trend5) <= WEEK_MOVE
            o_bull, o_bear = night > NIGHT_MOVE, night < -NIGHT_MOVE
            if w_bear and o_bull:
                signal, note = "reversal", (f"Down {abs(trend5):.1f}% this week, "
                                            f"underlying up {night:.1f}% overnight")
            elif w_bull and o_bear:
                signal, note = "pullback-risk", (f"Up {trend5:.1f}% this week, "
                                                 f"underlying fell {abs(night):.1f}% overnight")
            elif w_bull and o_bull:
                signal, note = "momentum", (f"Up {trend5:.1f}% this week, "
                                            f"underlying up {night:.1f}% overnight")
            elif w_bear and o_bear:
                signal, note = "avoid", (f"Down {abs(trend5):.1f}% this week, "
                                         f"underlying down {abs(night):.1f}% overnight")
            elif w_flat and o_bull:
                signal, note = "watch", (f"Flat this week, underlying up "
                                         f"{night:.1f}% overnight - may be lagging")
            else:
                note = f"{trend5:+.1f}% this week, underlying {night:+.1f}% overnight"
        etfs.append({**spec, **q, "trend5": trend5, "week_hi": week_hi, "week_lo": week_lo,
                     "night": night, "under_name": under["name"] if under else "",
                     "signal": signal, "note": note,
                     "rank": SIGNALS[signal]["rank"]})
    etfs.sort(key=lambda e: (-e["rank"], -(e["trend5"] or 0)))

    asof = max((v["date"] for v in indices.values()), default=None)
    return {"indices": indices, "sectors": sectors, "etfs": etfs,
            "missing": missing, "asof": asof, "error": None}


# ── round-up ────────────────────────────────────────────────────────────────

def write_roundup(client, data: dict) -> dict:
    """
    One short paragraph per region plus a one-word stance.

    Returns {region_id: {"text": str, "stance": str}}. Any failure returns {}
    and the tab renders the numbers without commentary - the prices are the
    point, the prose is the garnish.
    """
    idx = data.get("indices") or {}
    if not client or not idx:
        return {}

    lines = []
    for r in REGIONS:
        members = [v for v in idx.values() if v["region"] == r["id"]]
        if not members:
            continue
        lines.append(f"{r['name']}: " + "; ".join(
            f"{m['name']} {m['close']:,.2f} ({m['change_pct']:+.2f}%)" for m in members))
    if not lines:
        return {}

    sect = data.get("sectors") or []
    sect_line = ""
    if len(sect) >= 3:
        top = ", ".join(f"{s['name']} {s['change_pct']:+.1f}%" for s in sect[:3])
        bot = ", ".join(f"{s['name']} {s['change_pct']:+.1f}%" for s in sect[-3:])
        sect_line = f"\nUS sectors - strongest: {top}. Weakest: {bot}."

    prompt = f"""Overnight market data, most recent completed session:

{chr(10).join(lines)}{sect_line}

Write a round-up for an Australian company director reading at 5am, before the
ASX opens. For EACH region listed above, write exactly two sentences: what moved
and by how much, then the most likely driver. Use the actual numbers.

Rules:
- Only what the data above supports. Never invent a cause you cannot see in the
  numbers; if the driver is not evident, say what moved and stop.
- No advice, no "investors should", no disclaimers, no "it is worth noting".
- Plain prose. No markdown, no bullets, no asterisks.

Return ONLY a JSON object, no fences, keyed by region id, each value an object
with "text" (the two sentences) and "stance" (exactly one of Bullish, Bearish,
Mixed, Neutral). Region ids: {", ".join(r['id'] for r in REGIONS)}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
            timeout=60,
        )
        raw = msg.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        got = json.loads(raw)
        ok = {}
        for r in REGIONS:
            v = got.get(r["id"]) or {}
            text = str(v.get("text", "")).strip()
            stance = str(v.get("stance", "")).strip().title()
            if text:
                ok[r["id"]] = {"text": text[:600],
                               "stance": stance if stance in
                                         ("Bullish", "Bearish", "Mixed", "Neutral") else "Mixed"}
        return ok
    except Exception as e:
        print(f"   !  markets round-up skipped ({e})")
        return {}


# ── render ──────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    import html
    return html.escape(str(s if s is not None else ""))


def _pc(v, dp: int = 2) -> str:
    return "&mdash;" if v is None else f"{v:+.{dp}f}%"


def _col(v) -> str:
    if v is None:
        return "#8c887b"
    return "#1a6b3c" if v > 0.05 else ("#8a2b22" if v < -0.05 else "#5c5a52")


CSS = """
<style>
.gm-wrap{max-width:var(--page-max,1800px);margin:0 auto;padding:1.5rem 1.5rem 3rem}
.gm-sec{display:flex;align-items:center;gap:.5rem;padding-bottom:.5rem;margin:0 0 1rem;
  border-bottom:3px solid var(--ink,#1a1a17);font-size:1rem;font-weight:700}
.gm-sec .gm-note{margin-left:auto;font-size:.62rem;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-light,#6b6862);background:var(--paper-2,#f4f1e8);
  border:1px solid var(--rule,#e4e1d8);padding:.12rem .4rem;border-radius:2rem}
.gm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
  gap:1.4rem;align-items:start;margin-bottom:2rem}
.gm-card{background:#fff;border:1px solid var(--rule,#e4e1d8);border-radius:3px;
  padding:.85rem 1rem;transition:box-shadow .12s}
.gm-card:hover{box-shadow:0 1px 5px rgba(0,0,0,.08)}
.gm-card-h{display:flex;align-items:center;gap:.45rem;margin-bottom:.6rem;
  padding-bottom:.45rem;border-bottom:1px solid var(--rule,#e4e1d8)}
.gm-card-t{font-weight:700;font-size:.9rem}
.gm-stance{margin-left:auto;font-size:.55rem;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;padding:.1rem .4rem;border-radius:2px;color:#fff}
.gm-row{display:flex;align-items:baseline;gap:.5rem;padding:.3rem 0;font-size:.8rem}
.gm-row .n{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gm-row .v{font-variant-numeric:tabular-nums;color:#3d3a34}
.gm-row .c{font-variant-numeric:tabular-nums;font-weight:700;min-width:4.2rem;text-align:right}
.gm-say{font-size:.8rem;color:#484848;line-height:1.6;margin-top:.6rem;
  padding-top:.6rem;border-top:1px dotted rgba(0,0,0,.14)}
.gm-heat{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));
  gap:.5rem;margin-bottom:2rem}
.gm-tile{border:1px solid var(--rule,#e4e1d8);border-radius:3px;padding:.55rem .6rem;background:#fff}
.gm-tile .t{font-size:.68rem;font-weight:700;color:#3d3a34;line-height:1.2}
.gm-tile .p{font-size:.95rem;font-weight:700;font-variant-numeric:tabular-nums;margin-top:.2rem}
.gm-etf{background:#fff;border:1px solid var(--rule,#e4e1d8);border-radius:3px;
  padding:.85rem 1rem;margin-bottom:.5rem;border-left:3px solid var(--sig,#8c887b)}
.gm-etf-h{display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap}
.gm-code{font-size:.6rem;font-weight:700;letter-spacing:.06em;background:#1a1a17;color:#fff;
  padding:.08rem .34rem;border-radius:3px}
.gm-etf-n{font-weight:700;font-size:.88rem}
.gm-sig{margin-left:auto;font-size:.58rem;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--sig,#8c887b)}
.gm-etf-b{font-size:.79rem;color:#484848;line-height:1.55;margin-top:.3rem}
.gm-nums{display:flex;gap:1.1rem;flex-wrap:wrap;margin-top:.35rem;font-size:.68rem;
  color:var(--ink-light,#6b6862);font-variant-numeric:tabular-nums}
.gm-nums b{color:#3d3a34}
.gm-lead{font-size:.75rem;color:var(--ink-light,#6b6862);line-height:1.55;margin:-.4rem 0 1.2rem}
.gm-empty{padding:2rem;text-align:center;color:var(--ink-light,#6b6862);font-style:italic}
.gm-miss{margin-top:1.2rem;font-size:.63rem;color:#8a6d1f}
</style>
"""


def build_tab(data: dict, roundup: dict = None, now: datetime.datetime = None) -> str:
    now = now or datetime.datetime.now(AEST_OFFSET)
    roundup = roundup or {}
    idx, sect, etfs = data.get("indices") or {}, data.get("sectors") or [], data.get("etfs") or []

    if not idx and not etfs:
        return (CSS + '<div class="gm-wrap"><div class="gm-empty">'
                + _esc(data.get("error") or "No market data this run.")
                + "</div></div>")

    asof = data.get("asof")
    asof_txt = asof.strftime("%a %d %b") if asof else "latest session"

    # region cards
    cards = []
    for r in REGIONS:
        members = [v for v in idx.values() if v["region"] == r["id"]]
        if not members:
            continue
        rows = "".join(
            f'<div class="gm-row"><span class="n">{_esc(m["flag"])} {_esc(m["name"])}</span>'
            f'<span class="v">{m["close"]:,.2f}</span>'
            f'<span class="c" style="color:{_col(m["change_pct"])}">{_pc(m["change_pct"])}</span></div>'
            for m in members)
        say = roundup.get(r["id"]) or {}
        stance = say.get("stance")
        scol = {"Bullish": "#1a6b3c", "Bearish": "#8a2b22",
                "Mixed": "#8a5a1f", "Neutral": "#5c5a52"}.get(stance, "#8c887b")
        chip = (f'<span class="gm-stance" style="background:{scol}">{_esc(stance)}</span>'
                if stance else "")
        prose = f'<div class="gm-say">{_esc(say["text"])}</div>' if say.get("text") else ""
        cards.append(f'<div class="gm-card"><div class="gm-card-h">'
                     f'<span class="gm-card-t">{_esc(r["flag"])} {_esc(r["name"])}</span>{chip}</div>'
                     f'{rows}{prose}</div>')

    # US sector heat
    heat = ""
    if sect:
        tiles = "".join(
            f'<div class="gm-tile"><div class="t">{_esc(s["name"])}</div>'
            f'<div class="p" style="color:{_col(s["change_pct"])}">{_pc(s["change_pct"], 1)}</div></div>'
            for s in sect)
        heat = ('<div class="gm-sec">US sector rotation'
                f'<span class="gm-note">{len(sect)} sectors</span></div>'
                '<div class="gm-lead">SPDR sector funds, ranked by the last completed '
                'US session. Where the money went, not where it is.</div>'
                f'<div class="gm-heat">{tiles}</div>')

    # ETFs
    live = [e for e in etfs if e["signal"] in ("reversal", "momentum", "watch", "pullback-risk", "avoid")]
    rest = [e for e in etfs if e not in live]
    def etf_html(e):
        col = SIGNALS[e["signal"]]["col"]
        return (f'<div class="gm-etf" style="--sig:{col}"><div class="gm-etf-h">'
                f'<span class="gm-code">{_esc(e["code"])}</span>'
                f'<span class="gm-etf-n">{_esc(e["name"])}</span>'
                f'<span class="gm-sig">{_esc(SIGNALS[e["signal"]]["label"])}</span></div>'
                f'<div class="gm-etf-b">{_esc(e["note"])}</div>'
                f'<div class="gm-nums">'
                f'<span>last <b>${e["close"]:,.2f}</b></span>'
                f'<span>session <b>{_pc(e["change_pct"])}</b></span>'
                f'<span>5-day <b>{_pc(e["trend5"], 1)}</b></span>'
                f'<span>{_esc(e["under_name"])} overnight <b>{_pc(e["night"], 1)}</b></span>'
                f'<span>MER {e["mer"]:.2f}%</span>'
                f'</div></div>')
    etf_block = ""
    if etfs:
        etf_block = ('<div class="gm-sec">ASX-listed international ETFs'
                     f'<span class="gm-note">{len(etfs)} tracked</span></div>'
                     '<div class="gm-lead">Each ETF trades on the ASX while its underlying market '
                     'is closed, so an overnight move offshore has not been priced in yet. '
                     'A reversal is a fund down over the week whose underlying rose overnight. '
                     'These are observations about pricing, not recommendations.</div>'
                     + "".join(etf_html(e) for e in live + rest))

    missing = data.get("missing") or []
    miss = (f'<div class="gm-miss">{len(missing)} symbol(s) returned no data this run: '
            f'{_esc(", ".join(missing[:12]))}.</div>') if missing else ""

    return (CSS + '<div class="gm-wrap">'
            f'<div class="gm-sec">Overnight round-up'
            f'<span class="gm-note">{_esc(asof_txt)}</span></div>'
            '<div class="gm-lead">Offshore markets only &mdash; the ASX is in the widget bar '
            'above and the news columns. Figures are the last completed session on each '
            'exchange, so US and European numbers are from overnight.</div>'
            f'<div class="gm-grid">{"".join(cards)}</div>'
            f'{heat}{etf_block}{miss}</div>')


# ── CLI ─────────────────────────────────────────────────────────────────────

def _probe():
    """
    Resolve every symbol and report. Yahoo is unreachable from both the cloud
    workspace and the desktop VM, so this is meant to be run on the GitHub
    runner - the only place with the same network as production.
    """
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    syms = all_symbols()
    print(f"probing {len(syms)} symbols")
    hist = _history(syms)
    ok, bad = [], []
    for spec in INDICES:
        (ok if spec["sym"] in hist else bad).append(f"index   {spec['sym']:<12} {spec['name']}")
    for code, name in US_SECTORS:
        (ok if code in hist else bad).append(f"sector  {code:<12} {name}")
    for e in ETFS:
        (ok if _asx(e['code']) in hist else bad).append(f"etf     {_asx(e['code']):<12} {e['name']}")
    for line in ok:
        print("  OK   " + line)
    for line in bad:
        print("  MISS " + line)
    print(f"\n{len(ok)} resolved, {len(bad)} missing")
    return 1 if bad else 0


def _test():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    data = fetch_markets()
    print(f"indices {len(data['indices'])}  sectors {len(data['sectors'])}  "
          f"etfs {len(data['etfs'])}  missing {len(data['missing'])}")
    for e in data["etfs"][:6]:
        print(f"  {e['code']:<5} {e['signal']:<14} {e['note']}")
    client = None
    try:
        import anthropic, os as _os
        if _os.environ.get("ANTHROPIC_API_KEY"):
            client = anthropic.Anthropic(api_key=_os.environ["ANTHROPIC_API_KEY"])
    except Exception:
        pass
    roundup = write_roundup(client, data)
    html = build_tab(data, roundup)
    from pathlib import Path
    out = Path(__file__).parent / "global_markets_preview.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    if "--probe" in sys.argv:
        sys.exit(_probe())
    sys.exit(_test())
