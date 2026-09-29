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
    ("in-tab sticky headers offset against it",
     BRIEF.count("position: sticky; top: var(--head-h)") == 2
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
    ("the Schedule tab's columns are even",
     re.search(r"\.sx-main, \.sx-side \{ flex:1 1 0", SCHED) is not None),
    ("no fixed-width day column remains",
     "flex:0 0 250px" not in SCHED and "flex:0 0 300px" not in SCHED
     and "flex:0 0 330px" not in SCHED),
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
