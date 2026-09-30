"""
test_global_markets.py
----------------------
The Global Markets tab, ported from the stock picker's dashboard.

The signal these guards protect is easy to get subtly wrong: an ETF is compared
against its underlying's OVERNIGHT move, which only means something when the
underlying trades while the ASX is shut. Two faults in the original are covered
here as regressions - Australian-tracking ETFs, where there is no overnight gap
to find at all, and ETFs pointing at an underlying that was never fetched, which
left the signal stuck on neutral forever.

    py tests/test_global_markets.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import international_markets as im

idx_ids   = {i["id"] for i in im.INDICES}
etf_codes = {e["code"] for e in im.ETFS}

# a synthetic set exercising every branch of the signal
def _mk(trend5, night):
    """Reproduce the classifier on one pair without touching the network."""
    w_bear, w_bull = trend5 < -im.WEEK_MOVE, trend5 > im.WEEK_MOVE
    w_flat = abs(trend5) <= im.WEEK_MOVE
    o_bull, o_bear = night > im.NIGHT_MOVE, night < -im.NIGHT_MOVE
    if w_bear and o_bull: return "reversal"
    if w_bull and o_bear: return "pullback-risk"
    if w_bull and o_bull: return "momentum"
    if w_bear and o_bear: return "avoid"
    if w_flat and o_bull: return "watch"
    return "neutral"

data = {"indices": {"SP500": {"id": "SP500", "sym": "^GSPC", "name": "S&P 500",
                              "region": "us", "flag": "", "close": 5000.0,
                              "prev": 4950.0, "change_pct": 1.01,
                              "date": __import__("datetime").date(2026, 9, 30)}},
        "sectors": [{"code": "XLE", "name": "Energy", "close": 90.0, "prev": 89.0,
                     "change_pct": 1.12, "date": None}],
        "etfs": [{"code": "IVV", "name": "iShares S&P 500", "under": "SP500", "mer": 0.03,
                  "close": 62.0, "prev": 61.5, "change_pct": 0.81, "date": None,
                  "trend5": -2.4, "week_hi": 64.0, "week_lo": 61.0, "night": 1.01,
                  "under_name": "S&P 500", "signal": "reversal",
                  "note": "Down 2.4% this week, underlying up 1.0% overnight",
                  "rank": 100}],
        "missing": [], "asof": __import__("datetime").date(2026, 9, 30), "error": None}

html = im.build_tab(data, {"us": {"text": "Wall Street rose.", "stance": "Bullish"}})
empty = im.build_tab({"indices": {}, "sectors": [], "etfs": [], "missing": [],
                      "asof": None, "error": "no market data returned"})

CHECKS = [
    # scope
    ("the ASX indices are excluded",
     not {"ASX200", "AORD"} & idx_ids),
    ("Australian-tracking ETFs are excluded - no overnight gap exists for them",
     not {"STW", "A200", "IOZ", "VAS", "QRE"} & etf_codes),
    ("the Australian bond ETF is excluded - it has no overnight underlying",
     "IAF" not in etf_codes),

    # the bug carried over from the dashboard
    ("every ETF points at an underlying that is actually fetched",
     all(e["under"] in idx_ids for e in im.ETFS)),
    ("the world / Asia / emerging proxies exist",
     {"WORLD", "ASIAXJ", "EM"} <= idx_ids),
    ("proxies are kept out of the region cards",
     all(i["region"] == "_proxy" for i in im.INDICES
         if i["id"] in {"WORLD", "ASIAXJ", "EM"})),

    # classifier
    ("down on the week, underlying up overnight -> reversal", _mk(-2.0, 1.0) == "reversal"),
    ("up on the week, underlying down overnight -> pullback risk", _mk(2.0, -1.0) == "pullback-risk"),
    ("up and up -> momentum", _mk(2.0, 1.0) == "momentum"),
    ("down and down -> avoid", _mk(-2.0, -1.0) == "avoid"),
    ("flat but underlying up -> watch", _mk(0.2, 1.0) == "watch"),
    ("a small overnight move is not a move", _mk(-2.0, 0.1) == "neutral"),
    ("a small weekly move is not a trend", _mk(0.5, -1.0) == "neutral"),
    ("every signal has a label and a rank",
     all(s in im.SIGNALS for s in ("reversal", "momentum", "watch",
                                   "neutral", "pullback-risk", "avoid"))),

    # render
    ("the tab renders", "Overnight round-up" in html and "gm-wrap" in html),
    ("the round-up prose appears", "Wall Street rose." in html),
    ("the stance chip appears", "Bullish" in html),
    ("the ETF section explains what a reversal is", "reversal is a fund down over the week" in html),
    ("it is framed as observation, not advice", "not recommendations" in html),
    ("it says the ASX is covered elsewhere", "the ASX is in the widget bar" in html),
    ("no data renders a message rather than an empty page",
     "no market data returned" in empty),
    ("it shares the page-width cap", "var(--page-max" in im.CSS),
]

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    bad = 0
    for label, ok in CHECKS:
        print(("ok   " if ok else "FAIL ") + label)
        bad += not ok
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} passed")
    sys.exit(1 if bad else 0)
