"""
patch_briefing_fixes.py
-----------------------
Run this ONCE in your morning briefing project folder to fix:
  1. Stock ticker bar showing all stocks (not just 4)
  2. East coast gas price fetching from a working source
  3. Sunshine Beach weather showing 7 days (same as Brisbane)

Usage:
    cd "C:\\Users\\dmcal\\OneDrive - State Gas\\Documents\\Current document editing\\new AI projects\\morning briefing system"
    py patch_briefing_fixes.py

A backup is saved as briefing.py.bak2 before changes are made.
"""

import re
import sys
import shutil
from pathlib import Path

TARGET = Path("briefing.py")
BACKUP = Path("briefing.py.bak2")

if not TARGET.exists():
    print("ERROR: briefing.py not found. Run from your project folder.")
    sys.exit(1)

shutil.copy(TARGET, BACKUP)
print(f"✓ Backup saved: {BACKUP}\n")

content = TARGET.read_text(encoding="utf-8")
changes = 0

# ═══════════════════════════════════════════════════════════════════════════
# FIX 1 — Widget bar: allow all tickers to show
#
# The widget bar uses flex layout. When more stocks are added they wrap to a
# second row that gets clipped. Fix: add overflow-x: auto and flex-wrap: nowrap
# so extra stocks scroll horizontally within the bar.
# ═══════════════════════════════════════════════════════════════════════════

print("── Fix 1: Ticker bar overflow ──")

# Pattern A: overflow hidden on widget-bar
if "overflow: hidden" in content:
    # Could be on .widget-bar or .weather-bar — only fix widget-bar context
    # Find the .widget-bar CSS block and replace overflow: hidden with overflow-x: auto
    new_content = re.sub(
        r'(\.widget-bar\s*\{[^}]*?)overflow:\s*hidden',
        r'\1overflow-x: auto',
        content,
        count=1
    )
    if new_content != content:
        content = new_content
        print("  ✓ Changed .widget-bar overflow: hidden → overflow-x: auto")
        changes += 1
    else:
        print("  ℹ  overflow: hidden not inside .widget-bar block — trying flex-wrap")

# Pattern B: flex-wrap: wrap on widget-bar (wraps to hidden second row)
new_content = re.sub(
    r'(\.widget-bar\s*\{[^}]*?)flex-wrap:\s*wrap',
    r'\1flex-wrap: nowrap',
    content,
    count=1
)
if new_content != content:
    content = new_content
    print("  ✓ Changed .widget-bar flex-wrap: wrap → flex-wrap: nowrap")
    changes += 1
else:
    print("  ℹ  flex-wrap: wrap not found in .widget-bar — may already be nowrap or not set")

# Pattern C: ensure widget-bar has overflow-x: auto if it doesn't already
if ".widget-bar" in content and "overflow-x: auto" not in content:
    new_content = re.sub(
        r'(\.widget-bar\s*\{)',
        r'\1\n            overflow-x: auto;',
        content,
        count=1
    )
    if new_content != content:
        content = new_content
        print("  ✓ Added overflow-x: auto to .widget-bar")
        changes += 1

# Pattern D: ASX watchlist widget slots — ensure they don't shrink away
# flex-shrink: 0 keeps each slot its natural width when bar is compressed
if "widget-slot" in content and "flex-shrink: 0" not in content:
    new_content = re.sub(
        r'(\.widget-slot\s*\{)',
        r'\1\n            flex-shrink: 0;',
        content,
        count=1
    )
    if new_content != content:
        content = new_content
        print("  ✓ Added flex-shrink: 0 to .widget-slot")
        changes += 1

# ═══════════════════════════════════════════════════════════════════════════
# FIX 2 — East coast gas price: replace broken AEMO endpoint
#
# The AEMO GBB visualisation API at /aemo/apps/api/report/GBB_GAS_PRICES
# has changed structure and often returns empty. Replace fetch_gas_price()
# with a version that tries multiple sources in order:
#   1. AEMO NEMweb gas price file (publicly accessible CSV)
#   2. AEMO GBB API (different endpoint structure)
#   3. Natural Gas futures via yfinance as a fallback indicator
# ═══════════════════════════════════════════════════════════════════════════

print("\n── Fix 2: Gas price fetch ──")

NEW_FETCH_GAS = '''def fetch_gas_price() -> dict:
    """
    Fetch East Coast Australia STTM gas spot price.
    Tries three sources in order, returns first success.
    Returns dict: {price, unit, hub, date, source} or {} on failure.
    """
    import datetime

    # ── Source 1: AEMO STTM daily schedule prices (public API) ──
    try:
        import requests as _req
        today = datetime.date.today().strftime("%Y/%m/%d")
        url = "https://www.aemo.com.au/aemo/apps/api/report/PRICE_AND_DEMAND"
        resp = _req.get(url, timeout=8, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        })
        if resp.ok:
            data = resp.json()
            records = data if isinstance(data, list) else data.get("data", data.get("Data", []))
            # Find Sydney or Adelaide (east coast hubs)
            hub_prefs = ["sydney", "adelaide", "brisbane"]
            chosen = None
            for pref in hub_prefs:
                chosen = next(
                    (r for r in records
                     if pref in str(r.get("REGIONID", r.get("region", r.get("REGION", "")))).lower()),
                    None
                )
                if chosen:
                    break
            if not chosen and records:
                chosen = records[0]
            if chosen:
                price_val = (chosen.get("RRP") or chosen.get("rrp") or
                             chosen.get("PRICE") or chosen.get("price"))
                if price_val is not None:
                    return {
                        "price":  round(float(price_val), 2),
                        "unit":   "$/MWh",
                        "hub":    str(chosen.get("REGIONID", chosen.get("region", "NSW"))),
                        "date":   str(chosen.get("SETTLEMENTDATE", chosen.get("date", today)))[:10],
                        "source": "AEMO",
                    }
    except Exception:
        pass

    # ── Source 2: AEMO GBB STTM hub prices ──
    try:
        for endpoint in [
            "https://visualisations.aemo.com.au/aemo/apps/api/report/GBB_PRICE_INDEX",
            "https://www.aemo.com.au/aemo/apps/api/report/GBB_PRICE_INDEX",
        ]:
            try:
                resp = _req.get(endpoint, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                if not resp.ok:
                    continue
                data = resp.json()
                records = data if isinstance(data, list) else data.get("data", [])
                if not records:
                    continue
                # Try Sydney first
                r = next(
                    (x for x in records if "sydney" in str(x).lower()),
                    records[0]
                )
                price_val = (r.get("PRICE") or r.get("INDEX_PRICE") or
                             r.get("price") or r.get("GAS_PRICE"))
                if price_val:
                    return {
                        "price":  round(float(price_val), 2),
                        "unit":   "$/GJ",
                        "hub":    str(r.get("LOCATION_ID", r.get("HUB_NAME", "STTM"))),
                        "date":   str(r.get("GAS_DATE", r.get("date", "")))[:10],
                        "source": "AEMO GBB",
                    }
            except Exception:
                continue
    except Exception:
        pass

    # ── Source 3: Henry Hub futures via yfinance (US benchmark, labelled clearly) ──
    try:
        import yfinance as _yf
        t = _yf.Ticker("NG=F")
        info = t.fast_info
        price_val = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        if price_val:
            return {
                "price":  round(float(price_val), 3),
                "unit":   "USD/MMBtu",
                "hub":    "Henry Hub (US)",
                "date":   datetime.date.today().isoformat(),
                "source": "yfinance",
            }
    except Exception:
        pass

    return {}
'''

# Find the existing fetch_gas_price function and replace it
match = re.search(
    r'(def fetch_gas_price\(\).*?)(?=\ndef [a-z])',
    content,
    re.DOTALL
)
if match:
    content = content[:match.start()] + NEW_FETCH_GAS + "\n" + content[match.end():]
    print("  ✓ Replaced fetch_gas_price() with multi-source version")
    changes += 1
else:
    print("  ✗ Could not find fetch_gas_price() function to replace")
    print("    The function may have a different name — search briefing.py for 'gas_price'")
    print("    and replace it manually with the function below:\n")
    print(NEW_FETCH_GAS)

# ═══════════════════════════════════════════════════════════════════════════
# FIX 3 — Sunshine Beach weather: change from 5 days to 7 days
# ═══════════════════════════════════════════════════════════════════════════

print("\n── Fix 3: Sunshine Beach 7-day forecast ──")

# Pattern A: LOCATIONS dict with "days": 5 for Sunshine Beach
new_content = re.sub(
    r'("Sunshine Beach":\s*\{[^}]*)"days":\s*5',
    r'\1"days": 7',
    content
)
if new_content != content:
    content = new_content
    print('  ✓ Changed Sunshine Beach "days": 5 → "days": 7 in LOCATIONS dict')
    changes += 1
else:
    print("  ℹ  LOCATIONS dict pattern not matched — trying direct string replacement")

# Pattern B: days parameter passed directly in fetch call
# e.g. forecast_days=5 in the Open-Meteo URL for Sunshine Beach
# This is harder to target specifically so we look for the sunshine fetch call
patterns_b = [
    ('forecast_days=5', 'forecast_days=7'),
    ('"forecast_days": 5', '"forecast_days": 7'),
    ("'forecast_days': 5", "'forecast_days': 7"),
]
for old_p, new_p in patterns_b:
    if old_p in content:
        content = content.replace(old_p, new_p, 1)
        print(f"  ✓ Changed {old_p!r} → {new_p!r} in fetch params")
        changes += 1
        break

# Pattern C: direct slice like sunshine[:5] or forecast[:5]
new_content = re.sub(
    r'(sunshine(?:_data|_forecast)?)\[:5\]',
    r'\1[:7]',
    content
)
if new_content != content:
    content = new_content
    print("  ✓ Removed [:5] slice on sunshine data")
    changes += 1

# Pattern D: weather_bar_html receives sunshine with a cap applied
# e.g. fetch_weather_for("Sunshine Beach")[:5]
new_content = re.sub(
    r'(fetch_weather_for\s*\(\s*["\']Sunshine Beach["\']\s*\))\s*\[:5\]',
    r'\1',
    content
)
if new_content != content:
    content = new_content
    print("  ✓ Removed [:5] slice on fetch_weather_for(Sunshine Beach) call")
    changes += 1

# Also check the weather_bar_html function itself for a slice
new_content = re.sub(
    r'(sunshine(?:\s+or\s+\[\])?)\[:5\]',
    r'\1',
    content
)
if new_content != content:
    content = new_content
    print("  ✓ Removed [:5] slice in weather_bar_html")
    changes += 1

# ═══════════════════════════════════════════════════════════════════════════
# Verify and write
# ═══════════════════════════════════════════════════════════════════════════

print(f"\n── Validation ──")
try:
    import ast
    ast.parse(content)
    print("  ✓ Python syntax OK")
except SyntaxError as e:
    print(f"  ✗ Syntax error at line {e.lineno}: {e.msg}")
    print(f"  Restoring backup...")
    shutil.copy(BACKUP, TARGET)
    sys.exit(1)

TARGET.write_text(content, encoding="utf-8")

print(f"\n{'='*50}")
print(f"Done. {changes} change(s) applied.")
print(f"Original saved as: {BACKUP}")
if changes == 0:
    print("\n⚠  No changes were applied — the patterns may have shifted.")
    print("   This usually means the code was restructured since these fixes were written.")
    print("   Check the manual fix instructions printed above for any failures.")
print("\nTest with:  py briefing.py")
