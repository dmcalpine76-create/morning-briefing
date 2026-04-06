"""
outlook_email.py  —  Microsoft Outlook / Graph API email integration
---------------------------------------------------------------------
Uses Microsoft Graph search API to fetch recent emails efficiently
rather than crawling folder-by-folder.

Usage:
  py outlook_email.py setup   — one-time browser auth
  py outlook_email.py test    — test connection, print raw emails
"""

import os
import json
import datetime
import requests
import msal
import anthropic
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────
# CONFIG  — update AUTHORITY with your tenant ID
# ─────────────────────────────────────────────

CLIENT_ID  = os.environ.get("OUTLOOK_CLIENT_ID", "")
AUTHORITY  = "https://login.microsoftonline.com/" + os.environ.get("OUTLOOK_TENANT_ID", "consumers")
SEND_TO    = os.environ.get("BRIEFING_EMAIL_TO", "")   # your email address

SCOPES     = ["Mail.Read", "Mail.Send", "User.Read"]
CACHE_FILE = Path(__file__).parent / ".outlook_token_cache.bin"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Tuning
MAX_EMAILS       = 80   # max emails to analyse
MAX_DIGEST_ITEMS = 10
MAX_ACTIONS      = 8
MAX_PEOPLE       = 8
REQUEST_TIMEOUT  = 12   # seconds per API call
MAX_FOLDERS      = 20   # never crawl more than this many folders


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

def _load_cache():
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text())
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize())
        try:
            CACHE_FILE.chmod(0o600)
        except Exception:
            pass

def _build_app(cache):
    if not CLIENT_ID:
        raise RuntimeError("OUTLOOK_CLIENT_ID not set. Add it to your .env file.")
    return msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

def get_access_token() -> str:
    cache = _load_cache()
    app   = _build_app(cache)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]
    raise RuntimeError(
        "Outlook token missing or expired.\n"
        "Run:  py outlook_email.py setup"
    )

def setup_auth():
    if not CLIENT_ID:
        print("\n❌  OUTLOOK_CLIENT_ID not found in environment / .env")
        return
    cache = _load_cache()
    app   = _build_app(cache)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            print(f"\n✅  Already authenticated as: {accounts[0]['username']}\n")
            return
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"\n❌  Failed: {json.dumps(flow, indent=2)}\n")
        return
    print("\n" + "─" * 60)
    print("  OUTLOOK AUTHORISATION")
    print("─" * 60)
    print(f"\n  1. Open:  https://microsoft.com/devicelogin")
    print(f"  2. Enter: {flow['user_code']}")
    print(f"  3. Sign in with your Microsoft account")
    print(f"\n  Waiting…\n")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        _save_cache(cache)
        try:
            me    = requests.get(f"{GRAPH_BASE}/me",
                                 headers={"Authorization": f"Bearer {result['access_token']}"},
                                 timeout=10).json()
            print(f"✅  Authenticated: {me.get('displayName')} ({me.get('mail') or me.get('userPrincipalName')})\n")
        except Exception:
            print("✅  Authenticated successfully!\n")
    else:
        print(f"\n❌  {result.get('error_description', result)}\n")


# ─────────────────────────────────────────────
# EMAIL FETCHING  —  fast, flat, no recursion
# ─────────────────────────────────────────────

def _graph_get(token: str, path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_msg(msg: dict, folder_name: str, is_sent: bool) -> dict:
    sender   = msg.get("from", {}).get("emailAddress", {})
    to_list  = [r.get("emailAddress", {}).get("name", "") or r.get("emailAddress", {}).get("address", "")
                for r in msg.get("toRecipients", [])[:3]]
    ts       = msg.get("sentDateTime") or msg.get("receivedDateTime", "")
    return {
        "subject":    msg.get("subject", "(no subject)").strip(),
        "from_name":  sender.get("name", ""),
        "from_email": sender.get("address", ""),
        "to":         ", ".join(to_list),
        "received":   ts,
        "preview":    msg.get("bodyPreview", "")[:500].strip(),
        "importance": msg.get("importance", "normal"),
        "is_read":    msg.get("isRead", True),
        "has_attach": msg.get("hasAttachments", False),
        "folder":     folder_name,
        "is_sent":    is_sent,
    }


def fetch_recent_emails(token: str, hours_back: int = 48) -> list:
    """
    Fetch recent emails using three targeted calls only:
      1. Inbox (received)
      2. Sent Items
      3. Top-level custom folders (flat, no recursion, capped at MAX_FOLDERS)
    Fast and predictable — no recursive folder crawling.
    """
    since     = datetime.datetime.utcnow() - datetime.timedelta(hours=hours_back)
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    base_params = {
        "$orderby": "receivedDateTime desc",
        "$top":     40,
        "$select":  "subject,from,toRecipients,receivedDateTime,bodyPreview,importance,isRead,hasAttachments",
    }
    sent_params = {
        "$orderby": "sentDateTime desc",
        "$top":     20,
        "$select":  "subject,from,toRecipients,sentDateTime,bodyPreview,importance,hasAttachments",
        "$filter":  f"sentDateTime ge {since_str}",
    }

    all_emails = []
    seen_ids   = set()

    def add_from_folder(folder_id, folder_name, is_sent, params):
        p = dict(params)
        if not is_sent:
            p["$filter"] = f"receivedDateTime ge {since_str}"
        try:
            print(f"   → {folder_name}…", end=" ", flush=True)
            data = _graph_get(token, f"/me/mailFolders/{folder_id}/messages", p)
            msgs = data.get("value", [])
            added = 0
            for msg in msgs:
                mid = msg.get("id", "")
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    all_emails.append(_parse_msg(msg, folder_name, is_sent))
                    added += 1
            print(f"{added} emails")
        except Exception as e:
            print(f"skipped ({type(e).__name__})")

    # 1. Inbox
    add_from_folder("inbox", "Inbox", False, base_params)

    # 2. Sent Items
    add_from_folder("sentitems", "Sent Items", True, sent_params)

    # 3. Other top-level folders (flat only, no recursion, skip system folders)
    SKIP = {"inbox", "deleteditems", "junkemail", "drafts", "outbox",
            "sentitems", "archive", "conversationhistory", "syncissues"}
    try:
        data    = _graph_get(token, "/me/mailFolders", {"$top": 50})
        folders = data.get("value", [])
        checked = 0
        for f in folders:
            if checked >= MAX_FOLDERS:
                print(f"   ℹ️  Folder cap reached ({MAX_FOLDERS}), skipping remainder")
                break
            fname = f.get("displayName", "")
            fid   = f["id"]
            if fname.lower().replace(" ", "") in SKIP:
                continue
            # Only scan folder if it has messages received in window
            total = f.get("totalItemCount", 0)
            if total == 0:
                continue
            add_from_folder(fid, fname, False, base_params)
            checked += 1
    except Exception as e:
        print(f"   ⚠️  Could not list extra folders: {e}")

    all_emails.sort(key=lambda e: e["received"], reverse=True)
    return all_emails[:MAX_EMAILS]


# ─────────────────────────────────────────────
# AI ANALYSIS
# ─────────────────────────────────────────────

def _fmt(email: dict, index: int) -> str:
    flags = []
    if email["is_sent"]:       flags.append("SENT BY ME")
    elif not email["is_read"]: flags.append("UNREAD")
    if email["importance"] == "high": flags.append("HIGH IMPORTANCE")
    if email["has_attach"]:    flags.append("has attachment")
    flag_str  = f" [{', '.join(flags)}]" if flags else ""
    timestamp = email["received"][:16].replace("T", " ") if email["received"] else ""
    to_str    = f"To: {email['to']}\n" if email["is_sent"] and email["to"] else ""
    return (
        f"[{index+1}]{flag_str}\n"
        f"From: {email['from_name']} <{email['from_email']}>\n"
        f"{to_str}"
        f"Subject: {email['subject']}\n"
        f"Folder: {email['folder']}\n"
        f"Time: {timestamp}\n"
        f"Preview: {email['preview']}\n"
    )


def analyse_emails(client: anthropic.Anthropic, emails: list) -> dict:
    if not emails:
        return {"digest": [], "actions": [], "people": []}

    emails_text = "\n\n".join(_fmt(e, i) for i, e in enumerate(emails))
    today = datetime.date.today().strftime("%A %d %B %Y")

    prompt = f"""You are a sharp executive assistant. Today is {today}.
You have {len(emails)} emails from the last 24 hours (received + sent).
Return a JSON object with exactly three keys: "digest", "actions", "people".

"digest": up to {MAX_DIGEST_ITEMS} most important emails.
Each: subject, from_name, from_email, received, folder, is_read (bool), is_sent (bool),
priority ("action-required"|"important"|"informational"),
summary (2 sentences max), action (short phrase or "").

"actions": up to {MAX_ACTIONS} concrete things YOU need to do today.
Each: action (specific task), context (1 sentence), deadline ("" if none),
priority ("urgent"|"high"|"normal"), from_email (subject reference).

"people": up to {MAX_PEOPLE} people to contact or meetings to schedule.
Each: name, email, action ("contact"|"schedule-meeting"|"follow-up"),
reason (1-2 sentences), suggested_timing, context (which email).

Deprioritise: newsletters, automated alerts, marketing, read receipts.
Return ONLY the JSON object — no markdown, no extra text.

EMAILS:
{emails_text}
"""

    try:
        message = client.messages.create(
            model      = "claude-opus-4-6",
            max_tokens = 4000,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        return {
            "digest":  result.get("digest", []),
            "actions": result.get("actions", []),
            "people":  result.get("people", []),
        }
    except Exception as e:
        print(f"  ⚠️  Email analysis failed: {e}")
        return {"digest": [], "actions": [], "people": []}


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

def get_email_analysis(client: anthropic.Anthropic) -> dict:
    if not CLIENT_ID:
        return {}
    try:
        token  = get_access_token()
        print(f"   Scanning folders…")
        emails = fetch_recent_emails(token)
        sent   = sum(1 for e in emails if e["is_sent"])
        recvd  = len(emails) - sent
        print(f"   📬  {recvd} received + {sent} sent = {len(emails)} total")
        if not emails:
            return {}
        print(f"   🤖  Running AI analysis…")
        result = analyse_emails(client, emails)
        print(f"   ✓   {len(result['digest'])} priority · "
              f"{len(result['actions'])} actions · "
              f"{len(result['people'])} people/meetings")
        return result
    except RuntimeError as e:
        print(f"  ⚠️  Outlook: {e}")
        return {}
    except Exception as e:
        print(f"  ⚠️  Outlook error: {e}")
        return {}

def get_email_digest(client):
    return get_email_analysis(client).get("digest", [])



# ─────────────────────────────────────────────
# SEND BRIEFING EMAIL
# ─────────────────────────────────────────────

def _build_email_body(analysis: dict, sections: dict, generated_at: datetime.datetime) -> str:
    """
    Build a clean HTML email body with:
      - Top news stories from each category (headline + one-line summary + link)
      - Work actions digest (action items + people to contact)
    No JavaScript, no tabs — renders cleanly in any email client.
    """
    date_str = generated_at.strftime("%A %#d %B %Y")
    time_str = generated_at.strftime("%H:%M AEST")

    # ── Styles (inline-friendly) ──
    S = {
        "body":     "font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:700px;margin:0 auto;padding:20px",
        "masthead": "background:#1a1a1a;color:#fff;padding:16px 20px;margin-bottom:0",
        "title":    "font-size:22px;font-weight:900;margin:0;letter-spacing:-0.5px",
        "date":     "font-size:12px;color:rgba(255,255,255,0.5);margin:3px 0 0",
        "rule":     "border:none;border-top:3px solid #c0392b;margin:0 0 20px",
        "sec_head": "font-size:15px;font-weight:700;color:#1a1a1a;border-bottom:2px solid #eee;padding-bottom:6px;margin:24px 0 12px",
        "story":    "margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #f0f0f0",
        "hl":       "font-size:13px;font-weight:700;color:#1a1a1a;margin:0 0 3px",
        "hl_link":  "font-size:13px;font-weight:700;color:#1a3a5c;text-decoration:none",
        "src":      "font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.5px;margin:0 0 3px",
        "summary":  "font-size:12px;color:#555;margin:0;line-height:1.5",
        "badge_a":  "display:inline-block;font-size:10px;font-weight:700;background:#fde8e8;color:#c0392b;padding:1px 5px;border-radius:2px;margin-right:5px",
        "badge_b":  "display:inline-block;font-size:10px;font-weight:700;background:#fef3e2;color:#d35400;padding:1px 5px;border-radius:2px;margin-right:5px",
        "badge_c":  "display:inline-block;font-size:10px;font-weight:700;background:#e8f4e8;color:#27ae60;padding:1px 5px;border-radius:2px;margin-right:5px",
        "action":   "background:#fff;border:1px solid #ddd;border-left:3px solid #c0392b;padding:8px 10px;margin-bottom:8px;border-radius:2px",
        "act_title":"font-size:13px;font-weight:700;color:#1a1a1a;margin:0 0 3px",
        "act_ctx":  "font-size:12px;color:#666;margin:0",
        "deadline": "display:inline-block;font-size:10px;font-weight:700;background:#fde8e8;color:#c0392b;padding:1px 5px;border-radius:2px;margin-left:6px",
        "person":   "background:#fff;border:1px solid #ddd;padding:8px 10px;margin-bottom:8px;border-radius:2px",
        "footer":   "font-size:11px;color:#aaa;margin-top:24px;padding-top:12px;border-top:1px solid #eee",
    }

    BADGE_STYLE = {"critical": S["badge_a"], "major": S["badge_b"], "notable": S["badge_c"]}
    BADGE_LABEL = {"critical": "⚡ Critical", "major": "● Major", "notable": "◦ Notable"}

    html = f"""<div style="{S['body']}">
<div style="{S['masthead']}">
  <h1 style="{S['title']}">Doug's Morning Briefing</h1>
  <p style="{S['date']}">{date_str} &mdash; Generated {time_str}</p>
</div>
<hr style="{S['rule']}">"""

    # ── News sections ──
    for cat_name, stories in sections.items():
        if not stories:
            continue
        emoji = {"International News": "🌍", "Australian News": "🦘",
                 "Australian Finance & Markets": "📈"}.get(cat_name, "📰")
        html += f'<div style="{S["sec_head"]}">{emoji} {cat_name}</div>'
        for s in stories[:10]:
            sig   = s.get("significance", "notable").lower()
            badge = f'<span style="{BADGE_STYLE.get(sig, S["badge_c"])}">{BADGE_LABEL.get(sig, "◦ Notable")}</span>'
            link  = s.get("link", "").strip()
            hl    = s.get("headline", "")
            src   = s.get("source", "")
            summ  = s.get("summary", "")
            hl_html = f'<a href="{link}" style="{S["hl_link"]}">{hl}</a>' if link.startswith("http") else f'<span style="{S["hl"]}">{hl}</span>'
            html += f"""<div style="{S['story']}">
  <p style="{S['src']}">{badge} {src}</p>
  <p style="margin:0 0 3px">{hl_html}</p>
  <p style="{S['summary']}">{summ}</p>
</div>"""

    # ── Work Actions ──
    actions = (analysis or {}).get("actions", [])
    people  = (analysis or {}).get("people", [])
    digest  = (analysis or {}).get("digest", [])

    if actions:
        html += f'<div style="{S["sec_head"]}">⚡ Actions for Today</div>'
        for i, a in enumerate(actions):
            dl  = a.get("deadline", "")
            dl_html = f'<span style="{S["deadline"]}">⏰ {dl}</span>' if dl else ""
            html += f"""<div style="{S['action']}">
  <p style="{S['act_title']}">{i+1}. {a.get("action","")} {dl_html}</p>
  <p style="{S['act_ctx']}">{a.get("context","")}</p>
</div>"""

    if people:
        html += f'<div style="{S["sec_head"]}">👥 People &amp; Meetings</div>'
        ICONS = {"schedule-meeting": "📅", "follow-up": "🔄", "contact": "💬"}
        LABELS = {"schedule-meeting": "Schedule meeting", "follow-up": "Follow up", "contact": "Contact"}
        for p in people:
            act  = p.get("action", "contact")
            icon = ICONS.get(act, "💬")
            lbl  = LABELS.get(act, act)
            timing = p.get("suggested_timing", "")
            html += f"""<div style="{S['person']}">
  <p style="{S['act_title']}">{icon} {p.get("name","")} &mdash; <em style="font-weight:normal">{lbl}</em>{"&nbsp;&nbsp;⏰&nbsp;" + timing if timing else ""}</p>
  <p style="{S['act_ctx']}">{p.get("reason","")}</p>
</div>"""

    if digest:
        action_count = sum(1 for d in digest if d.get("priority") == "action-required")
        unread_count = sum(1 for d in digest if not d.get("is_read") and not d.get("is_sent"))
        html += f'''<div style="{S["sec_head"]}">📬 Priority Inbox Summary</div>
<p style="{S["summary"]}">{len(digest)} priority emails · {unread_count} unread · {action_count} need action</p>'''

    html += f'''<p style="{S["footer"]}">
Open the attached <strong>Morning_Briefing_{generated_at.strftime("%Y-%m-%d")}.html</strong>
in a browser for the full interactive briefing with all topic tabs.
</p></div>'''

    return html


def send_briefing_email(briefing_html: str, out_dir_name: str,
                         generated_at: datetime.datetime,
                         sections: dict = None,
                         analysis: dict = None) -> bool:
    """
    Send the morning briefing with:
      - HTML body: readable news stories + work actions (works in any email client)
      - HTML attachment: full interactive briefing (open in browser for tabs/links)
    Requires Mail.Send permission and BRIEFING_EMAIL_TO in .env
    """
    if not SEND_TO:
        print("  ⚠️  BRIEFING_EMAIL_TO not set in .env — skipping email send")
        return False

    try:
        import base64
        token    = get_access_token()
        date_str = generated_at.strftime("%A %#d %B %Y")
        subject  = f"Doug's Morning Briefing — {date_str}"
        filename = f"Morning_Briefing_{generated_at.strftime('%Y-%m-%d')}.html"

        # Build readable email body
        body_html = _build_email_body(analysis or {}, sections or {}, generated_at)

        # Encode full HTML as attachment
        html_b64 = base64.b64encode(briefing_html.encode("utf-8")).decode("ascii")

        message = {
            "subject":    subject,
            "importance": "normal",
            "body": {
                "contentType": "HTML",
                "content":     body_html,
            },
            "toRecipients": [{"emailAddress": {"address": SEND_TO}}],
            "attachments": [
                {
                    "@odata.type":  "#microsoft.graph.fileAttachment",
                    "name":         filename,
                    "contentType":  "text/html",
                    "contentBytes": html_b64,
                }
            ],
        }

        resp = requests.post(
            f"{GRAPH_BASE}/me/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"message": message, "saveToSentItems": True},
            timeout=60,
        )

        if resp.status_code == 202:
            print(f"   ✉️   Emailed to {SEND_TO} — body + attachment ({filename})")
            return True
        else:
            print(f"   ⚠️  Email send failed: {resp.status_code} {resp.text[:200]}")
            return False

    except RuntimeError as e:
        print(f"   ⚠️  Email auth error: {e}")
        return False
    except Exception as e:
        print(f"   ⚠️  Email send error: {e}")
        return False


# ─────────────────────────────────────────────
# STANDALONE
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_auth()
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        print("\n🔍  Testing Outlook connection…")
        try:
            token  = get_access_token()
            me     = _graph_get(token, "/me")
            print(f"✅  Connected as: {me.get('displayName')} ({me.get('mail') or me.get('userPrincipalName')})\n")
            emails = fetch_recent_emails(token)
            sent   = sum(1 for e in emails if e["is_sent"])
            print(f"\n📬  {len(emails)} emails ({len(emails)-sent} received, {sent} sent)\n")
            for i, e in enumerate(emails[:8]):
                tag = "→ SENT" if e["is_sent"] else ("★ UNREAD" if not e["is_read"] else "  read")
                print(f"  {i+1}. [{tag}] [{e['folder']}]")
                print(f"     {e['from_name']}: {e['subject']}")
                print(f"     {e['preview'][:80]}…\n")
        except RuntimeError as e:
            print(f"❌  {e}\n")
    else:
        print(__doc__)
