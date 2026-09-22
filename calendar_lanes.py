"""
calendar_lanes.py
-----------------
Decides which swimlane a calendar event belongs in.

Rules are deterministic and run in a fixed order, so the same event lands in
the same lane on the 5:17 run and the 8:15 run. Claude is only consulted for
events no rule places, and that happens in the existing
analyse_calendar_events() call, so it costs nothing extra.

Order (first match wins):
  1. override map      — exact subject, then regex. Pin recurring meetings here.
  2. source == personal — never run work rules over personal events.
  3. keywords          — subject and location, word-boundary matched.
                         Runs BEFORE domains on purpose: every attendee at a
                         board meeting is a colleague, so the domain rule would
                         otherwise file it as Internal.
  4. external domains  — organiser and attendee addresses.
  5. shape             — a multi-day all-day event with a location is travel.
  6. all-internal      — everyone is on the company domain.
  7. unfiled           — visible by default so misfiling is obvious, not silent.

Self-test:
    py calendar_lanes.py --test
"""

import re
import json
import datetime
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "briefing_settings.json"

UNFILED = {"id": "unfiled", "name": "Unfiled", "color": "#8c887b"}
PERSONAL_FALLBACK = {"id": "personal", "name": "Personal", "color": "#3d3d38"}

# Short or ambiguous keywords that must match as whole words, never substrings,
# or "whiteboard session" files itself under Board.
_WORD_SAFE = re.compile(r"^[a-z0-9][a-z0-9 '&/-]*$", re.I)


def load_lane_config() -> dict:
    if not SETTINGS_FILE.exists():
        return {"lanes": [], "overrides": {}, "unfiled_lane_visible": True}
    try:
        s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  WARNING: could not read calendar_lanes settings: {e}")
        return {"lanes": [], "overrides": {}, "unfiled_lane_visible": True}
    cfg = s.get("calendar_lanes", {}) or {}
    cfg.setdefault("lanes", [])
    cfg.setdefault("overrides", {})
    cfg.setdefault("unfiled_lane_visible", True)
    return cfg


def _lane_by_id(cfg: dict, lane_id: str):
    for lane in cfg.get("lanes", []):
        if lane.get("id") == lane_id:
            return lane
    return None


def _keyword_hit(text: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw:
        return False
    if _WORD_SAFE.match(kw):
        # whole-word / whole-phrase match
        return re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", text) is not None
    return kw in text


def _addresses(ev: dict) -> list:
    """Every email address we can see on the event, lowercased."""
    out = []
    org = ev.get("organizer_email") or ""
    if org:
        out.append(org.lower())
    for a in ev.get("attendee_emails") or []:
        if a:
            out.append(a.lower())
    # organizer may arrive as a display name with an address embedded
    blob = f"{ev.get('organizer','')} {ev.get('body_preview','')}"
    out += [m.lower() for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", blob)]
    return out


def _domain_of(addr: str) -> str:
    return addr.rpartition("@")[2]


def classify_event(ev: dict, cfg: dict = None, internal_domain: str = "stategas.com") -> dict:
    """
    Returns {"id","name","color","reason"} — reason names the rule that fired,
    which is what the Unfiled review list and any debugging rely on.
    """
    cfg = cfg if cfg is not None else load_lane_config()
    subject = (ev.get("subject") or "").strip()
    haystack = f"{subject} {ev.get('location','')}".lower()

    # 1. overrides
    overrides = cfg.get("overrides") or {}
    for pattern, lane_id in overrides.items():
        if pattern.lower() == subject.lower():
            lane = _lane_by_id(cfg, lane_id)
            if lane:
                return {**lane, "reason": "override (exact)"}
    for pattern, lane_id in overrides.items():
        try:
            if re.search(pattern, subject, re.I):
                lane = _lane_by_id(cfg, lane_id)
                if lane:
                    return {**lane, "reason": f"override (regex {pattern})"}
        except re.error:
            continue

    # 2. personal source never meets the work rules
    if ev.get("source") == "personal":
        lane = _lane_by_id(cfg, "personal") or PERSONAL_FALLBACK
        return {**lane, "reason": "source is the personal calendar"}

    # 3. keywords, in lane order
    for lane in cfg.get("lanes", []):
        for kw in lane.get("keywords", []) or []:
            if _keyword_hit(haystack, kw):
                return {**lane, "reason": f"keyword '{kw}'"}

    # 4. external domains
    addrs = _addresses(ev)
    externals = [a for a in addrs if _domain_of(a) and _domain_of(a) != internal_domain]
    for lane in cfg.get("lanes", []):
        for dom in lane.get("domains", []) or []:
            dom = dom.lower().strip()
            if not dom or dom == internal_domain:
                continue
            if any(_domain_of(a) == dom or _domain_of(a).endswith("." + dom) for a in addrs):
                return {**lane, "reason": f"domain {dom}"}

    # 5. shape — a multi-day all-day event with a place is travel
    if ev.get("is_all_day"):
        start, end = ev.get("start_dt"), ev.get("end_dt")
        if start and end and (end.date() - start.date()).days >= 1 and ev.get("location"):
            lane = _lane_by_id(cfg, "travel")
            if lane:
                return {**lane, "reason": "multi-day all-day event with a location"}

    # 6. everyone internal
    if addrs and not externals:
        lane = _lane_by_id(cfg, "internal")
        if lane:
            return {**lane, "reason": f"all attendees on {internal_domain}"}

    # 7. give up loudly rather than quietly
    return {**UNFILED, "reason": "no rule matched"}


def classify_all(events: list, cfg: dict = None) -> list:
    cfg = cfg if cfg is not None else load_lane_config()
    for ev in events:
        lane = classify_event(ev, cfg)
        ev["lane"] = lane["name"]
        ev["lane_id"] = lane["id"]
        ev["lane_color"] = lane.get("color", "#8c887b")
        ev["lane_reason"] = lane["reason"]
    return events


def unfiled(events: list) -> list:
    return [e for e in events if e.get("lane_id") == "unfiled"]


# ── self-test ────────────────────────────────────────────────────────────────

def _self_test():
    cfg = load_lane_config()
    print("Lane classifier self-test")
    print("=" * 72)
    print(f"lanes configured : {[l['id'] for l in cfg.get('lanes', [])]}")
    print(f"overrides        : {len(cfg.get('overrides') or {})}")
    print()

    AEST = datetime.timezone(datetime.timedelta(hours=10))
    d = lambda day, h=9: datetime.datetime(2026, 9, day, h, 0, tzinfo=AEST)

    cases = [
        # (expected_lane_id, event)
        ("board",      {"subject": "Board paper review — Q3",
                        "organizer_email": "aaron@stategas.com",
                        "attendee_emails": ["doug@stategas.com"]}),
        ("internal",   {"subject": "Whiteboard session on well design",
                        "organizer_email": "eng@stategas.com",
                        "attendee_emails": ["doug@stategas.com"]}),
        ("investors",  {"subject": "Broker briefing — September",
                        "organizer_email": "research@somebroker.com.au"}),
        ("regulatory", {"subject": "Reid's Dome tenure renewal lodgement",
                        "organizer_email": "doug@stategas.com"}),
        ("regulatory", {"subject": "Catch-up about PL 231",
                        "organizer_email": "officer@resources.qld.gov.au"}),
        ("internal",   {"subject": "Ops stand-up",
                        "organizer_email": "doug@stategas.com",
                        "attendee_emails": ["team@stategas.com"]}),
        ("travel",     {"subject": "Away", "location": "Sydney", "is_all_day": True,
                        "start_dt": d(1), "end_dt": d(3)}),
        ("personal",   {"subject": "Dentist", "source": "personal"}),
        ("unfiled",    {"subject": "Catch-up", "organizer_email": "j.hartley@gmail.com"}),
        ("internal",   {"subject": "Quick sync",
                        "organizer_email": "doug@stategas.com"}),
    ]

    width = max(len(c[1]["subject"]) for c in cases) + 2
    passed = 0
    for expected, ev in cases:
        got = classify_event(ev, cfg)
        ok = got["id"] == expected
        passed += ok
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {ev['subject']:<{width}} -> {got['id']:<11} ({got['reason']})")
        if not ok:
            print(f"     expected {expected}")

    print()
    print(f"{passed}/{len(cases)} passed")
    print()
    print("The 'Whiteboard session' case is the one that matters: it proves short")
    print("keywords match as whole words, so 'board' does not swallow it.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
