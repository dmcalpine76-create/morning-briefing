"""
weekly_wrap.py  —  Friday afternoon week-in-review email  (D14)
----------------------------------------------------------------
Sends a compact HTML wrap of the week to the briefing addresses:

  •  Watchlist: week-over-week price moves for every ASX ticker
  •  Calendar:  meetings held this week (from Outlook)
  •  Tasks:     Microsoft To Do tasks completed this week
  •  Themes:    Claude-summarised themes from the week's email traffic

Run manually:   py weekly_wrap.py
Scheduled:      .github/workflows/friday_wrap.yml (Fridays 16:30 AEST)

Uses the same Outlook token cache and secrets as the daily briefing.
"""

import os
import json
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import outlook_email as _outlook
from asx_announcements import WATCHLIST, COMPANY_NAMES

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
AEST_OFFSET = datetime.timezone(datetime.timedelta(hours=10))
DAYFMT      = "%#d" if os.name == "nt" else "%-d"


# ── Watchlist week-over-week ──────────────────────────────────────────────────

def watchlist_week_moves() -> list[dict]:
    if not _YF_AVAILABLE:
        return []
    out = []
    for code in WATCHLIST:
        symbol = f"{code}.AX"
        try:
            hist = yf.Ticker(symbol).history(period="8d", interval="1d")
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                continue
            first, last = float(closes.iloc[0]), float(closes.iloc[-1])
            pct = (last - first) / first * 100 if first else 0.0
            out.append({
                "code":    code,
                "company": COMPANY_NAMES.get(code, code),
                "open":    first,
                "close":   last,
                "pct":     pct,
            })
        except Exception as e:
            print(f"   ⚠️  {symbol}: {e}")
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


# ── Calendar: this week's meetings ───────────────────────────────────────────

def week_meetings(token: str) -> list[dict]:
    now   = datetime.datetime.now(AEST_OFFSET)
    start = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/calendarView",
            headers={"Authorization": f"Bearer {token}",
                     "Prefer": 'outlook.timezone="E. Australia Standard Time"'},
            params={
                "startDateTime": start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "endDateTime":   now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "$select":       "subject,start,isAllDay",
                "$orderby":      "start/dateTime",
                "$top":          "50",
            },
            timeout=20,
        )
        resp.raise_for_status()
        meetings = []
        for ev in resp.json().get("value", []):
            subj = ev.get("subject", "")
            if subj.startswith("🎯"):
                continue    # scheduler blocks aren't meetings
            try:
                dt = datetime.datetime.fromisoformat(ev["start"]["dateTime"][:19])
                when = dt.strftime(f"%a {DAYFMT} %b, %H:%M")
            except Exception:
                when = ""
            meetings.append({"subject": subj, "when": when})
        return meetings
    except Exception as e:
        print(f"   ⚠️  Calendar fetch failed: {e}")
        return []


# ── To Do: completed this week ───────────────────────────────────────────────

def completed_tasks_this_week(token: str) -> list[dict]:
    now   = datetime.datetime.now(AEST_OFFSET)
    start = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    done = []
    try:
        lists = requests.get(
            f"{GRAPH_BASE}/me/todo/lists",
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        ).json().get("value", [])
        for lst in lists:
            try:
                tasks = requests.get(
                    f"{GRAPH_BASE}/me/todo/lists/{lst['id']}/tasks",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"$filter": "status eq 'completed'", "$top": "50"},
                    timeout=15,
                ).json().get("value", [])
            except Exception:
                continue
            for t in tasks:
                comp = (t.get("completedDateTime") or {}).get("dateTime", "")
                if not comp:
                    continue
                try:
                    cdt = datetime.datetime.fromisoformat(comp[:19]).replace(
                        tzinfo=datetime.timezone.utc).astimezone(AEST_OFFSET)
                except Exception:
                    continue
                if cdt >= start:
                    done.append({
                        "title": t.get("title", ""),
                        "list":  lst.get("displayName", ""),
                        "day":   cdt.strftime("%a"),
                    })
    except Exception as e:
        print(f"   ⚠️  To Do fetch failed: {e}")
    return done


# ── Email themes via Claude ──────────────────────────────────────────────────

def email_themes(api_key: str) -> list[str]:
    if not (_ANTHROPIC_AVAILABLE and api_key):
        return []
    try:
        emails = _outlook.fetch_recent_emails(hours_back=168, max_emails=80)
    except Exception as e:
        print(f"   ⚠️  Email fetch failed: {e}")
        return []
    if not emails:
        return []
    lines = "\n".join(
        f"- From {e.get('from','?')}: {e.get('subject','')}"
        for e in emails[:80]
    )
    prompt = f"""Below are the subjects of one week of work emails for Doug McAlpine
(State Gas, Queensland gas exploration). Identify the 3 to 5 main THEMES of
the week — recurring topics, projects or threads, not individual emails.

{lines}

Respond ONLY as a JSON array of short strings, each one theme in under
15 words. No markdown, no extra text."""
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
            timeout=60,
        )
        raw = resp.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return [str(t)[:120] for t in json.loads(raw)][:5]
    except Exception as e:
        print(f"   ⚠️  Theme summarisation failed: {e}")
        return []


# ── Compose + send ───────────────────────────────────────────────────────────

def build_html(moves, meetings, done, themes, now) -> str:
    def row(cells, bold=False):
        w = "700" if bold else "400"
        tds = "".join(
            f'<td style="padding:4px 10px;font-weight:{w};'
            f'border-bottom:1px solid #e5e0d4">{c}</td>' for c in cells)
        return f"<tr>{tds}</tr>"

    moves_rows = ""
    for m in moves:
        col = "#1e7d32" if m["pct"] >= 0 else "#c0392b"
        arrow = "▲" if m["pct"] >= 0 else "▼"
        moves_rows += row([
            f"<strong>{m['code']}</strong> {m['company']}",
            f"${m['close']:.3f}" if m["close"] < 1 else f"${m['close']:.2f}",
            f'<span style="color:{col};font-weight:700">{arrow} {m["pct"]:+.1f}%</span>',
        ])
    themes_html = "".join(f'<li style="margin:3px 0">{t}</li>' for t in themes) \
        or "<li>No email themes identified.</li>"
    meetings_html = "".join(
        f'<li style="margin:3px 0"><strong>{m["when"]}</strong> — {m["subject"]}</li>'
        for m in meetings) or "<li>No meetings recorded this week.</li>"
    done_html = "".join(
        f'<li style="margin:3px 0">✅ {t["title"]} '
        f'<span style="color:#888;font-size:0.85em">({t["day"]}, {t["list"]})</span></li>'
        for t in done) or "<li>No tasks marked complete this week.</li>"

    week_label = now.strftime(f"week ending %A {DAYFMT} %B %Y")
    return f"""<!DOCTYPE html><html><body style="font-family:'Segoe UI',Arial,sans-serif;
background:#f5f2eb;margin:0;padding:1.5rem 1rem;color:#1a1a1a">
<div style="max-width:640px;margin:0 auto">
<h1 style="font-family:Georgia,serif;font-size:1.35rem;border-bottom:3px solid #c0392b;
padding-bottom:0.5rem">📊 Friday Wrap — {week_label}</h1>

<h2 style="font-size:1rem;margin-top:1.4rem">📈 Watchlist — week-over-week</h2>
<table style="border-collapse:collapse;width:100%;font-size:0.9rem;background:#fff;
border:1px solid #e5e0d4">{moves_rows or row(['No price data available', '', ''])}</table>

<h2 style="font-size:1rem;margin-top:1.4rem">🧵 Themes of the week</h2>
<ul style="font-size:0.9rem;padding-left:1.2rem">{themes_html}</ul>

<h2 style="font-size:1rem;margin-top:1.4rem">✅ Completed tasks</h2>
<ul style="font-size:0.9rem;padding-left:1.2rem;list-style:none">{done_html}</ul>

<h2 style="font-size:1rem;margin-top:1.4rem">📅 Meetings this week</h2>
<ul style="font-size:0.9rem;padding-left:1.2rem">{meetings_html}</ul>

<p style="color:#888;font-size:0.75rem;margin-top:2rem">Generated automatically —
weekly_wrap.py · {now.strftime('%H:%M AEST')}</p>
</div></body></html>"""


def main():
    now = datetime.datetime.now(AEST_OFFSET)
    print("📊 Friday Wrap — " + now.strftime(f"%A {DAYFMT} %B %Y"))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    token   = _outlook.get_access_token()

    print("\n📈 Watchlist week moves…")
    moves = watchlist_week_moves()
    print(f"   ✓ {len(moves)} ticker(s)")

    print("📅 This week's meetings…")
    meetings = week_meetings(token)
    print(f"   ✓ {len(meetings)} meeting(s)")

    print("✅ Completed tasks…")
    done = completed_tasks_this_week(token)
    print(f"   ✓ {len(done)} task(s) completed")

    print("🧵 Email themes…")
    themes = email_themes(api_key)
    print(f"   ✓ {len(themes)} theme(s)")

    html = build_html(moves, meetings, done, themes, now)

    recipients = [a.strip() for a in (
        os.environ.get("BRIEFING_EMAIL_TO", ""),
        os.environ.get("BRIEFING_EMAIL_GMAIL", ""),
    ) if a.strip()]
    if not recipients:
        Path("friday_wrap.html").write_text(html, encoding="utf-8")
        print("\n⚠️  No BRIEFING_EMAIL_TO set — saved to friday_wrap.html instead")
        return

    subject = "📊 Friday Wrap — " + now.strftime(f"{DAYFMT} %B %Y")
    message = {
        "subject":      subject,
        "body":         {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
    }
    resp = requests.post(
        f"{GRAPH_BASE}/me/sendMail",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"message": message, "saveToSentItems": "false"},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"\n✉️   Friday Wrap sent to: {', '.join(recipients)}")


if __name__ == "__main__":
    main()
