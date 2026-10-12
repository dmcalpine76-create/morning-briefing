"""
test_north_america_gate.py
--------------------------
Energy Market Watch drops North American items that are not about energy.

Unlike test_market_relevance.py, these headlines are CONSTRUCTED, not taken
from a production run - written to the shapes the gate has to tell apart. The
real-headline regression set is test_market_relevance.py, which must still
pass unchanged.

    py tests/test_north_america_gate.py
"""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import market_monitor as mm

def it(h, s="", pub="Google News"):
    return {"headline": h, "summary": s, "publisher": pub,
            "when": datetime.datetime.now(mm.AEST_OFFSET), "layer": "search"}

GATED = [   # North American, not energy -> gate fires
    it("Origin Materials shares slide on Nasdaq after quarterly miss"),
    it("Beach volleyball finals set for Houston this weekend"),
    it("Comet sighting draws crowds across Ontario"),
    it("Santos named in Texas restaurant chain lawsuit"),
    it("Arrow appoints new chief marketing officer", pub="PR Newswire"),
]
PASSES = [  # must NOT be gated
    it("Santos advances Pikka oil project in Alaska"),
    it("Woodside commits to Louisiana LNG final investment decision"),
    it("US LNG exports climb as Gulf Coast terminals ramp up"),
    it("Dyno Nobel to idle Louisiana ammonia plant on gas costs"),
    it("Canadian utility signs power deal with AGL"),
    it("Origin Energy shares rise on ASX as US rates ease"),          # Australian angle
    it("Senex lifts Atlas output as Surat gas demand climbs"),         # not North American
    it("Comet Ridge appoints chief executive", pub="The Australian"), # not North American
]

ents = {e["name"]: e for e in mm._entities(mm.load_cfg())}
off  = dict(ents["Santos"]); off["na_gate"] = False

CHECKS = (
    [(f"gated: {i['headline'][:60]}", mm._north_american_off_topic(i)) for i in GATED]
  + [(f"kept:  {i['headline'][:60]}", not mm._north_american_off_topic(i)) for i in PASSES]
  + [
    ("the company's own name is not evidence of energy",
     mm._north_american_off_topic(it("Blue Energy Lounge opens third Chicago location"),
                                  ents["Blue Energy"])),
    ("...but a real energy word still is",
     not mm._north_american_off_topic(it("Blue Energy raises funds for Texas nuclear plant"),
                                      ents["Blue Energy"])),
    ("the gate is wired into _relevant",
     not mm._relevant(it("Santos named in Texas restaurant chain lawsuit"), ents["Santos"])),
    ("the setting switches it off",
     mm._north_american_off_topic(it("Santos named in Texas restaurant chain lawsuit"))
     and mm._relevant(it("Santos Limited named in Texas restaurant chain lawsuit"), off)),
    ("no bare 'US' in NA_GEO - it matches the pronoun",
     "US" not in mm.NA_GEO and "us" not in [g.lower() for g in mm.NA_GEO]),
    ("Washington is not treated as North American (Soul Patts is on the ASX)",
     not mm._north_american_off_topic(it("Washington H. Soul Pattinson lifts dividend"))),
    ("cap is five per company",
     mm.load_cfg().get("max_per_entity") == 5),
  ])

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
