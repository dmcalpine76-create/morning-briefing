"""
launcher_server.py  —  Local server for the Morning Briefing launcher GUI
-------------------------------------------------------------------------
Run this from your project folder:
    py launcher_server.py

Keep the terminal open. The launcher opens automatically in your browser.
Press Ctrl+C to stop.
"""

import os, sys, json, subprocess, threading, webbrowser, time, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections import deque

PORT   = 8780
FOLDER = Path(__file__).parent

COMMANDS = {
    "briefing":  ["py", "briefing.py"],
    "review":    ["py", "briefing.py", "review"],
    "scheduler": ["py", "outlook_scheduler.py", "dashboard"],
    "preview":   ["py", "outlook_scheduler.py", "preview"],
    "rules":     ["OPEN", "scheduling_rules.html"],
    "token":     ["py", "outlook_email.py", "setup"],
}

# Shared log buffer — stores last 200 lines, polled by browser
log_lines  = deque(maxlen=200)
log_lock   = threading.Lock()
running    = False
run_thread = None

def add_log(text: str, level: str = ""):
    with log_lock:
        log_lines.append({"t": time.time(), "text": text, "level": level})

def run_command(key: str):
    global running
    cmd = COMMANDS.get(key)
    if not cmd:
        add_log(f"Unknown command: {key}", "err")
        running = False
        return

    # Special case: open HTML in browser
    if cmd[0] == "OPEN":
        target = FOLDER / cmd[1]
        add_log(f"Opening {cmd[1]} in browser…", "info")
        webbrowser.open(target.as_uri())
        add_log(f"✓ Opened {cmd[1]}", "ok")
        running = False
        return

    add_log(f"▶  Running:  {' '.join(cmd)}", "info")
    add_log("─" * 48, "dim")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(FOLDER),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        )

        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            level = ("ok"   if any(x in line for x in ["✓","✅","Done","done"]) else
                     "err"  if any(x in line for x in ["✗","❌","Error","error","Traceback","Exception"]) else
                     "warn" if any(x in line for x in ["⚠","Warning","warning"]) else
                     "info" if any(x in line for x in ["🌐","📅","📋","📬","Opening","Serving","localhost"]) else
                     "")
            add_log(line, level)

        proc.wait()
        add_log("─" * 48, "dim")
        if proc.returncode == 0:
            add_log("✅  Finished successfully", "ok")
        else:
            add_log(f"⚠️  Process exited with code {proc.returncode}", "warn")

    except FileNotFoundError:
        add_log("❌  'py' not found. Make sure Python is in your PATH.", "err")
    except Exception as e:
        add_log(f"❌  Error: {e}", "err")
    finally:
        running = False


class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200); self._cors_headers(); self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/launcher.html"):
            # Serve the launcher HTML directly so fetch() to localhost works
            html_file = FOLDER / "launcher.html"
            if html_file.exists():
                body = html_file.read_bytes()
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()

        elif self.path == "/ping":
            self._json({"ok": True, "running": running})

        elif self.path.startswith("/log"):
            since = 0
            if "since=" in self.path:
                try: since = float(self.path.split("since=")[1])
                except: pass
            with log_lock:
                lines = [l for l in log_lines if l["t"] > since]
            self._json({"lines": lines, "running": running})

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        global running, run_thread
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/run":
            key = body.get("cmd", "")
            if key not in COMMANDS:
                self._json({"ok": False, "error": "Unknown command"}); return

            if running:
                self._json({"ok": False, "error": "A command is already running"}); return

            running = True
            run_thread = threading.Thread(target=run_command, args=(key,), daemon=True)
            run_thread.start()
            self._json({"ok": True, "started": key})

        elif self.path == "/stop":
            # Can't easily kill subprocess, but mark as not running
            running = False
            self._json({"ok": True})

        else:
            self.send_response(404); self.end_headers()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress HTTP noise


if __name__ == "__main__":
    server = HTTPServer(("localhost", PORT), Handler)
    add_log("Launcher server started", "ok")
    add_log(f"Project folder: {FOLDER}", "dim")
    add_log("Click a button above to run a command.", "dim")
    print(f"\n  🚀  Morning Briefing Launcher")
    print(f"  ──────────────────────────────────────")
    print(f"  Server running on http://localhost:{PORT}")
    print(f"  Project folder: {FOLDER}")
    print(f"\n  Opening http://localhost:8780 in your browser…")
    print(f"  Press Ctrl+C to stop.\n")
    threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()
