"""
test_layout_chrome.py
---------------------
Page furniture that is easy to break silently by editing CSS:

  * the masthead, widget bar and weather bar ride together in one frozen
    block, so the tabs and the day's numbers stay reachable at any scroll depth
  * the sticky headers INSIDE the tabs offset against that block's measured
    height, or they stick underneath it and disappear
  * --font-display is the masthead's newspaper face and nothing else. Using it
    on tab content is what made News and My Topics read as a different
    document from the rebuilt tabs.
  * the Schedule tab's four columns are even

    py tests/test_layout_chrome.py
"""
import sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIEF = (ROOT / "briefing.py").read_text(encoding="utf-8")
SCHED = (ROOT / "schedule_render.py").read_text(encoding="utf-8")
MARKET = (ROOT / "market_monitor.py").read_text(encoding="utf-8")

head_open  = BRIEF.index('<div class="page-head"')
head_close = BRIEF.index("</div><!-- /page-head -->")
inside     = BRIEF[head_open:head_close]

# every rule that sets font-display, minus the declaration of the token itself
display_users = [ln.strip() for ln in BRIEF.splitlines()
                 if "var(--font-display)" in ln]

CHECKS = [
    ("the frozen block wraps the masthead", "<header class=\"masthead\">" in inside),
    ("the frozen block wraps the widget bar", 'id="widget-bar"' in inside),
    ("the frozen block wraps the weather bar", "{weather_html}" in inside),
    ("nothing of a tab view is inside it", "view-news" not in inside),
    ("the block is sticky", re.search(r"\.page-head \{\{\s*\n?\s*position: sticky", BRIEF) is not None),
    ("it unsticks on small screens",
     re.search(r"@media \(max-width: 900px\), \(max-height: 620px\)", BRIEF) is not None),
    ("the remaining in-tab sticky header offsets against it",
     BRIEF.count("position: sticky; top: var(--head-h)") == 1
     and "position: sticky; top: 0; z-index: 10" not in BRIEF),
    ("the offset is measured, not hard-coded",
     "function measureHead()" in BRIEF and "--head-h" in BRIEF),
    ("it is re-measured on resize and tab switch",
     "addEventListener('resize', measureHead)" in BRIEF
     and BRIEF.count("measureHead();") >= 1),
    ("anchors do not land under the frozen block",
     "scroll-padding-top: var(--head-h)" in BRIEF),
    ("the display face is used by the masthead only",
     all("masthead" in ln for ln in display_users)),
    ("one page-width cap drives every tab",
     "--page-max: 1800px" in BRIEF and "var(--page-max" in SCHED
     and "var(--page-max" in MARKET and "1440px" not in BRIEF),
    ("the Schedule tab's columns are even",
     re.search(r"\.sx-main, \.sx-side \{ flex:1 1 0", SCHED) is not None),
    ("no fixed-width day column remains",
     "flex:0 0 250px" not in SCHED and "flex:0 0 300px" not in SCHED
     and "flex:0 0 330px" not in SCHED),

    # Before your meetings must never vanish - an absent column is
    # indistinguishable from the feature being broken.
    ("the meeting-prep column always renders",
     "brief_col = (" in SCHED and 'brief_col = ""' not in SCHED),
    ("it explains an empty state", "sx-brief-none" in SCHED
     and "No work meetings in today's diary" in SCHED),
    ("it still covers today only",
     "brief_nxt" not in SCHED and "sx-brief-day" not in SCHED),

    # News, My Topics, Market Watch and Actions share one column format.
    ("news columns use the Actions container",
     BRIEF.count("max-width: var(--page-max); margin: 0 auto; padding: 1.5rem 1.5rem 3rem;") >= 2),
    ("the hairline newspaper grid is gone",
     "gap: 1px; background: var(--rule);" not in BRIEF
     and "gap: 1px;\n            background: var(--rule);" not in BRIEF),
    ("story cards are discrete, like .ep-card",
     "border: 1px solid var(--rule); border-radius: 3px; padding: 0.85rem 1rem;" in BRIEF),
    ("column headers match the panel title rule",
     "padding-bottom: 0.5rem; margin-bottom: 1rem;" in BRIEF),
    ("market watch shares the container and card treatment",
     "margin:0 auto;padding:1.5rem 1.5rem 3rem" in MARKET
     and "padding:0.85rem 1rem;transition:box-shadow .12s" in MARKET),
    ("market watch uses the body face",
     "var(--font-display" not in MARKET),
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
