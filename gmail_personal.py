"""
gmail_personal.py
-----------------
Reads the personal Gmail inbox over IMAP and extracts personal action items,
the same way inbox_actions.py does for Outlook.

Why IMAP and not the Gmail API: gmail.readonly is a RESTRICTED scope, so
publishing the OAuth app out of "Testing" demands branding, a verified domain,
a privacy policy and a security assessment. While it stays in Testing, Google
expires every refresh token after 7 days - which is exactly how the previous
integration died. An app password has none of that, and Google's own
documentation confirms third-party IMAP access continues (what is being
withdrawn is Gmailify and the "Check mail from other accounts" POP feature).

The mailbox is ALWAYS opened read-only. This module never marks as read,
never moves, never deletes, and never sends.

Self-test:
    py gmail_personal.py --test
"""

import os
import re
import ssl
import sys
import json
import email
import imaplib
import datetime
from email.header import decode_header, make_header
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

IMAP_HOST   = "imap.gmail.com"
IMAP_PORT   = 993
SETTINGS    = Path(__file__).parent / "briefing_settings.json"
ACTIONS_OUT = Path(__file__).parent / ".gmail_actions.json"

ADDRESS  = os.environ.get("GMAIL_ADDRESS", "").strip()
APP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()

DEFAULT_CFG = {
    "enabled":        True,
    "lookback_days":  3,
    "max_messages":   40,
    "max_actions":    8,
    "list_name":      "Personal Priorities",
    # senders and subjects never worth an action - newsletters, receipts, noise
    "ignore_senders": ["noreply", "no-reply", "donotreply", "notifications@",
                       "mailer-daemon", "newsletter", "marketing"],
    "ignore_subjects": ["unsubscribe", "your receipt", "order confirmation",
                        "statement is ready", "verify your email"],
}


def _load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if SETTINGS.exists():
        try:
            s = json.loads(SETTINGS.read_text(encoding="utf-8"))
            cfg.update(s.get("gmail_personal", {}) or {})
        except Exception as e:
            print(f"  WARNING: could not read gmail_personal settings: {e}")
    return cfg


def _decode(raw) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _body_text(msg, limit: int = 1200) -> str:
    """Plain text only; HTML is stripped crudely because we only need the gist."""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
               "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    parts.append(part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"))
                except Exception:
                    continue
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"))
        except Exception:
            pass
    text = "\n".join(parts)
    if not text:
        text = re.sub(r"<[^>]+>", " ", str(msg.get_payload())[:4000])
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _skip(sender: str, subject: str, cfg: dict) -> bool:
    s, j = sender.lower(), subject.lower()
    if any(p in s for p in cfg.get("ignore_senders", [])):
        return True
    return any(p in j for p in cfg.get("ignore_subjects", []))


def fetch_recent(cfg: dict = None) -> dict:
    """
    Returns {"messages": [...], "error": None|str}

    Each message: id, subject, sender, date, snippet
    """
    cfg = cfg or _load_cfg()
    if not cfg.get("enabled", True):
        return {"messages": [], "error": None}
    if not ADDRESS or not APP_PASS:
        return {"messages": [], "error":
                "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env"}

    since = (datetime.date.today()
             - datetime.timedelta(days=int(cfg.get("lookback_days", 3)))
             ).strftime("%d-%b-%Y")

    try:
        ctx = ssl.create_default_context()
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx) as M:
            M.login(ADDRESS, APP_PASS)
            # readonly=True - this module must never change the mailbox
            M.select("INBOX", readonly=True)
            typ, data = M.search(None, f'(SINCE {since})')
            if typ != "OK":
                return {"messages": [], "error": f"IMAP search failed: {typ}"}
            ids = data[0].split()[-int(cfg.get("max_messages", 40)):]

            out = []
            for num in reversed(ids):
                typ, raw = M.fetch(num, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
                subject = _decode(msg.get("Subject"))
                sender  = _decode(msg.get("From"))
                if _skip(sender, subject, cfg):
                    continue
                out.append({
                    "id":      num.decode() if isinstance(num, bytes) else str(num),
                    "subject": subject,
                    "sender":  sender,
                    "date":    _decode(msg.get("Date")),
                    "snippet": _body_text(msg),
                })
            return {"messages": out, "error": None}
    except imaplib.IMAP4.error as e:
        detail = str(e)
        hint = ""
        if "AUTHENTICATIONFAILED" in detail.upper() or "Invalid credentials" in detail:
            hint = ("\n  The app password was rejected. Check it was copied whole "
                    "(16 characters, spaces removed) and that 2-Step Verification "
                    "is still enabled on the account.")
        return {"messages": [], "error": f"IMAP error: {detail}{hint}"}
    except Exception as e:
        return {"messages": [], "error": f"could not read Gmail: {e}"}


PROMPT = """You are triaging Doug's PERSONAL inbox (his Gmail, not work).

Extract only genuine personal action items - things HE must do. Household
admin, school and sport logistics, family arrangements, bookings, renewals,
bills needing a decision, appointments to confirm.

Ignore entirely: newsletters, marketing, receipts for completed purchases,
notifications with nothing to decide, and anything that is work related
(State Gas, gas industry, investors, regulators) - that is handled elsewhere.

Return ONLY a JSON array, at most {max_actions} items, most pressing first:
  "action":   the task in the imperative, under 12 words
  "context":  one sentence of why, under 25 words
  "from":     who it came from, a name not an address
  "deadline": any date or timing stated in the email, else ""
  "priority": "high" | "normal" | "low"

No markdown fences. If there are no genuine personal actions, return [].

MESSAGES:
{messages}"""


def extract_actions(messages: list, api_key: str = "", cfg: dict = None) -> dict:
    """Returns {"actions": [...], "error": None|str}"""
    cfg = cfg or _load_cfg()
    if not messages:
        return {"actions": [], "error": None}
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"actions": [], "error": "ANTHROPIC_API_KEY not set"}

    try:
        import anthropic
    except ImportError:
        return {"actions": [], "error": "anthropic package not installed"}

    blob = "\n\n---\n\n".join(
        f"From: {m['sender']}\nDate: {m['date']}\nSubject: {m['subject']}\n{m['snippet'][:700]}"
        for m in messages[:30])

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1600,
            messages=[{"role": "user", "content": PROMPT.format(
                max_actions=cfg.get("max_actions", 8), messages=blob)}],
            timeout=90,
        )
        raw = msg.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        actions = json.loads(raw)
        if not isinstance(actions, list):
            return {"actions": [], "error": "unexpected response shape"}
        clean = []
        for a in actions:
            if not isinstance(a, dict) or not (a.get("action") or "").strip():
                continue
            clean.append({
                "action":   str(a.get("action", "")).strip()[:160],
                "context":  str(a.get("context", "")).strip()[:240],
                "from":     str(a.get("from", "")).strip()[:80],
                "deadline": str(a.get("deadline", "")).strip()[:60],
                "priority": (a.get("priority") or "normal").lower(),
                "source":   "gmail",
            })
        return {"actions": clean[:cfg.get("max_actions", 8)], "error": None}
    except Exception as e:
        return {"actions": [], "error": f"extraction failed: {e}"}


def get_personal_actions(api_key: str = "") -> dict:
    """
    Entry point for briefing.py.
    Returns {"actions": [...], "checked": int, "error": None|str}
    """
    cfg = _load_cfg()
    got = fetch_recent(cfg)
    if got.get("error"):
        return {"actions": [], "checked": 0, "error": got["error"]}
    res = extract_actions(got["messages"], api_key, cfg)
    payload = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "checked": len(got["messages"]),
               "actions": res.get("actions", [])}
    try:
        ACTIONS_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    except Exception:
        pass
    return {"actions": res.get("actions", []), "checked": len(got["messages"]),
            "error": res.get("error")}


def _self_test():
    print("Personal Gmail self-test")
    print("=" * 66)
    cfg = _load_cfg()
    print(f"address       : {ADDRESS or 'MISSING'}")
    print(f"app password  : {'set (' + str(len(APP_PASS)) + ' chars)' if APP_PASS else 'MISSING'}")
    print(f"lookback      : {cfg['lookback_days']} days, max {cfg['max_messages']} messages")

    print("\n-- connecting to Gmail over IMAP (read-only) --")
    got = fetch_recent(cfg)
    if got.get("error"):
        print(f"ERROR: {got['error']}")
        return 1
    msgs = got["messages"]
    print(f"  messages after filtering: {len(msgs)}")
    for m in msgs[:10]:
        print(f"   - {m['sender'][:34]:<34} | {m['subject'][:44]}")
    if not msgs:
        print("\nNo messages in the window. Widen lookback_days if that seems wrong.")
        return 0

    print("\n-- extracting personal actions --")
    res = extract_actions(msgs, cfg=cfg)
    if res.get("error"):
        print(f"ERROR: {res['error']}")
        return 1
    if not res["actions"]:
        print("  none found - nothing in the last few days needs doing.")
        return 0
    for i, a in enumerate(res["actions"], 1):
        dl = f"  [{a['deadline']}]" if a["deadline"] else ""
        print(f"  {i}. ({a['priority']}) {a['action']}{dl}")
        print(f"       {a['context']}  - from {a['from']}")
    print("\nCheck these are genuinely yours to do, and that nothing work-related")
    print("has leaked in from the wrong mailbox.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(_self_test())
