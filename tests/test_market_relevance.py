"""
test_market_relevance.py
------------------------
Every case here is a real headline from a production Market Watch run, not an
invented one. The DROP list is what the filter let through and should not have;
the KEEP list is what it must never start dropping in the course of tightening.

Lives in the repo because it was written twice already - once from the 23 Sep
run and once after a sandbox restart lost the first copy.

    py tests/test_market_relevance.py
"""
import sys, datetime
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import market_monitor as mm
ents = {e["name"]: e for e in mm._entities(mm.load_cfg())}
def it(h, s="", pub="Google News"):
    return {"headline": h, "summary": s, "publisher": pub,
            "when": datetime.datetime.now(mm.AEST_OFFSET), "layer": "search"}

DROP = [
 # from the 23 Sep production page - the residual three patterns
 ("Woodside Energy","Freo can make history this weekend - and the Dockers know it"),
 ("Woodside Energy","Male stabbed, suspect wanted in Scarborough stabbing"),
 ("Woodside Energy","Macclesfield FC vs. Scarborough Athletic - Live Score"),
 ("Senex Energy","Gas prices surge across Illinois amid Great Lakes cold snap"),
 ("Mineral Resources","Minister of Mines and Petroleum Stresses Efficiency Drive"),
 ("Mineral Resources","Lawyer speaks after Norwest shooting charge"),
 ("Gas M&A and farm-ins","Navitas forges ahead in South Africa as it wraps up acquisition"),
 ("Drilling and services capacity","Jindal Drilling Q1 Results: New ONGC Rig Contract"),
 ("LNG import terminals","Seatrium completes seventh FSRU for Karpowership"),
 ("Domestic gas policy","European energy industry urges EU not to cap gas prices"),
 ("Small-cap energy capital markets","Bounty Oil & Gas NL Acquires PetroQuest Liberia"),
 ("Gas prices and benchmarks","US LNG Exports to Europe Decline as Asia Prices Surge"),
 # and the originals, which must stay dropped
 ("Blue Energy","Garmin epix Pro Gen 2 Sapphire Edition - 16-Day Battery"),
 ("Senex Energy","Apollo's Atlas Facing Possible $1.1 Billion Loss From MF"),
 ("Santos","WanderList: Barossa"),
 ("Arrow Energy","Tipton Girls Varsity Volleyball @ Indiahoma"),
 ("Vintage Energy","Xbox 360 Emulation on the Odin 3"),
 ("Amplitude Energy","Amazon Athena Setup: 13 Steps, Query S3 at $5/TB"),
 ("Central Petroleum","Amadeus IT stock gains 0.58 percent ahead of the open"),
 ("ADZ Energy","Kincora Copper Drilling Update Identifies Province-Scale Target"),
 ("Elixir Energy","Westword's in-office concert with Velvet Daydream"),
 ("Strike Energy","Iraq, Pakistan Strike Energy Deals With Iran as Tehran Fumes"),
]
KEEP = [
 # the AND-ed gate must not cost real corporate news
 ("Woodside Energy","Woodside considering pre-emption of BP's Browse deal"),
 ("Woodside Energy","Woodside Energy sanctions Scarborough expansion","gas project offshore"),
 ("Senex Energy","Senex lifts Atlas output as Surat gas demand climbs"),
 ("Senex Energy","Senex Energy appoints new chief executive","Roma North Surat"),
 ("Mineral Resources","Lockyer Deep flow test exceeds expectations"),
 ("Gas M&A and farm-ins","Omega Oil & Gas-led joint venture awarded Queensland acreage"),
 ("Gas M&A and farm-ins","Comet Ridge farm-in lifts Mahalo gas stake","Queensland petroleum"),
 ("Drilling and services capacity","Rig availability tightens for Queensland onshore gas drillers"),
 ("LNG import terminals","Port Kembla FSRU secures first LNG import cargo","Australia east coast gas"),
 ("Domestic gas policy","ACCC flags ADGSM trigger as east coast gas shortfall looms"),
 ("Gas prices and benchmarks","Wallumbilla gas netback price falls as Asian LNG demand eases"),
 ("Energy policy and politics","NT Energy Minister takes gas message to national forum"),
 ("LNG exports and spot cargoes","Australian LNG export revenue up in August"),
 ("Santos","Santos flags Barossa LNG first gas delay"),
 ("State Gas","State Gas Limited reports Reid's Dome flow rates"),
 ("Blue Energy","What Does Blue Energy's (ASX:BLU) Governance Update Show"),
 ("Beach Energy","Are Waitsia Cargoes Reshaping Beach Energy (ASX:BPT)?"),
 ("Tamboran Resources","Formentera, Tamboran tie in five Beetaloo wells as gas sales begin"),
 ("Emissions policy","Safeguard Mechanism review to tighten limits on Australian gas producers"),
]
fails=0
print("SHOULD DROP")
for r in DROP:
    got = mm._relevant(it(r[1]), ents[r[0]]); fails += got
    print(("FAIL kept    " if got else "ok   dropped ") + f"[{r[0][:24]:<24}] {r[1][:50]}")
print("\nSHOULD KEEP")
for r in KEEP:
    got = mm._relevant(it(r[1], r[2] if len(r)>2 else ""), ents[r[0]]); fails += (not got)
    print(("ok   kept    " if got else "FAIL dropped ") + f"[{r[0][:24]:<24}] {r[1][:50]}")
n=len(DROP)+len(KEEP); print(f"\n{n-fails}/{n} passed")
sys.exit(1 if fails else 0)
