"""
settings_server.py  —  Briefing Settings Dashboard Server
----------------------------------------------------------
Serves the settings dashboard on localhost and handles save/load
requests for briefing_settings.json and topics.json.

Usage:
    py settings_server.py

Opens http://localhost:8766 in your browser automatically.
Press Ctrl+C when done.
"""

import json
import webbrowser
import threading
import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

HERE             = Path(__file__).parent
SETTINGS_FILE    = HERE / "briefing_settings.json"
TOPICS_FILE      = HERE / "topics.json"
DASHBOARD_FILE   = HERE / "settings.html"

# ── Default settings (mirrors briefing.py hardcoded values) ─────────────────

DEFAULT_SETTINGS = {
    "story_counts": {
        "top_n_stories":       5,
        "top_n_topic_stories": 5,
        "max_feed_items":      20,
    },
    "categories": [
        {
            "name":  "International News",
            "emoji": "🌍",
            "color": "#1a3a5c",
            "feeds": [
                "https://feeds.bbci.co.uk/news/world/rss.xml",
                "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
                "https://www.theguardian.com/world/rss",
                "https://feeds.reuters.com/reuters/worldNews",
                "https://www.aljazeera.com/xml/rss/all.xml",
            ],
        },
        {
            "name":  "Australian News",
            "emoji": "🦘",
            "color": "#1a4a2e",
            "feeds": [
                "https://www.abc.net.au/news/feed/2942460/rss.xml",
                "https://www.smh.com.au/rss/feed.xml",
                "https://www.theaustralian.com.au/feed",
                "https://feeds.skynews.com.au/feeds/news.xml",
                "https://www.theguardian.com/australia-news/rss",
            ],
        },
        {
            "name":  "Australian Finance & Markets",
            "emoji": "📈",
            "color": "#3a1a4a",
            "feeds": [
                "https://www.abc.net.au/news/business/rss.xml",
                "https://www.theage.com.au/rss/business.xml",
                "https://www.news.com.au/content-feeds/latest-news-finance/",
                "https://financialnewswire.com.au/feed",
                "https://afndaily.com.au/feed",
                "https://www.rba.gov.au/rss/media-releases.xml",
                "https://au.investing.com/rss/news.rss",
                "https://www.asx.com.au/asx/1/news/rss",
            ],
        },
    ],
    "market_tickers": [
        {"sym": "^AXJO",    "label": "ASX 200", "fmt": "index"},
        {"sym": "AUDUSD=X", "label": "AUD/USD", "fmt": "fx"},
        {"sym": "GC=F",     "label": "Gold",    "fmt": "price"},
        {"sym": "CL=F",     "label": "Oil",     "fmt": "price"},
        {"sym": "^GSPC",    "label": "S&P 500", "fmt": "index"},
    ],
    "asx_watchlist": [
        {"sym": "GAS.AX", "label": "GAS"},
        {"sym": "COI.AX", "label": "COI"},
        {"sym": "BPT.AX", "label": "BPT"},
        {"sym": "STO.AX", "label": "STO"},
    ],
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️  Could not read settings file: {e}")
    import copy
    return copy.deepcopy(DEFAULT_SETTINGS)


def save_settings(data: dict):
    data["_updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   💾  Settings saved → {SETTINGS_FILE.resolve()}")
    print(f"       Categories: {len(data.get('categories', []))}  "
          f"Tickers: {len(data.get('market_tickers', []))}  "
          f"ASX: {len(data.get('asx_watchlist', []))}")


def load_topics() -> list:
    if TOPICS_FILE.exists():
        try:
            raw = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
            return raw.get("topics", [])
        except Exception:
            pass
    return []


def save_topics(topics: list):
    data = {
        "_comment": "Watch topics for Morning Briefing. Edit here or via settings dashboard.",
        "topics": topics,
    }
    TOPICS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/settings.html":
            if not DASHBOARD_FILE.exists():
                self._send_json(404, {"error": "settings.html not found"})
                return
            body = DASHBOARD_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/ping":
            self._send_json(200, {
                "ok": True,
                "settings_file": str(SETTINGS_FILE.resolve()),
                "settings_exists": SETTINGS_FILE.exists(),
            })

        elif self.path == "/api/settings":
            self._send_json(200, load_settings())

        elif self.path == "/api/topics":
            self._send_json(200, {"topics": load_topics()})

        elif self.path == "/shutdown":
            body = b"Closing settings dashboard..."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            shutdown_event.set()

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            if self.path == "/api/settings":
                data = self._read_json()
                save_settings(data)
                print(f"   💾  Settings saved → {SETTINGS_FILE.name}")
                self._send_json(200, {"ok": True})

            elif self.path == "/api/topics":
                data = self._read_json()
                save_topics(data.get("topics", []))
                print(f"   💾  Topics saved → {TOPICS_FILE.name}  ({len(data.get('topics',[]))} topics)")
                self._send_json(200, {"ok": True})

            else:
                self.send_response(404)
                self.end_headers()

        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass   # suppress request logs


# ── Entry point ───────────────────────────────────────────────────────────────

shutdown_event = threading.Event()

if __name__ == "__main__":
    for port in (8766, 8767, 8768):
        try:
            server = HTTPServer(("localhost", port), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit("❌  No free port found (tried 8766–8768).")

    url = f"http://localhost:{port}"
    print(f"\n⚙️   Briefing Settings Dashboard")
    print(f"    {url}")
    print(f"    Settings file: {SETTINGS_FILE.resolve()}")
    print(f"    Topics file:   {TOPICS_FILE.resolve()}")
    print(f"    Press Ctrl+C when done.\n")

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        while not shutdown_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\n   Settings server closed.\n")
