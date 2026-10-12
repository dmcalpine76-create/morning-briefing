"""
core/config.py — single source of truth for paths, company identity,
and the State Gas strategic pillar definitions.

Everything that used to be copy-pasted across four scripts lives here.
"""

from pathlib import Path

# Package root — the folder containing inbox_actions.py / board_update.py etc.
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Company identity ─────────────────────────────────────────────────────────
COMPANY_NAME   = "State Gas Limited"
COMPANY_TICKER = "ASX: GAS"
MD_NAME        = "Doug McAlpine"
MD_TITLE       = "Managing Director"

# ── Memory / state files ─────────────────────────────────────────────────────
# company_knowledge.json replaces BOTH shared_knowledge.json and
# board_context.json (run migrate_knowledge.py once to merge them).
COMPANY_KNOWLEDGE_FILE = BASE_DIR / "company_knowledge.json"
INBOX_STATE_FILE       = BASE_DIR / "inbox_state.json"
PILLAR_HISTORY_FILE    = BASE_DIR / "pillar_history.json"
CONTEXT_BRIEFING_FILE  = BASE_DIR / "context_briefing.txt"

# ── Scheduling ───────────────────────────────────────────────────────────────
# The single Microsoft To Do list that is the scheduler's work queue.
# inbox_actions.py pushes approved actions INTO this list; outlook_scheduler.py
# reads tasks OUT of it. Defined here rather than in scheduling_rules.json
# because the rules dashboard rewrites global.* wholesale on save and would
# silently drop an extra key.
TASK_LIST_NAME = "Daily Priorities"

SCHEDULING_RULES_FILE = BASE_DIR / "scheduling_rules.json"
SCHEDULING_PLAN_FILE  = BASE_DIR / "scheduling_plan.json"
DURATION_HISTORY_FILE = BASE_DIR / ".duration_history.json"

# Brisbane — no DST, so a fixed offset is safe.
AEST_OFFSET_HOURS = 10

# ── Auth / caches ────────────────────────────────────────────────────────────
TOKEN_CACHE_FILE  = BASE_DIR / ".outlook_token_cache.bin"
FOLDER_CACHE_FILE = BASE_DIR / ".folder_cache.json"
FOLDER_CACHE_TTL_DAYS = 7

# ── Shared writing voice ─────────────────────────────────────────────────────
# Core voice rules shared by board_update (email) and board_briefing (deck).
# Each tool appends its own format-specific instructions.
VOICE_RULES = """WRITING VOICE — apply to every matter you write:
1. FIRST PERSON. Write as Doug — "I met with...", "I have agreed to...", "My view is..."
   Never "The MD" or "Management". This is a personal update from Doug to his board.
2. INCLUDE JUDGMENT AND OPINION. Don't just report facts — express the MD's assessment.
   e.g. "Santos' view, which I share, is that..." The directors want Doug's take.
3. FORWARD FOCUS. Every matter should say what happens next and when. If there's no
   clear next step, note what is being waited on.
4. SHORT AND DIRECT. 2-4 sentences per matter unless genuinely significant.
   Write the way a smart executive emails their board — no lawyerly compound sentences.
5. NO PROCEDURAL DETAIL. Skip execution mechanics, statutory references, and admin
   process unless the process IS the news.
6. CONVERSATIONAL BUT PROFESSIONAL. Collegial register — Doug knows these directors
   personally. Factual and specific. Never "discussions continued" or "matters are
   progressing"."""

# ── State Gas strategic pillars ──────────────────────────────────────────────
# Single definition — previously duplicated (and drifting) between
# board_briefing.py and board_report_analyser.py.
STRATEGIC_PILLARS = [
    {
        "id":          "reserves_production",
        "title":       "2P Reserve & Production Testing",
        "description": (
            "Status of the 30PJ 2P reserve, dewatering and production testing of "
            "Rougemont 5 and 6 (R5/R6), gathering system connection works, HDNG plant "
            "operations and care & maintenance, and any new subsurface or well data."
        ),
        "keywords": [
            "reserve", "2P", "production", "dewatering", "R5", "R6", "Rougemont",
            "HDNG plant", "gathering system", "wells", "PJ", "TJ", "NSAI",
            "compression", "well test",
        ],
    },
    {
        "id":          "litigation",
        "title":       "Wildhorse Litigation",
        "description": (
            "Land Court proceedings MER087-25 and MER279-25 against the McDonald family. "
            "Covers the CCA dispute, produced water/dust suppression incident, expert "
            "witnesses, lay witness statements, discovery, insurance, and hearing dates."
        ),
        "keywords": [
            "McDonald", "Wildhorse", "litigation", "land court", "CCA",
            "ACO", "organic", "dust suppression", "produced water",
            "MER087", "MER279", "H&G", "Gadens", "witness", "discovery",
            "compensation", "injunction",
        ],
    },
    {
        "id":          "access_rights",
        "title":       "Extension of Access Rights",
        "description": (
            "Negotiation of a new Conduct and Compensation Agreement (CCA) with the "
            "McDonalds, compensation payments, entry notices under MERCPA, independent "
            "valuation, and the ONeill landowner arrangement at PL231."
        ),
        "keywords": [
            "CCA", "compensation", "access", "entry notice", "MERCPA",
            "land court", "ONeill", "access rights", "new CCA",
            "compensation statement", "valuer",
        ],
    },
    {
        "id":          "rolleston_west",
        "title":       "Rolleston West Project Development",
        "description": (
            "Pre-feasibility study for the 10TJ/day CSG development, engineering firm "
            "appointment, farm-in advisor selection, Jemena pipeline discussions, NAIF "
            "infrastructure funding, Japanese offtake marketing via TIQ, ATP 2062 tenure, "
            "and PL application timetable."
        ),
        "keywords": [
            "Rolleston West", "RW", "Equinox", "Jemena", "pipeline", "farm-in",
            "farm in", "JV", "NAIF", "Japan", "Mizuho", "PKF", "Ironstone",
            "Standard Chartered", "Rothschild", "ATP 2062", "feasibility",
            "PL application", "TIQ", "concept study", "pre-FEED", "FEED",
        ],
    },
    {
        "id":          "alt_fuels",
        "title":       "Alternative Fuels / HDNG Strategy",
        "description": (
            "HDNG gas sales pipeline, Jellinbah trial, Mitchell Group micro-LNG "
            "discussions, Kala Data bitcoin/AI data centre proposal, PL231 conventional "
            "gas, Comet Ridge competitive dynamics, and Denison relationship."
        ),
        "keywords": [
            "HDNG", "Jellinbah", "Mitchell", "Kala", "bitcoin", "data centre",
            "LNG", "PL231", "Curragh", "Thiess", "fuel cell",
            "alternative fuel", "Comet Ridge", "Denison", "OGT", "micro-LNG",
        ],
    },
    {
        "id":          "capital",
        "title":       "Capital, Overheads & Risk Management",
        "description": (
            "Cashflow management, R&D grant and ATO correspondence, convertible notes "
            "and Ignite Equity, BDO half-year audit/review, ASX compliance, Santos JV "
            "on ATP 2068/2069, investor relations and roadshow activity, Director loans."
        ),
        "keywords": [
            "R&D", "ATO", "refund", "cashflow", "cash flow", "Ignite",
            "convertible note", "BDO", "ASX", "Santos", "ATP 2068", "ATP 2069",
            "investor", "roadshow", "capital", "liquidity", "budget",
            "overhead", "Director loan", "half-year", "audit",
        ],
    },
]

# Short pillar ids used by the PowerPoint data schema (generate_board_deck.js)
# mapped to the canonical long ids above. The deck JSON keeps the short ids
# so the PPTX pipeline is unchanged; pillar_history.json uses the long ids.
PILLAR_ID_MAP = {
    "reserves":   "reserves_production",
    "litigation": "litigation",
    "access":     "access_rights",
    "rw":         "rolleston_west",
    "hdng":       "alt_fuels",
    "capital":    "capital",
}
