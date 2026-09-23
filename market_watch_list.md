# Market Watch — monitoring list

Companies and topics for the Market Watch tab of the morning briefing.
Verified against the ASX company register on **23 September 2026**.

Edit this file freely. When you're happy with it, the rows map one-to-one onto the
settings dashboard (`py settings_server.py` → Market Watch), or I can load it
straight into `briefing_settings.json` for you.

---

## How the columns work

| Column | What it does |
|---|---|
| **Aliases** | What catches the story. Legal name, the short name the press uses, project and JV names, former names. Comma-separated. |
| **Must also mention** | Only needed when the name is ordinary English or belongs to something famous elsewhere. At least **one** listed term must also appear, or the item is dropped. Listing the unambiguous form of the name here means a clean headline passes on its own. Leave empty for distinctive names. |
| **Never mention** | Kills the item outright. For known false friends. |

The two qualifier columns are the difference between a usable page and one full of
Ohio state gas regulators. They're left empty below wherever the name is distinctive
enough not to need them — that's deliberate, not an oversight.

---

## Tier 1 — Direct peers

Queensland CSG and conventional explorers/developers at a similar stage. These are
the ones where a farm-in, a reserve upgrade or a gas sales agreement changes how your
own asset is valued.

| Company | Code | Aliases | Must also mention | Never mention |
|---|---|---|---|---|
| State Gas | GAS | State Gas Limited, Rolleston West, Reid's Dome, Nyanda | State Gas Limited, Rolleston, Reid's Dome, Queensland, Australian, ASX, GAS.AX | Ohio, Texas, Pennsylvania, state gas tax |
| Comet Ridge | COI | Comet Ridge Limited, Mahalo, Mahalo North, Mahalo East | | |
| Blue Energy | BLU | Blue Energy Limited, Sapphire, Lancewood, Bowen Basin | Blue Energy Limited, Bowen Basin, Sapphire, Lancewood, Queensland, ASX, Australian | |
| Omega Oil & Gas | OMA | Omega Oil and Gas, Taroom Trough, Canyon, Canyon-2 | | |
| Elixir Energy | EXR | Elixir Energy Limited, Grandis, Daydream, Daydream-2 | Elixir Energy, Grandis, Daydream, Queensland, Bowen, ASX | Mongolia |
| Galilee Energy | GLL | Galilee Energy Limited, Glenaras, Galilee Basin | | |
| Denison Gas | *unlisted* | Denison Gas, Denison Trough, Baffle Creek | | |
| ADZ Energy | *unlisted* | ADZ Energy, Kincora, Myall Creek | | |

**On ADZ Energy** — this is where Armour Energy's operating assets ended up after
Armour went into liquidation. If you were monitoring Armour, ADZ is the name that
now matters. Armour Energy itself is gone: receivers in 2023, liquidators appointed,
delisted.

**On Denison Gas** — active and expanding in the Denison Trough (14 fields, 10
production leases, ~315 km of pipeline, plus the Baffle Creek tight gas discovery).
Privately held; I could not establish who owns it. Worth knowing before you read a
story about them.

---

## Tier 2 — East coast producers and mid-caps

Larger, but operating in the same basins and bidding for the same acreage, rigs and
customers.

| Company | Code | Aliases | Must also mention | Never mention |
|---|---|---|---|---|
| Santos | STO | Santos Limited, GLNG, Barossa, Cooper Basin, Narrabri | Santos Limited, GLNG, Barossa, Cooper Basin, LNG, ASX, Australia, Australian, Darwin, Papua | Santos FC, football, Neymar, Sao Paulo, Brazil, Palmeiras |
| Beach Energy | BPT | Beach Energy Limited, Waitsia, Otway, Bass Basin | | |
| Amplitude Energy | AEL | Amplitude Energy Limited, Cooper Energy, Sole, Athena, Orbost | | |
| Origin Energy | ORG | Origin Energy Limited, APLNG, Australia Pacific LNG | | |
| Senex Energy | *unlisted* | Senex, Senex Energy, Atlas, Roma North | | |
| Arrow Energy | *unlisted* | Arrow Energy, Surat Gas Project, Daandine, Tipton | | |
| Shell QGC | *unlisted* | QGC, Queensland Gas Company, QCLNG, Curtis Island | QGC, Queensland, Surat, Curtis Island, LNG, Australia, Australian | |
| Australia Pacific LNG | *unlisted* | APLNG, Australia Pacific LNG, Walloons | | |
| Bridgeport Energy | *unlisted* | Bridgeport Energy, New Hope Group | Australia, Australian, Cooper Basin, Queensland, Surat, New Hope | Connecticut, power plant |
| Vintage Energy | VEN | Vintage Energy Ltd, Vali, Odin, Nangwarry | | |
| Central Petroleum | CTP | Central Petroleum Limited, Mereenie, Palm Valley, Amadeus | | |

**On Senex** — no longer listed. Taken private in 2022 by POSCO International (50.1%)
and Hancock Energy (49.9%). The name is still very much active in the press as a
Surat Basin producer, so it stays on the list; there's just no ASX feed for it, and
`SXY` is dead as a code.

**On Bridgeport** — the exclude terms matter here. There's an unrelated "Bridgeport
Energy Project" power station in Connecticut that dominates search results.

---

## Tier 3 — Majors and LNG exporters

Less about competition, more about the market signals that move your netback.

| Company | Code | Aliases | Must also mention | Never mention |
|---|---|---|---|---|
| Woodside Energy | WDS | Woodside Energy Group, Scarborough, North West Shelf, Pluto | | |
| Strike Energy | STX | Strike Energy Limited, West Erregulla, South Erregulla, Walyering | | |
| Tamboran Resources | TBN | Tamboran Resources, Beetaloo, Shenandoah South, Sturt Plateau | | |
| Beetaloo Energy Australia | BTL | Beetaloo Energy, Empire Energy, Carpentaria, Beetaloo Sub-basin | | |
| Mineral Resources | MIN | Mineral Resources Limited, Lockyer Deep, Norwest, Perth Basin gas | Lockyer Deep, Perth Basin, gas, Norwest, Erregulla | lithium, iron ore |

**On the Beetaloo** — worth carrying even though it's the Northern Territory. If
Beetaloo gas reaches the east coast at scale it changes the supply picture you're
selling into. Note **Empire Energy renamed to Beetaloo Energy Australia and moved
from EEG to BTL in June 2025** — `EEG` is dead.

**On Mineral Resources** — mostly a miner. The exclude terms keep the lithium and
iron ore noise out, so you only see the Perth Basin gas stories. If it's still too
noisy, drop it; it's the most marginal inclusion here.

---

## Tier 4 — Infrastructure, midstream and buyers

Pipeline capacity, tariffs and who's contracting are often the real story behind a
gas sales negotiation.

| Company | Code | Aliases | Must also mention | Never mention |
|---|---|---|---|---|
| APA Group | APA | APA Group, South West Queensland Pipeline, SWQP, Wallumbilla, Moomba Sydney Pipeline | APA Group, pipeline, gas, Wallumbilla, Queensland, Australia | |
| Jemena | *unlisted* | Jemena, SGSP, Queensland Gas Pipeline, Northern Gas Pipeline, Eastern Gas Pipeline | | |
| Squadron Energy | *unlisted* | Squadron Energy, Port Kembla Energy Terminal, Tattarang | | |
| AGL Energy | AGL | AGL Energy Limited | AGL Energy, gas, contract, supply, Australia | |
| CleanCo / Stanwell / CS Energy | *unlisted* | CleanCo, Stanwell Corporation, CS Energy, Kogan Creek | Queensland, gas, generation, contract | |
| Incitec Pivot | IPL | Incitec Pivot, Gibson Island, Phosphate Hill | gas, supply, contract, Queensland, ammonia | |
| Orica | ORI | Orica Limited, Yarwun | gas, supply, contract, ammonium nitrate | |

The Queensland government generators (CleanCo, Stanwell, CS Energy) are grouped into
one row because you almost always care about the same thing: who is contracting gas
for firming. Split them if you'd rather have three cards.

Large industrial gas users are on the list because their contracting decisions are
the demand side of your market. Gibson Island in particular has been a recurring
signal on east coast gas availability.

---

## Topics

Companies tell you what happened. Topics tell you what's about to.

| Topic | Keywords | Must also mention | Never mention |
|---|---|---|---|
| East coast gas supply | east coast gas, gas shortfall, supply adequacy, GSOO, gas market review | Australia, Australian, ACCC, AEMO, east coast | |
| Domestic gas policy | ADGSM, domestic gas security mechanism, gas market code, mandatory code of conduct, price cap, gas reservation | Australia, Australian, Commonwealth, federal | |
| Queensland tenure and acreage | ATP tender, acreage release, petroleum lease, PL grant, domestic gas condition, relinquishment | Queensland, Qld | |
| Queensland royalties and state budget | petroleum royalty, royalty rate, Queensland budget, resources royalty | Queensland, Qld, gas, petroleum | |
| CSG water and environment | associated water, UWIR, OGIA, make good, EPBC, water trigger, environmental authority | Queensland, coal seam gas, CSG, Australia | |
| Pipeline capacity and tariffs | pipeline capacity, capacity trading, access arrangement, pipeline tariff, SWQP, compression | Australia, Australian, AER, gas | |
| Gas prices and benchmarks | Wallumbilla, Gas Supply Hub, netback, gas netback price, JKM, short term trading market | Australia, Australian, gas | |
| LNG exports and spot cargoes | spot cargo, LNG export, uncontracted LNG, Curtis Island, Gladstone LNG | Australia, Australian, Queensland | |
| Gas M&A and farm-ins | farm-in, farmout, joint venture, acquisition, scheme of arrangement, takeover | gas, petroleum, oil, basin, ASX, Australia | |
| Drilling and services capacity | rig availability, drilling rig, well services, completion costs, rig contract | Australia, Australian, Queensland, gas, onshore | |
| Small-cap energy capital markets | capital raising, placement, entitlement offer, rights issue, equity raising | ASX, energy, gas, oil, explorer, junior | |
| Emissions policy | Safeguard Mechanism, fugitive emissions, methane intensity, MMRV, ACCU, carbon credits | Australia, Australian, gas, petroleum | |
| Land access and native title | conduct and compensation, cultural heritage, native title, land access, ILUA | Queensland, gas, petroleum, resources | |
| Gas-fired generation | peaking plant, gas peaker, firming capacity, gas generation, capacity investment scheme | Australia, Australian, NEM, Queensland | |
| LNG import terminals | LNG import terminal, FSRU, Port Kembla, regasification | Australia, Australian, east coast | |
| Energy policy and politics | energy policy, gas strategy, future gas strategy, energy minister | Australia, Australian, Queensland, gas | |

Sixteen topics is more than you want on day one. My suggestion: start with the first
six, run it for a week, and add the rest once you can see what each one actually
produces. It's easier to judge a topic by its output than in the abstract.

---

## Do not add these — stale codes

Seven codes that look right and aren't. Each would have failed silently: the
announcement fetch returns nothing and the card just sits there quiet, which looks
identical to a quiet news day.

| Code | Why not |
|---|---|
| SXY | Senex. Delisted 2022, taken private by POSCO International / Hancock. |
| COE | Cooper Energy. Renamed Amplitude Energy, now **AEL**. |
| EEG | Empire Energy. Renamed Beetaloo Energy Australia, now **BTL**, June 2025. |
| WGO | Warrego Energy. Taken over by Hancock Energy in 2023. |
| NWE | Norwest Energy. Compulsorily acquired by Mineral Resources, June 2023 — the exposure is inside MIN now. |
| RLE | Real Energy. Delisted. |
| ICN | Icon Energy. Removed from the ASX Official List in 2025 after a two-year suspension. |
| AJQ | Armour Energy. In liquidation, delisted. Watch **ADZ Energy** instead. |

Also considered and left off as having no Australian east coast gas exposure:
**KAR** (Karoon — Brazil and US Gulf), **HZN** (Horizon — PNG, China, Thailand),
**88E** (Alaska, Namibia), **ADX** (Austria, Italy), **GGE** (US helium),
**CVN** (Carnarvon Basin, WA), **BRU** (Canning Basin, WA), **LKO** (Lakes Blue
Energy — Gippsland, arguably worth adding if Victorian onshore matters to you).

---

## Notes on exactness

If you add any of these by hand, the registered names have quirks that matter for
exact matching:

- `BLUE ENERGY LIMITED.` and `AGL ENERGY LIMITED.` — trailing full stop in the ASX record
- `WOODSIDE ENERGY GROUP LTD` and `VINTAGE ENERGY LTD` — "LTD", not "LIMITED"
- `LAKES BLUE ENERGY NL` — an NL
- `APA GROUP` — no company suffix at all, it's a stapled entity
- `TAMBORAN RESOURCES CORPORATION` — a US corporation listed via CDIs

The alias matcher is whole-phrase and case-insensitive, so these only matter if you
want an exact registered name as one of the aliases.

---

## Things I could not verify

Stated plainly so you don't inherit them as fact:

- **Denison Gas ownership** — the company is clearly active, but I could not find who owns it.
- **APLNG shareholdings** — Origin / ConocoPhillips / Sinopec percentages have moved through the ConocoPhillips operatorship change and the EIG transaction. Re-confirm before relying on a specific split.
- **Jemena / SGSP ownership** — the State Grid 60% / Singapore Power 40% structure dates from 2014 and I found no 2026 confirmation.
- **Squadron Energy's Port Kembla terminal** — the company and ownership are solid; whether the terminal is operating as at September 2026 is not confirmed.
- **Real Energy** — confirmed not listed, but whether it survives as a private entity is unknown.

---

## Suggested starting point

If you want the shortest list that still earns its place, this is where I'd begin:

**Companies (11):** State Gas, Comet Ridge, Blue Energy, Omega Oil & Gas, Elixir
Energy, Denison Gas, Senex Energy, Arrow Energy, Shell QGC, Santos, APA Group

**Topics (6):** East coast gas supply, Domestic gas policy, Queensland tenure and
acreage, CSG water and environment, Gas prices and benchmarks, Gas M&A and farm-ins

That's 17 entities. At roughly one search each plus the announcement fetch, it adds
well under a minute to the run.

---

# Feed sources

Checked on **23 September 2026**. Every URL below was fetched and parsed, not
assumed — a feed that 404s or has silently gone stale looks identical to a quiet
news day, which is the failure this tab exists to avoid.

## Added to `topic_search_feeds`

| Source | URL | Verified |
|---|---|---|
| WattClarity | `https://wattclarity.com.au/feed/` | 20 items, newest 22 Sep 2026 |
| Small Caps | `https://smallcaps.com.au/feed/` | 15 items, newest 22 Sep 2026 |
| LNG Prime | `https://lngprime.com/feed/` | 10 items, newest 22 Sep 2026 |
| Offshore Energy | `https://www.offshore-energy.biz/feed/` | 10 items, newest 21 Sep 2026 |
| Energy Voice | `https://www.energyvoice.com/feed/` | 16 items, newest 22 Sep 2026 |
| Rigzone | `https://www.rigzone.com/news/rss/rigzone_latest.aspx` | 20 items, newest 21 Sep 2026 |
| MINING.COM | `https://www.mining.com/feed/` | 15 items, newest 22 Sep 2026 |

**WattClarity is the pick of these.** Australian, free, no paywall, and it publishes
close analysis of the NEM and east coast gas market — generation dispatch, gas-powered
generation demand, market events — which is the demand-side context you don't
currently get anywhere in the briefing. Paul McArdle's outage and market-event posts
are the sort of thing that moves gas demand and rarely makes mainstream press.

**Small Caps** covers ASX juniors, which is the peer group and the capital-raising
environment you operate in.

## Rejected, with reasons

| Source | Why not |
|---|---|
| **Australian Mining** | Feed responds and parses — but the newest item is dated **January 2017**. Abandoned, still serving. This is exactly the failure mode worth guarding against: it would have sat in the pool contributing nothing, forever, with no error. |
| Energy News Bulletin | `/rss` returns 404. It's the natural Australian oil and gas trade title, so worth finding the real feed URL if you have a subscription. |
| Australian Energy Producers (ex-APPEA) | `/feed/` returns 404. The industry body's media releases would be valuable — may need scraping rather than RSS. |
| Queensland Ministerial Media Statements | `/rss` returns 404. Would have been the best single source for tenure, acreage and royalty announcements. |
| ACCC media releases | No working RSS found. |
| Boiling Cold | `/feed/` returns 404. |
| Natural Gas Intelligence | Returns 405 — blocks automated fetching. |
| World Oil | `/rss/` is an index of feeds, not a feed. A specific topic feed would work; I didn't pick one for you. |
| gasworld | Returns 403 to automated requests. |
| The Australian Pipeliner, Energy Today | Blocked by robots — could not verify. These *may* work from your machine; they refused this check, not necessarily the briefing. |

## The gap this leaves

No Australian regulator or government feed survived verification — AEMO, the ACCC,
the AER and the Queensland Government either don't publish RSS or don't publish it
at a discoverable URL. That matters, because for a Queensland explorer the ACCC gas
inquiry reports, AEMO's GSOO and Queensland acreage announcements are more
consequential than most journalism.

The workaround is already built. Those bodies are covered through the **Market Watch
topic rows**, which use per-entity news search rather than subscribed feeds — so
"Domestic gas policy" with `ADGSM, gas market code, price cap` and "Queensland
tenure and acreage" with `ATP tender, acreage release` will pick up the coverage of
those announcements even though there's no feed to subscribe to. That's the layer
doing the work here, not the feed pool.

If you want the announcements themselves rather than coverage of them, the honest
answer is that it needs a small scraper per body — a different job from this one, and
worth doing only for the two or three bodies you actually care about.

## A caveat on all of the above

These were verified over a different network path than the briefing uses. A feed that
answered here should answer from your machine and from GitHub Actions, but that isn't
guaranteed. After the next run, check the console output for `Failed to fetch` lines
against any of the seven new URLs.
