"""
gmail_probe.py - can we still read the Gmail inbox with the stored credentials?

Uses the ordinary Gmail REST API, not the gated MCP preview service.
Run:  py gmail_probe.py > gmail_probe.txt
"""
import os, sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from dotenv import load_dotenv
load_dotenv()
import requests

CID = os.environ.get("GMAIL_CLIENT_ID", "")
SEC = os.environ.get("GMAIL_CLIENT_SECRET", "")
RT  = os.environ.get("GMAIL_REFRESH_TOKEN", "")

print("Gmail access probe")
print("=" * 62)
print(f"client id     : {'set' if CID else 'MISSING'}")
print(f"client secret : {'set' if SEC else 'MISSING'}")
print(f"refresh token : {'set' if RT else 'MISSING'}")
if not (CID and SEC and RT):
    raise SystemExit("\nCredentials incomplete - stopping.")

print("\n-- step 1: exchange the refresh token for an access token --")
r = requests.post("https://oauth2.googleapis.com/token", data={
    "client_id": CID, "client_secret": SEC,
    "refresh_token": RT, "grant_type": "refresh_token"}, timeout=30)
print(f"  HTTP {r.status_code}")
if r.status_code != 200:
    print("  response:", r.text[:400])
    print("\n  invalid_grant almost always means the refresh token has been")
    print("  revoked or expired. Google expires refresh tokens after 7 days")
    print("  while an OAuth app is still in 'Testing' publishing status.")
    raise SystemExit(1)
tok = r.json()
access = tok.get("access_token", "")
print(f"  access token  : obtained ({len(access)} chars)")
print(f"  granted scope : {tok.get('scope','(not reported)')}")

H = {"Authorization": f"Bearer {access}"}

print("\n-- step 2: read the profile --")
r = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers=H, timeout=30)
print(f"  HTTP {r.status_code}")
if r.status_code == 200:
    p = r.json()
    print(f"  mailbox       : {p.get('emailAddress')}")
    print(f"  total messages: {p.get('messagesTotal')}")
else:
    print("  response:", r.text[:300]); raise SystemExit(1)

print("\n-- step 3: list recent primary-inbox threads --")
q = "newer_than:3d in:inbox -in:promotions -in:social -in:updates"
r = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                 headers=H, params={"q": q, "maxResults": 10}, timeout=30)
print(f"  HTTP {r.status_code}  query: {q}")
ids = [m["id"] for m in r.json().get("messages", [])] if r.status_code == 200 else []
print(f"  matched: {len(ids)} message(s)")

print("\n-- step 4: read headers of each (subject and sender only) --")
for mid in ids[:6]:
    rr = requests.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                      headers=H, params={"format": "metadata",
                                         "metadataHeaders": ["Subject", "From", "Date"]},
                      timeout=30)
    if rr.status_code != 200:
        print(f"  {mid}: HTTP {rr.status_code}"); continue
    hs = {h["name"]: h["value"] for h in rr.json().get("payload", {}).get("headers", [])}
    print(f"  - {hs.get('From','?')[:38]:<38} | {hs.get('Subject','(no subject)')[:46]}")

print("\nIf every step returned 200, the ordinary Gmail API works with the")
print("credentials already in .env, and the personal action extractor can be")
print("built on exactly the same pattern as the Outlook one.")
