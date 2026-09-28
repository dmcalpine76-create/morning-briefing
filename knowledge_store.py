"""
knowledge_store.py - read (and, for the Friday wrap, write one file to) the
State Gas knowledge store: the "state gas knowledge" folder in OneDrive that
inbox_actions, the Sunday weekly plan and the board tools all share.

On the laptop the store is the synced folder next to this one. In GitHub
Actions there is no synced folder, so files are read through Microsoft Graph
with the same Microsoft sign-in the briefing already uses (the shared sign-in
includes OneDrive file access).

Everything here is best-effort. If the store cannot be reached for any reason
the caller gets None / "" and the briefing runs exactly as it did before.
Nothing from the store is ever printed to the (public) Actions log.
"""
import os
import json
import datetime
from pathlib import Path
from urllib.parse import quote

GRAPH = "https://graph.microsoft.com/v1.0"
DRIVE_PATH = os.environ.get(
    "STORE_DRIVE_PATH",
    "Documents/Current document editing/new AI projects/state gas knowledge")
_HERE = Path(__file__).resolve().parent
_CLOUD = os.environ.get("CI") == "true"
_etags = {}


def _local_dir():
    p = os.environ.get("STATEGAS_STORE_DIR")
    cand = Path(p) if p else _HERE.parent / "state gas knowledge"
    return cand if (not _CLOUD and cand.exists()) else None


def _token():
    try:
        import outlook_email as _o
        cache = _o._load_cache()
        app = _o._build_app(cache)
        accts = app.get_accounts()
        if not accts:
            return None
        r = app.acquire_token_silent(["Files.ReadWrite"], account=accts[0])
        if r and "access_token" in r:
            _o._save_cache(cache)
            return r["access_token"]
    except Exception:
        pass
    return None


def _url(rel, suffix=""):
    path = "/".join(quote(p) for p in f"{DRIVE_PATH}/{rel}".split("/"))
    return f"{GRAPH}/me/drive/root:/{path}{suffix}"


def read_json(rel):
    """A store file as parsed JSON, or None."""
    try:
        d = _local_dir()
        if d:
            f = d / rel
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        tok = _token()
        if not tok:
            return None
        import requests
        h = {"Authorization": f"Bearer {tok}"}
        meta = requests.get(_url(rel), headers=h, timeout=30)
        if meta.status_code != 200:
            return None
        _etags[rel] = meta.json().get("eTag")
        r = requests.get(_url(rel, ":/content"), headers=h, timeout=60)
        return r.json() if r.ok else None
    except Exception:
        return None


def write_json(rel, data):
    """Write one store file. Never overwrites a newer copy. True on success."""
    body = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        d = _local_dir()
        if d:
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f.suffix + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(f)
            return True
        tok = _token()
        if not tok:
            return False
        import requests
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
        if _etags.get(rel):
            h["If-Match"] = _etags[rel]
        r = requests.put(_url(rel, ":/content"), headers=h, data=body.encode("utf-8"), timeout=60)
        return r.status_code in (200, 201)
    except Exception:
        return False


def deadlines(days_ahead=30):
    """Dated obligations from the store within the window, soonest first."""
    d = read_json("deadlines.json") or {}
    today = datetime.date.today()
    out = []
    for x in d.get("deadlines", []):
        try:
            when = datetime.date.fromisoformat((x.get("date") or "")[:10])
        except ValueError:
            continue
        if today <= when <= today + datetime.timedelta(days=days_ahead):
            out.append({**x, "when": when})
    return sorted(out, key=lambda x: x["when"])


def email_context(sender_emails=(), max_matters=40, max_chars=9000):
    """
    What the company knowledge says that helps triage today's email:
    live matters (and what to watch for), who the senders are, and dated
    obligations in the next three weeks. "" if the store is unreachable.
    """
    try:
        m = (read_json("matters.json") or {}).get("matters", {})
        if not m:
            return ""
        live = [x for x in m.values() if (x.get("status") or "active") == "active"]
        live.sort(key=lambda x: x.get("last_active") or "", reverse=True)
        lines = ["LIVE MATTERS (most recently active first):"]
        for x in live[:max_matters]:
            s = f"- {x.get('name','')} [{x.get('category','')}]"
            if x.get("next_step"):
                s += f" - next: {x['next_step'][:140]}"
                if x.get("next_step_due"):
                    s += f" (due {x['next_step_due']})"
            if x.get("watch_for"):
                s += f" | watch for: {x['watch_for'][:140]}"
            lines.append(s)

        senders = {e.lower() for e in sender_emails if e}
        if senders:
            ppl = (read_json("people.json") or {}).get("key_people", {})
            known = [p for p in ppl.values() if (p.get("email") or "").lower() in senders]
            if known:
                lines.append("\nWHO TODAY'S SENDERS ARE:")
                for p in known[:40]:
                    lines.append(f"- {p.get('name','')} <{p.get('email','')}>: "
                                 f"{p.get('role','')}, {p.get('organisation','')}")

        dl = deadlines(21)
        if dl:
            lines.append("\nDATED OBLIGATIONS IN THE NEXT 3 WEEKS:")
            for x in dl[:15]:
                lines.append(f"- {x['when']:%a %d %b}: {x.get('title','')}")
        text = "\n".join(lines)
        return text[:max_chars]
    except Exception:
        return ""
