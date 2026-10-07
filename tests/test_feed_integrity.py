"""
test_feed_integrity.py
----------------------
Two faults that both failed silently, found when the Domestic Gas Update topic
showed the same Egypt story for weeks:

  1. Every link on the News and My Topics tabs was invented. The prompt asked
     for "the URL from the item", but the text handed to the model contained
     only title, source and summary - the real URL was captured by
     fetch_feed_items and then dropped before the prompt was built. With no URL
     to copy, the model emitted the publication's home page. _clean_stories
     passed that straight through to the page.

  2. naturalgasworld.com/rss stopped publishing in February 2025 and kept
     serving the same 30 items. It is valid XML, so it parsed without error and
     nothing looked at the dates - fetch_feed_items read each item's published
     field into the dict and no line anywhere read it again.

    py tests/test_feed_integrity.py
"""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import briefing as b

SRC = (Path(__file__).resolve().parent.parent / "briefing.py").read_text(encoding="utf-8")

ITEMS = [
    {"title": "Santos lifts Barossa guidance", "summary": "x", "source": "AFR",
     "link": "https://afr.com/santos-barossa", "published": "", "age_days": None},
    {"title": "ACCC flags east coast shortfall", "summary": "y", "source": "ABC",
     "link": "https://abc.net.au/accc-gas", "published": "", "age_days": 1},
]

# what the model returns now: an index, never a URL
good = b._clean_stories([{"headline": "ACCC flags shortfall", "summary": "s",
                          "source": "ABC", "index": 2,
                          "significance": "major"}], ITEMS)
# a model that invents a URL anyway must not get it onto the page
sneaky = b._clean_stories([{"headline": "H", "summary": "s", "source": "ABC",
                            "index": 1, "link": "https://invented.example/made-up",
                            "significance": "notable"}], ITEMS)
# an index that does not resolve degrades to no link, not a wrong one
bad_idx = b._clean_stories([{"headline": "H", "summary": "s", "source": "X",
                             "index": 99, "significance": "notable"}], ITEMS)
missing = b._clean_stories([{"headline": "H", "summary": "s", "source": "X",
                             "significance": "notable"}], ITEMS)
no_items = b._clean_stories([{"headline": "H", "summary": "s", "source": "X",
                              "index": 1, "significance": "notable"}])

def age(days):
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")

CHECKS = [
    # links
    ("the index resolves to the item's real URL",
     good and good[0]["link"] == "https://abc.net.au/accc-gas"),
    ("a URL invented by the model is discarded",
     sneaky and sneaky[0]["link"] == "https://afr.com/santos-barossa"),
    ("an out-of-range index gives no link rather than a wrong one",
     bad_idx and bad_idx[0]["link"] == ""),
    ("a missing index gives no link", missing and missing[0]["link"] == ""),
    ("no items means no link, not a crash", no_items and no_items[0]["link"] == ""),
    ("the prompts ask for an index, not a URL",
     SRC.count('"index": the [n] number') + SRC.count('"index":        the [n] number') == 2
     and '"link": the URL if available' not in SRC
     and '"link":         URL from the item' not in SRC),
    ("both summarisers pass the items in",
     SRC.count("_clean_stories(json.loads(raw), items[:30])") == 2),

    # staleness
    ("an RFC-822 date parses", b._item_age_days(age(5)) == 5),
    ("an ISO date parses", b._item_age_days(
        (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(days=3)).isoformat()) == 3),
    ("an unparseable date is None, not zero", b._item_age_days("not a date") is None),
    ("an empty date is None", b._item_age_days("") is None),
    ("the cutoff is three weeks", b.MAX_ITEM_AGE_DAYS == 21),
    ("a 19-month-old item is past the cutoff", b._item_age_days(age(580)) > b.MAX_ITEM_AGE_DAYS),
    ("a two-day-old item is not", b._item_age_days(age(2)) <= b.MAX_ITEM_AGE_DAYS),
    ("undated items are kept - plenty of feeds omit the date",
     "i[\"age_days\"] is None or" in SRC),
    ("a wholly stale feed is named in the log", "STALE FEED" in SRC),
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
