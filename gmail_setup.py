"""
gmail_setup.py - re-authorise Gmail read access, the way Google now requires.

The previous flow used urn:ietf:wg:oauth:2.0:oob, which Google deprecated in
2022 and blocked outright in 2023. The comment in gmail_email.py claiming
localhost was blocked has it backwards: loopback is the recommended redirect
for desktop apps, and OOB is the one that no longer works.

BEFORE RUNNING, in Google Cloud Console:
  1. APIs & Services > OAuth consent screen > PUBLISH APP.
     While publishing status is "Testing", Google expires every refresh token
     after 7 days - which is why the stored one had already died.
  2. APIs & Services > Credentials: the OAuth client must be type
     "Desktop app". Desktop clients accept any loopback port automatically.
  3. APIs & Services > Library: Gmail API enabled.

Run:  py gmail_setup.py
"""
import os, sys, json, socket, urllib.parse, webbrowser, datetime, pathlib
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv()
import requests

ENV_FILE  = pathlib.Path(__file__).parent / ".env"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE     = "https://www.googleapis.com/auth/gmail.readonly"

CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")

_received = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(q)
        _received.update({k: v[0] for k, v in params.items()})
        body = ("<html><body style='font-family:system-ui;padding:3rem'>"
                "<h2>Authorisation received</h2>"
                "<p>You can close this tab and return to the terminal.</p>"
                "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    print("Gmail authorisation")
    print("=" * 62)
    if not CLIENT_ID or not CLIENT_SECRET:
        print("GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET missing from .env")
        return 1

    port = _free_port()
    redirect_uri = f"http://localhost:{port}"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={urllib.parse.quote(CLIENT_ID)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        "&response_type=code"
        f"&scope={urllib.parse.quote(SCOPE)}"
        "&access_type=offline"
        "&prompt=consent"
    )

    print(f"\nListening on {redirect_uri}")
    print("Opening your browser. Sign in as dmcalpine76@gmail.com and allow access.")
    print("If the app is unverified, choose Advanced then 'Go to ... (unsafe)'.")
    print(f"\nIf the browser does not open, paste this:\n\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    srv = HTTPServer(("127.0.0.1", port), _Handler)
    srv.timeout = 300
    while "code" not in _received and "error" not in _received:
        srv.handle_request()

    if "error" in _received:
        print(f"\nGoogle returned an error: {_received['error']}")
        if _received["error"] == "redirect_uri_mismatch":
            print("  The OAuth client is probably type 'Web application'.")
            print(f"  Either change it to 'Desktop app', or add {redirect_uri}")
            print("  as an authorised redirect URI.")
        return 1

    print("\nExchanging the authorisation code for tokens...")
    r = requests.post(TOKEN_URL, data={
        "code": _received["code"],
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=30)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text[:400]}")
        return 1

    tok = r.json()
    refresh = tok.get("refresh_token", "")
    if not refresh:
        print("  No refresh token returned. Revoke the app's access at")
        print("  myaccount.google.com/permissions and run this again.")
        return 1
    print(f"  refresh token obtained ({len(refresh)} chars)")
    print(f"  scope granted: {tok.get('scope','')}")

    # verify immediately
    p = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                     headers={"Authorization": f"Bearer {tok['access_token']}"},
                     timeout=30)
    print(f"  profile check: HTTP {p.status_code}"
          + (f"  mailbox {p.json().get('emailAddress')}" if p.status_code == 200 else ""))

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    text = ENV_FILE.read_text(encoding="utf-8", errors="replace")
    ENV_FILE.with_name(f".env.bak-{stamp}").write_text(text, encoding="utf-8")
    out, done = [], False
    for line in text.splitlines():
        if line.strip().startswith("GMAIL_REFRESH_TOKEN"):
            out.append(f"GMAIL_REFRESH_TOKEN={refresh}"); done = True
        else:
            out.append(line)
    if not done:
        out.append(f"GMAIL_REFRESH_TOKEN={refresh}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n.env updated (backup .env.bak-{stamp})")
    print("Now run:  py gmail_probe.py > gmail_probe.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
