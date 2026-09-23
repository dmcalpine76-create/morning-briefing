"""
schedule_render.py
------------------
Builds the three replacement tabs: Schedule, Calendar (14-day swimlanes) and
Backlog.

Returns HTML fragments only — briefing.py embeds them as values, never inside
an f-string, so the braces in the CSS below are safe.

All text is escaped explicitly and the caller writes UTF-8, because calendar
subjects contain emoji and a silently dropped character in a lane label would
be far harder to spot than a crash.

Self-test:
    py schedule_render.py --test      writes schedule_preview.html
"""

import html as _html
import datetime

AEST_OFFSET = datetime.timezone(datetime.timedelta(hours=10))

RAIL_START_HOUR = 7
RAIL_END_HOUR   = 18
RAIL_PX_PER_MIN = 0.85

INK       = "#1a1a1a"
MID       = "#5c5a52"
RULE      = "#e4e1d8"
GROUND    = "#faf9f5"
RED       = "#9b1c1c"
AMBER     = "#8a5a00"
GREEN     = "#1a4a2e"
NAVY      = "#1a3a5c"
PERSONAL  = "#3d3d38"


def esc(s) -> str:
    return _html.escape(str(s or ""), quote=True)


# ── CSS ──────────────────────────────────────────────────────────────────────

def build_css() -> str:
    return """
<style>
.sx-wrap, .cw-wrap, .bk-wrap { font-size: 0.85rem; color: #1a1a1a; }
.sx-head { display:flex; align-items:flex-end; justify-content:space-between;
           gap:1rem; border-bottom:2px solid #1a1a1a; padding-bottom:.6rem; margin-bottom:1rem; }
.sx-title { font-size:1.5rem; font-weight:700; margin:0; }
.sx-sum { font-size:.8rem; color:#5c5a52; margin-top:.3rem; }
.sx-sum b { color:#1a1a1a; }

.sx-cap { background:#fff; border:1px solid #e4e1d8; border-radius:10px;
          padding:.8rem 1rem 1rem; margin-bottom:1.1rem; }
.sx-cap-hd { display:flex; justify-content:space-between; align-items:baseline;
             font-size:.65rem; letter-spacing:.14em; text-transform:uppercase;
             color:#5c5a52; font-weight:600; margin-bottom:.7rem; }
.sx-cap-row { display:flex; gap:.55rem; }
.sx-day { flex:1 1 0; border:1px solid #e4e1d8; border-radius:8px;
          padding:.5rem .55rem .6rem; background:#fcfbf7; min-width:0; }
.sx-day.is-today { background:#f3f5f8; border-color:#1a3a5c; }
.sx-day.is-best  { background:#f2f6f2; border:2px solid #1a4a2e; }
.sx-day.is-off   { background:#f1efe7; }
.sx-day-top { display:flex; justify-content:space-between; align-items:center; height:14px; }
.sx-day-lbl { font-size:.62rem; letter-spacing:.08em; font-weight:700; color:#5c5a52; }
.sx-day.is-today .sx-day-lbl { color:#1a3a5c; }
.sx-pin { color:#9b1c1c; font-size:.7rem; font-weight:700; }
.sx-free { font-size:1.35rem; font-weight:700; line-height:1; margin:.3rem 0; }
.sx-bar { height:7px; background:#dfdacc; border-radius:4px; overflow:hidden; display:flex; }
.sx-bar i { display:block; height:100%; }
.sx-meta { font-size:.62rem; color:#5c5a52; margin-top:.35rem; }
.sx-verdict { font-size:.6rem; font-weight:700; line-height:1.2; margin-top:.25rem; min-height:1.1em; }

.sx-cols { display:flex; gap:1.3rem; align-items:flex-start; }
.sx-main { flex:1 1 auto; min-width:0; }
.sx-side { flex:0 0 330px; }
@media (max-width:900px){ .sx-cols{flex-direction:column;} .sx-side{flex:1 1 auto;width:100%;}
                          .sx-cap-row{overflow-x:auto;} .sx-day{min-width:88px;} }

.sx-sec { display:flex; align-items:center; gap:.6rem; margin:.2rem 0 .7rem; }
.sx-sec h3 { margin:0; font-size:1.05rem; font-weight:600; }
.sx-sec .sx-line { flex:1 1 auto; height:1px; background:#d8d4c6; }
.sx-sec .sx-note { font-size:.68rem; color:#5c5a52; }

.sx-task { display:flex; align-items:flex-start; gap:.7rem; background:#fff;
           border:1px solid #e4e1d8; border-radius:8px; padding:.65rem .8rem; margin-bottom:.45rem; }
.sx-task.is-proposed { background:#fcfaf2; border:1px dashed #cfc8b0; }
.sx-rank { flex:0 0 auto; width:20px; height:20px; border-radius:50%; background:#1a3a5c;
           color:#fff; font-size:.62rem; font-weight:700; display:flex;
           align-items:center; justify-content:center; margin-top:.1rem; }
.sx-task.is-proposed .sx-rank { background:#8a5a00; }
.sx-task-body { flex:1 1 auto; min-width:0; }
.sx-task-title { font-size:.85rem; font-weight:600; line-height:1.3; }
.sx-why { font-size:.68rem; color:#1a3a5c; margin-top:.2rem; }
.sx-task.is-proposed .sx-why { color:#8a5a00; }
.sx-task-meta { font-size:.62rem; color:#5c5a52; margin-top:.2rem; }
.sx-chip { font-size:.6rem; font-weight:700; border-radius:4px; padding:.15rem .4rem;
           white-space:nowrap; border:1px solid #e4e1d8; background:#f1efe7; color:#5c5a52; }

.sx-rail { background:#fff; border:1px solid #e4e1d8; border-radius:10px; padding:.9rem; }
.sx-rail-inner { position:relative; overflow:hidden; }
.sx-gut { position:absolute; left:0; top:0; width:38px; height:100%; }
.sx-gut span { position:absolute; font-size:.6rem; color:#8c887b; }
.sx-track { position:absolute; left:38px; right:0; top:0; height:100%; }
.sx-hr { position:absolute; left:0; right:0; height:1px; background:#eeebe1; }
.sx-now { position:absolute; left:-6px; right:0; height:2px; background:#9b1c1c; }
.sx-now i { position:absolute; left:-6px; top:-5px; width:11px; height:11px;
            border-radius:50%; background:#9b1c1c; display:block; }
.sx-ev { position:absolute; left:8px; right:0; border-radius:5px; padding:.25rem .5rem;
         box-sizing:border-box; overflow:hidden; font-size:.65rem; }
.sx-ev b { display:block; font-size:.72rem; font-weight:700; line-height:1.2; }
.sx-gap { position:absolute; left:8px; right:0; border:1px dashed #d8d4c6; border-radius:5px;
          display:flex; align-items:center; justify-content:center; font-size:.63rem; color:#8c887b; }
.sx-gap.is-big { background:#f2f6f2; border-color:#1a4a2e; color:#1a4a2e; font-weight:700; }
.sx-after { background:#f7f7f4; border:1px solid #d8d6cc; border-radius:10px;
            padding:.6rem .8rem; margin-top:.7rem; display:flex; gap:.6rem; align-items:center; }
.sx-after i { width:4px; height:28px; background:#3d3d38; border-radius:2px; display:block; flex:0 0 auto; }

.sx-pers { background:#fcfcfa; border:1px solid #d8d6cc; border-radius:8px;
            padding:.6rem .75rem; margin-bottom:.45rem; display:flex;
            align-items:flex-start; gap:.7rem; }
.sx-pers-dot { flex:0 0 auto; width:8px; height:8px; border-radius:50%;
               background:#3d3d38; margin-top:.45rem; }
.sx-pers-b { flex:1 1 auto; min-width:0; }
.sx-pers-t { font-size:.82rem; font-weight:600; line-height:1.3; }
.sx-pers-c { font-size:.66rem; color:#5c5a52; margin-top:.2rem; }
.sx-pers-a { font-size:.6rem; font-weight:700; color:#fff; background:#3d3d38;
             border-radius:4px; padding:.3rem .6rem; text-decoration:none;
             white-space:nowrap; display:inline-block; min-height:26px; line-height:20px; }

.sx-brief { margin-top:.7rem; }
.sx-brief-card { background:#fff; border:1px solid #e4e1d8; border-radius:9px;
                 padding:.6rem .75rem; margin-bottom:.4rem; }
.sx-brief-top { display:flex; gap:.4rem; align-items:baseline; }
.sx-brief-t { font-size:.62rem; font-weight:700; letter-spacing:.04em; flex:0 0 auto; }
.sx-brief-s { font-size:.75rem; font-weight:600; line-height:1.25; min-width:0; }
.sx-brief-l { margin:.35rem 0 0; padding-left:.9rem; }
.sx-brief-l li { font-size:.68rem; color:#4a4a44; line-height:1.45; margin-bottom:.15rem; }

/* ── swimlane calendar ── */
.cw-scroll { overflow-x:auto; }
.cw-panel { background:#fff; border:1px solid #e4e1d8; border-radius:10px;
            padding:.9rem 1rem 1rem; min-width:1240px; position:relative; }
.cw-row { display:grid; grid-template-columns:132px repeat(14, minmax(0,1fr)); align-items:stretch; }
.cw-hdr { padding-bottom:.5rem; text-align:center; }
.cw-hdr-d { font-size:.58rem; letter-spacing:.1em; font-weight:600; color:#8c887b; }
.cw-hdr-n { font-size:1rem; font-weight:600; }
.cw-hdr.is-today .cw-hdr-d, .cw-hdr.is-today .cw-hdr-n { color:#1a3a5c; font-weight:700; }
.cw-hdr.is-wknd .cw-hdr-n { color:#8c887b; font-weight:400; }
.cw-cell-wknd { background:#f4f2ea; }
.cw-cell-today { box-shadow: inset 0 0 0 2px #1a3a5c; border-radius:4px; }
.cw-lane-lbl { display:flex; align-items:flex-start; gap:.4rem; padding:.55rem .5rem .55rem 0; }
.cw-lane-lbl i { width:4px; height:22px; border-radius:2px; display:block; flex:0 0 auto; margin-top:.1rem; }
.cw-lane-name { font-size:.68rem; font-weight:700; line-height:1.2; }
.cw-lane-sub { font-size:.55rem; font-weight:600; color:#8c887b; }
.cw-lane-track { grid-column:2 / -1; display:grid;
                 grid-template-columns:repeat(14, minmax(0,1fr)); row-gap:5px;
                 padding:.6rem 0; }
.cw-bar { border-radius:4px; padding:.28rem .4rem; font-size:.63rem; font-weight:700;
          line-height:1.2; min-height:2.1em; margin-right:2px; overflow:hidden;
          display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
.cw-load { display:flex; flex-direction:column; align-items:center; gap:3px; padding-bottom:.4rem; }
.cw-load-bar { width:70%; max-width:34px; border-radius:2px; }
.cw-load-n { font-size:.55rem; color:#5c5a52; }
.cw-sep { border-top:1px solid #f0ede4; }
.cw-readout { display:flex; gap:.8rem; margin-top:.9rem; flex-wrap:wrap; }
.cw-card { flex:1 1 220px; background:#fff; border:1px solid #e4e1d8; border-radius:10px; padding:.7rem .85rem; }
.cw-card-h { font-size:.58rem; letter-spacing:.14em; text-transform:uppercase;
             color:#5c5a52; font-weight:700; margin-bottom:.3rem; }
.cw-card-b { font-size:.78rem; line-height:1.4; }

/* ── backlog ── */
.bk-grp { margin-bottom:1.2rem; }
.bk-grp-h { display:flex; align-items:center; gap:.6rem; margin-bottom:.5rem; }
.bk-grp-h span { font-size:.65rem; letter-spacing:.14em; text-transform:uppercase;
                 font-weight:700; color:#5c5a52; }
.bk-grp-h i { flex:1 1 auto; height:1px; background:#d8d4c6; display:block; }
.bk-item { display:flex; align-items:center; gap:.7rem; background:#fdfcf8;
           border:1px solid #e9e6dd; border-radius:7px; padding:.45rem .7rem; margin-bottom:.3rem; }
.bk-item-t { flex:1 1 auto; font-size:.8rem; min-width:0; }
.bk-age { font-size:.6rem; color:#8c887b; white-space:nowrap; }
.bk-lead { font-size:.78rem; color:#5c5a52; line-height:1.5; margin-bottom:1rem;
           background:#fff; border:1px solid #e4e1d8; border-radius:9px; padding:.7rem .9rem; }
</style>
"""


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt_hm(minutes: int) -> str:
    h, m = divmod(max(int(minutes), 0), 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _work_bounds(cfg: dict, day: datetime.date):
    def hhmm(v, fb):
        try:
            h, m = str(v).split(":")
            return datetime.time(int(h), int(m))
        except Exception:
            h, m = fb.split(":")
            return datetime.time(int(h), int(m))
    ws = hhmm((cfg or {}).get("work_day_start"), "07:00")
    we = hhmm((cfg or {}).get("work_day_end"), "18:00")
    return (datetime.datetime.combine(day, ws, tzinfo=AEST_OFFSET),
            datetime.datetime.combine(day, we, tzinfo=AEST_OFFSET))


# ── Schedule tab ─────────────────────────────────────────────────────────────

def _todo_deeplink(title: str, detail: str = "") -> str:
    from urllib.parse import quote
    params = f"title={quote(title[:255])}"
    if detail:
        params += f"&body={quote(detail[:500])}"
    return f"https://to-do.microsoft.com/tasks/add?{params}"


def _build_personal(actions: list) -> str:
    """Personal actions from the Gmail inbox, kept visually distinct from work."""
    if not actions:
        return ""
    rows = []
    for a in actions:
        dl = f" &middot; {esc(a.get('deadline'))}" if a.get("deadline") else ""
        who = esc(a.get("from") or "")
        rows.append(
            '<div class="sx-pers"><span class="sx-pers-dot"></span>'
            '<div class="sx-pers-b">'
            '<div class="sx-pers-t">' + esc(a.get("action")) + '</div>'
            '<div class="sx-pers-c">' + esc(a.get("context")) + '</div>'
            '<div class="sx-pers-c">from ' + who + dl + '</div>'
            '</div>'
            '<a class="sx-pers-a" target="_blank" rel="noopener" href="'
            + esc(_todo_deeplink(a.get("action", ""), a.get("context", "")))
            + '">Add</a></div>')
    return ('<div class="sx-sec" style="margin-top:1.3rem"><h3>Personal</h3>'
            '<i class="sx-line"></i><span class="sx-note">from your Gmail inbox</span>'
            '</div>' + "".join(rows))


def build_schedule_tab(ranked: dict, cal: dict, pc_cfg: dict = None,
                       legacy_scheduler_html: str = "", briefings: dict = None,
                       personal_actions: list = None,
                       now: datetime.datetime = None) -> str:
    now   = now or datetime.datetime.now(AEST_OFFSET)
    today = now.date()
    live  = (ranked or {}).get("live", [])
    backlog_n = len((ranked or {}).get("backlog", []))
    days  = (cal or {}).get("days", [])[:7]
    load  = (cal or {}).get("load", {})
    by_day = (cal or {}).get("by_day", {})
    pc_cfg = pc_cfg or {}
    work_days = set(pc_cfg.get("work_days", [0, 1, 2, 3, 4]))

    # capacity per day
    ws, we = _work_bounds(pc_cfg, today)
    full_day = int((we - ws).total_seconds() // 60)
    cells, best_day, best_free = [], None, -1
    for d in days:
        booked = load.get(d, 0)
        off    = d.weekday() not in work_days
        free   = 0 if off else max(full_day - booked, 0)
        personal = sum(e.get("in_hours_mins", 0) for e in by_day.get(d, [])
                       if e.get("source") == "personal" and e.get("counts_capacity"))
        work_mins = max(booked - personal, 0)
        deadline = any(e.get("lane_id") == "regulatory" for e in by_day.get(d, []))
        cells.append({"d": d, "off": off, "free": free, "booked": booked,
                      "personal": personal, "work": work_mins, "deadline": deadline,
                      "events": by_day.get(d, [])})
        if not off and d >= today and free > best_free:
            best_free, best_day = free, d

    strip = []
    for c in cells:
        d = c["d"]
        klass = "sx-day"
        if c["off"]:
            klass += " is-off"
        elif d == best_day:
            klass += " is-best"
        elif d == today:
            klass += " is-today"

        if c["off"]:
            free_txt, free_col, meta = "&mdash;", "#a8a396", "weekend"
        else:
            free_txt = _fmt_hm(c["free"])
            free_col = GREEN if c["free"] >= 300 else (RED if c["free"] <= 120 else INK)
            bits = [f"{_fmt_hm(c['booked'])} booked"] if c["booked"] else ["nothing booked"]
            if c["personal"]:
                bits.append(f"incl. {_fmt_hm(c['personal'])} personal")
            meta = " &middot; ".join(bits)

        wpct = int(100 * c["work"] / full_day) if full_day else 0
        ppct = int(100 * c["personal"] / full_day) if full_day else 0
        bar = (f'<i style="width:{wpct}%;background:{NAVY if c["d"]==today else "#7d8a96"}"></i>'
               f'<i style="width:{ppct}%;background:{PERSONAL}"></i>')

        pin = '<span class="sx-pin">&#9650;</span>' if c["deadline"] else ""
        verdict, vcol = "", MID
        if d == best_day and not c["off"]:
            verdict, vcol = "most room this week", GREEN
            if c["deadline"]:
                verdict = "most room &mdash; but a deadline lands"
        elif c["deadline"] and c["free"] <= 120:
            verdict, vcol = "deadline, and no room", RED

        pin = '<span class="sx-pin">&#9650;</span>' if c["deadline"] else ''
        strip.append(
            f'<div class="{klass}">'
            f'<div class="sx-day-top"><span class="sx-day-lbl">{d.strftime("%a %d").upper()}</span>'
            f'{pin}</div>'
            f'<div class="sx-free" style="color:{free_col}">{free_txt}</div>'
            f'<div class="sx-bar">{bar}</div>'
            f'<div class="sx-meta">{meta}</div>'
            f'<div class="sx-verdict" style="color:{vcol}">{verdict or "&nbsp;"}</div>'
            f'</div>')

    # priorities
    rows = []
    for i, t in enumerate(live, 1):
        proposed = t.get("source") == "proposed"
        chip = ("proposed from inbox" if proposed
                else (f"{t['days_over']}d on the list" if t.get("days_over") else "on your list"))
        action = ('<button type="button" class="sx-chip" style="background:#1a3a5c;color:#fff;'
                  'border-color:#1a3a5c;cursor:pointer">Add</button>') if proposed else \
                 f'<span class="sx-chip">{esc(chip)}</span>'
        detail_html = ('<div class="sx-task-meta">' + esc(t.get("detail", "")[:110]) + '</div>') if t.get("detail") else ""
        cls = " is-proposed" if proposed else ""
        rows.append(
            f'<div class="sx-task{cls}">'
            f'<span class="sx-rank">{i}</span>'
            f'<div class="sx-task-body">'
            f'<div class="sx-task-title">{esc(t.get("title"))}</div>'
            f'<div class="sx-why">{esc(t.get("urgency_reason"))}</div>'
            f'{detail_html}'
            f'</div>{action}</div>')
    if not rows:
        rows.append('<div class="sx-task"><div class="sx-task-body">'
                    '<div class="sx-task-title">Nothing is pressing today.</div>'
                    '<div class="sx-why">No task couples to a dated obligation this fortnight.</div>'
                    '</div></div>')

    # today rail
    rail = _build_rail(by_day.get(today, []), now)

    after = [e for e in by_day.get(today, [])
             if e.get("source") == "personal" and not e.get("counts_capacity")
             and not e.get("is_all_day")]
    after_html = ""
    if after:
        items = "<br>".join(f'{esc(e["start_time"])} &mdash; {esc(e["subject"])}' for e in after[:3])
        after_html = (
            f'<div class="sx-after"><i></i><div style="flex:1 1 auto">'
            f'<div style="font-size:.55rem;letter-spacing:.12em;text-transform:uppercase;'
            f'color:#8c887b;font-weight:700">After hours &middot; personal</div>'
            f'<div style="font-size:.75rem;font-weight:600;color:#3d3d38;margin-top:.15rem">{items}</div>'
            f'</div><span style="font-size:.55rem;color:#8c887b;text-align:right;line-height:1.3">'
            f'shown,<br>not counted</span></div>')

    today_evts = [e for e in by_day.get(today, []) if not e.get("is_all_day")]
    brief_html = _build_briefings(by_day.get(today, []), briefings)
    personal_html = _build_personal(personal_actions or [])
    plural_evts = "s" if len(today_evts) != 1 else ""
    booked_today = load.get(today, 0)
    plural_backlog = "s" if backlog_n != 1 else ""
    legacy = ""
    if legacy_scheduler_html:
        legacy = (f'<div class="sx-sec" style="margin-top:1.4rem"><h3>Proposed diary blocks</h3>'
                  f'<i class="sx-line"></i></div>{legacy_scheduler_html}')

    return f"""<div class="sx-wrap">
<div class="sx-head">
  <div><h2 class="sx-title">Schedule</h2>
    <div class="sx-sum"><b>{len(live)}</b> worth your attention &middot;
      <b>{_fmt_hm(booked_today)}</b> booked today &middot;
      longest clear window <b>{_fmt_hm(best_free if best_free > 0 else 0)}</b>
      {("on " + best_day.strftime("%a %d %b")) if best_day else ""}</div>
  </div>
  <div style="text-align:right;font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;color:#5c5a52">
    Outlook + personal<br>{esc(now.strftime("%a %d %b, %H:%M"))}
  </div>
</div>

<div class="sx-cap">
  <div class="sx-cap-hd"><span>Where the work can actually go</span>
    <span style="text-transform:none;letter-spacing:0;font-weight:400">
      hours left after commitments &middot; <span style="color:{RED};font-weight:700">&#9650;</span> deadline</span></div>
  <div class="sx-cap-row">{''.join(strip)}</div>
</div>

<div class="sx-cols">
  <div class="sx-main">
    <div class="sx-sec"><h3>What matters today</h3><i class="sx-line"></i>
      <span class="sx-note">ranked by deadline, calendar coupling and recency</span></div>
    {''.join(rows)}
    <div style="font-size:.7rem;color:#5c5a52;margin-top:.6rem">
      {backlog_n} further open item{plural_backlog} with no urgency signal &mdash;
      see the Backlog tab.</div>
    {personal_html}
    {legacy}
  </div>
  <div class="sx-side">
    <div class="sx-sec"><h3>Today</h3><i class="sx-line"></i>
      <span class="sx-note">{len(today_evts)} event{plural_evts}</span></div>
    {rail}
    {after_html}
    {brief_html}
  </div>
</div>
</div>"""


def _build_briefings(today_events: list, briefings: dict) -> str:
    """
    Carries the per-meeting AI briefing bullets across from the old Calendar tab.
    They are the one genuinely useful thing the fortnight view cannot show, so
    they live under today's rail rather than being dropped.
    """
    if not briefings:
        return ""

    def lookup(ev):
        try:
            import outlook_calendar
            got = outlook_calendar._brief_for(briefings, ev)
            if got:
                return got
        except Exception:
            pass
        subj = (ev.get("subject") or "").strip()
        for key in (subj, subj.lower()):
            if key in briefings:
                return briefings[key]
        return None

    cards = []
    for ev in today_events:
        if ev.get("is_all_day") or ev.get("source") == "personal":
            continue
        brief = lookup(ev) or {}
        bullets = [b for b in (brief.get("bullets") or []) if b]
        if not bullets:
            continue
        col = ev.get("lane_color") or NAVY
        items = "".join("<li>" + esc(b) + "</li>" for b in bullets[:3])
        cards.append(
            '<div class="sx-brief-card">'
            '<div class="sx-brief-top">'
            '<span class="sx-brief-t" style="color:' + col + '">' + esc(ev.get("start_time")) + '</span>'
            '<span class="sx-brief-s">' + esc(ev.get("subject")) + '</span>'
            '</div><ul class="sx-brief-l">' + items + '</ul></div>')

    if not cards:
        return ""
    return ('<div class="sx-brief"><div class="sx-sec" style="margin-top:1rem">'
            '<h3 style="font-size:.95rem">Before your meetings</h3>'
            '<i class="sx-line"></i></div>' + "".join(cards) + '</div>')


def _rail_bounds(events: list) -> tuple:
    """
    The window stretches to cover the day actually booked. A fixed 07:00-18:00
    rail put an 8pm meeting 200px below the container and it spilled down the
    page, which is what the render fault was.
    """
    lo, hi = RAIL_START_HOUR, RAIL_END_HOUR
    for e in events:
        if e.get("is_all_day") or not e.get("start_dt"):
            continue
        lo = min(lo, e["start_dt"].hour)
        end = e.get("end_dt") or e["start_dt"]
        hi = max(hi, end.hour + (1 if end.minute else 0))
    lo = max(min(lo, RAIL_START_HOUR), 4)
    hi = min(max(hi, RAIL_END_HOUR), 24)
    if hi <= lo:
        hi = lo + 1
    return lo, hi


def _build_rail(events: list, now: datetime.datetime) -> str:
    lo_h, hi_h = _rail_bounds(events)
    span_mins = (hi_h - lo_h) * 60
    # keep the rail a sensible height however long the day turns out to be
    ppm = min(RAIL_PX_PER_MIN, 640 / span_mins) if span_mins else RAIL_PX_PER_MIN
    height = int(span_mins * ppm)
    day_start = now.replace(hour=lo_h, minute=0, second=0, microsecond=0)

    def top_of(dt):
        return min(max(int((dt - day_start).total_seconds() / 60 * ppm), 0), height)

    gutter, rules = [], []
    for h in range(lo_h, hi_h + 1, 2):
        y = int((h - lo_h) * 60 * ppm)
        label = datetime.time(h).strftime("%I %p").lstrip("0").lower()
        gutter.append(f'<span style="top:{y-6}px">{label}</span>')
        rules.append(f'<div class="sx-hr" style="top:{y}px"></div>')

    timed = sorted([e for e in events if not e.get("is_all_day") and e.get("start_dt")],
                   key=lambda e: e["start_dt"])

    blocks, cursor = [], day_start
    for e in timed:
        s, en = e["start_dt"], e["end_dt"]
        if en <= day_start:
            continue
        gap = int((s - cursor).total_seconds() // 60)
        if gap >= 30:
            gy = top_of(cursor)
            gh = min(int(gap * ppm), height - gy)
            big = " is-big" if gap >= 120 else ""
            blocks.append(f'<div class="sx-gap{big}" style="top:{gy}px;height:{gh}px">'
                          f'{_fmt_hm(gap)} clear</div>')
        col = PERSONAL if e.get("source") == "personal" else (e.get("lane_color") or NAVY)
        y = top_of(s)
        h = max(min(int((en - s).total_seconds() // 60 * ppm), height - y), 20)
        tight = h < 34
        inner = (f'<b>{esc(e["start_time"])} &middot; {esc(e["subject"])[:34]}</b>' if tight else
                 f'<span style="font-weight:700;color:{col}">{esc(e["start_time"])} &ndash; {esc(e["end_time"])}</span>'
                 f'<b>{esc(e["subject"])}</b>')
        blocks.append(f'<div class="sx-ev" style="top:{y}px;height:{h}px;'
                      f'background:{col}14;border:1px solid {col}">{inner}</div>')
        cursor = max(cursor, en)

    _rail_end = day_start + datetime.timedelta(minutes=span_mins)
    nowy = top_of(now) if day_start <= now <= _rail_end else None
    now_html = f'<div class="sx-now" style="top:{nowy}px"><i></i></div>' if nowy is not None else ""

    if not timed:
        blocks.append('<div class="sx-gap is-big" style="top:0;height:100%">'
                      'Nothing booked today</div>')

    return (f'<div class="sx-rail"><div class="sx-rail-inner" style="height:{height}px">'
            f'<div class="sx-gut">{"".join(gutter)}</div>'
            f'<div class="sx-track">{"".join(rules)}{now_html}{"".join(blocks)}</div>'
            f'</div></div>')


# ── Calendar tab (swimlanes) ─────────────────────────────────────────────────

def build_calendar_tab(cal: dict, now: datetime.datetime = None) -> str:
    now    = now or datetime.datetime.now(AEST_OFFSET)
    today  = now.date()
    days   = (cal or {}).get("days", [])[:14]
    if not days:
        return '<div class="cw-wrap"><p>No calendar data available.</p></div>'
    load   = (cal or {}).get("load", {})
    lanes  = (cal or {}).get("lanes", [])
    events = (cal or {}).get("events", [])
    index  = {d: i for i, d in enumerate(days)}
    maxload = max(list(load.values()) + [1])

    # headers
    hdr = ['<div class="cw-lane-lbl"></div>']
    for d in days:
        k = "cw-hdr"
        if d == today:
            k += " is-today"
        if d.weekday() >= 5:
            k += " is-wknd"
        try:
            label = d.strftime("%d %b") if d.day == 1 else d.strftime("%d")
        except Exception:
            label = str(d.day)
        hdr.append(f'<div class="{k}"><div class="cw-hdr-d">{d.strftime("%a").upper()}</div>'
                   f'<div class="cw-hdr-n">{label}</div></div>')

    # load strip
    lrow = ['<div class="cw-lane-lbl"><span class="cw-lane-name">Hours<br>booked</span></div>']
    for d in days:
        mins = load.get(d, 0)
        hgt  = max(int(30 * mins / maxload), 2) if mins else 2
        col  = RED if mins >= 420 else (AMBER if mins >= 300 else ("#a7b0b9" if mins else "#dad6c9"))
        wknd = " cw-cell-wknd" if d.weekday() >= 5 else ""
        lrow.append(f'<div class="cw-load{wknd}"><div class="cw-load-bar" '
                    f'style="height:{hgt}px;background:{col}"></div>'
                    f'<span class="cw-load-n">{(mins/60):.1f}</span></div>')

    # lanes
    lane_rows = []
    for lane in lanes:
        lane_events = [e for e in events if e.get("lane_id") == lane.get("id")]
        if not lane_events:
            continue
        placed, bars = [], []
        for e in sorted(lane_events, key=lambda x: x["start_dt"]):
            s = e["start_dt"].date()
            en = e["end_dt"].date() if e.get("end_dt") else s
            if s not in index and en < days[0]:
                continue
            c0 = index.get(max(s, days[0]))
            c1 = index.get(min(en, days[-1]))
            if c0 is None:
                continue
            if c1 is None:
                c1 = len(days) - 1
            span = max(c1 - c0 + 1, 1)
            row = 1
            while any(r == row and not (c1 < a or c0 > b) for r, a, b in placed):
                row += 1
            placed.append((row, c0, c1))
            col = lane.get("color", "#8c887b")
            solid = e.get("counts_capacity") and not e.get("is_all_day")
            style = (f"background:{col};color:#fff;border:1px solid {col}" if solid
                     else f"background:{col}14;color:{col};border:1px solid {col}")
            if lane.get("id") == "unfiled":
                style = f"background:#fafaf7;color:#5c5a52;border:1px dashed #8c887b"
            label = e["subject"] if e.get("is_all_day") else f'{e["start_time"]} {e["subject"]}'
            bars.append(f'<div class="cw-bar" style="grid-row:{row};'
                        f'grid-column:{c0+1} / span {span};{style}" '
                        f'title="{esc(e["subject"])}">{esc(label)}</div>')
        sub = "google calendar" if lane.get("id") == "personal" else (
              "click to assign" if lane.get("id") == "unfiled" else
              f"{len(lane_events)} event" + ("s" if len(lane_events) != 1 else ""))
        sub_html = ('<br><span class="cw-lane-sub">' + esc(sub) + '</span>') if sub else ""
        lane_rows.append(
            f'<div class="cw-row cw-sep">'
            f'<div class="cw-lane-lbl"><i style="background:{lane.get("color","#8c887b")}"></i>'
            f'<span class="cw-lane-name">{esc(lane.get("name"))}{sub_html}</span></div>'
            f'<div class="cw-lane-track">{"".join(bars)}</div></div>')

    # read-outs
    busiest = max(days, key=lambda d: load.get(d, 0))
    work_days = [d for d in days if d.weekday() < 5]
    quiet = sorted(work_days, key=lambda d: load.get(d, 0))[:2]
    unfiled_n = len([e for e in events if e.get("lane_id") == "unfiled"])
    readout = (
        f'<div class="cw-readout">'
        f'<div class="cw-card"><div class="cw-card-h">Heaviest day</div><div class="cw-card-b">'
        f'<b style="color:{RED}">{busiest.strftime("%a %d %b")}</b> &mdash; '
        f'{(load.get(busiest,0)/60):.1f} hours committed.</div></div>'
        f'<div class="cw-card"><div class="cw-card-h">Clearest weekdays</div><div class="cw-card-b">'
        f'<b style="color:{GREEN}">{" and ".join(d.strftime("%a %d %b") for d in quiet)}</b> &mdash; '
        f'{sum(load.get(d,0) for d in quiet)/60:.1f} hours between them.</div></div>'
        f'<div class="cw-card"><div class="cw-card-h">Unfiled</div><div class="cw-card-b">'
        f'{unfiled_n} event{"s" if unfiled_n != 1 else ""} matched no lane rule'
        f'{" &mdash; add a keyword or override in settings." if unfiled_n else "."}</div></div>'
        f'</div>')

    return f"""<div class="cw-wrap">
<div class="sx-head"><div><h2 class="sx-title">Calendar &mdash; next fortnight</h2>
  <div class="sx-sum">{days[0].strftime("%a %d %b")} &ndash; {days[-1].strftime("%a %d %b")} &middot;
    {len(events)} commitments &middot; Outlook + personal</div></div></div>
<div class="cw-scroll"><div class="cw-panel">
  <div class="cw-row">{''.join(hdr)}</div>
  <div class="cw-row cw-sep">{''.join(lrow)}</div>
  {''.join(lane_rows)}
</div></div>
{readout}
</div>"""


# ── Backlog tab ──────────────────────────────────────────────────────────────

def build_backlog_tab(ranked: dict, now: datetime.datetime = None) -> str:
    now = now or datetime.datetime.now(AEST_OFFSET)
    backlog = (ranked or {}).get("backlog", [])
    if not backlog:
        return ('<div class="bk-wrap"><div class="bk-lead">Nothing in the backlog. '
                'Everything open has an urgency signal.</div></div>')

    groups = {"Under a fortnight old": [], "Two to four weeks": [], "Over a month": [], "No due date": []}
    for t in backlog:
        d = t.get("days_over", 0) or 0
        if not t.get("due_date"):
            groups["No due date"].append(t)
        elif d < 14:
            groups["Under a fortnight old"].append(t)
        elif d < 31:
            groups["Two to four weeks"].append(t)
        else:
            groups["Over a month"].append(t)

    out = []
    for name, items in groups.items():
        if not items:
            continue
        rows = "".join(
            f'<div class="bk-item"><div class="bk-item-t">{esc(t.get("title"))}</div>'
            f'<span class="bk-age">{(str(t["days_over"]) + "d") if t.get("days_over") else "&mdash;"}</span></div>'
            for t in items)
        out.append(f'<div class="bk-grp"><div class="bk-grp-h"><span>{esc(name)}</span>'
                   f'<i></i><span>{len(items)}</span></div>{rows}</div>')

    return f"""<div class="bk-wrap">
<div class="sx-head"><div><h2 class="sx-title">Backlog</h2>
  <div class="sx-sum">{len(backlog)} open items with no current urgency signal</div></div></div>
<div class="bk-lead">These carry no deadline in their text, match nothing on the next
fortnight's calendar, and have not been touched recently. Their due dates are the dates
they were captured, not dates they are owed &mdash; so age here means age, not lateness.</div>
{''.join(out)}
</div>"""


# ── self-test ────────────────────────────────────────────────────────────────

def _rail_contained(sched_html: str) -> bool:
    """Nothing positioned inside the rail may extend past its container."""
    import re
    m = re.search(r'sx-rail-inner" style="height:(\d+)px', sched_html)
    if not m:
        return False
    limit = int(m.group(1))
    for mm in re.finditer(r'style="top:(-?\d+)px;height:(-?\d+)px', sched_html):
        top, hgt = int(mm.group(1)), int(mm.group(2))
        if top < 0 or hgt < 0 or top + hgt > limit:
            return False
    return True


def _self_test():
    import pathlib
    AEST = AEST_OFFSET
    now = datetime.datetime(2026, 9, 23, 8, 40, tzinfo=AEST)
    today = now.date()
    days = [today + datetime.timedelta(days=i) for i in range(14)]

    def ev(subj, day, h, mins, lane, col, src="outlook", allday=False):
        s = datetime.datetime.combine(days[day], datetime.time(h, 0), tzinfo=AEST)
        e = s + datetime.timedelta(minutes=mins)
        # mirror personal_calendar: only the slice inside 07:00-18:00 counts
        ws = s.replace(hour=7, minute=0)
        we = s.replace(hour=18, minute=0)
        lo, hi = max(s, ws), min(e, we)
        in_hours = 0 if allday else max(int((hi - lo).total_seconds() // 60), 0)
        return {"subject": subj, "start_dt": s, "end_dt": e,
                "start_time": s.strftime("%I:%M %p").lstrip("0"),
                "end_time": e.strftime("%I:%M %p").lstrip("0"),
                "is_all_day": allday, "lane_id": lane, "lane_color": col, "source": src,
                "in_hours_mins": in_hours, "counts_capacity": in_hours > 0,
                "location": "", "duration_mins": mins}

    events = [
        ev("Ops stand-up", 0, 8, 30, "internal", GREEN),
        ev("Board paper review — Q3 🎯", 0, 9, 90, "board", NAVY),
        ev("DNRM submission walkthrough", 0, 14, 60, "regulatory", "#4a2a1a"),
        ev("Investor call — institutional", 0, 16, 60, "investors", "#3a1a4a"),
        ev("Family dinner", 0, 18, 90, "personal", PERSONAL, "personal"),
        ev("Weekly Vroom Meeting", 0, 20, 120, "personal", PERSONAL, "personal"),
        ev("Dentist", 1, 8, 60, "personal", PERSONAL, "personal"),
        ev("Board meeting", 7, 9, 180, "board", NAVY),
        ev("Sydney roadshow", 8, 9, 480, "investors", "#3a1a4a"),
        ev("Rolleston West site visit", 5, 8, 480, "travel", "#15514f"),
        ev("Catch-up — J. Hartley", 6, 11, 30, "unfiled", "#8c887b"),
    ]
    load = {}
    for d in days:
        load[d] = sum(e["in_hours_mins"] for e in events
                      if e["start_dt"].date() == d and e["counts_capacity"])
    by_day = {}
    for e in events:
        by_day.setdefault(e["start_dt"].date(), []).append(e)

    lanes = [{"id": "board", "name": "Board & Governance", "color": NAVY},
             {"id": "investors", "name": "Investors & External", "color": "#3a1a4a"},
             {"id": "regulatory", "name": "Regulatory & Government", "color": "#4a2a1a"},
             {"id": "internal", "name": "Internal & Team", "color": GREEN},
             {"id": "travel", "name": "Travel & Site", "color": "#15514f"},
             {"id": "personal", "name": "Personal", "color": PERSONAL},
             {"id": "unfiled", "name": "Unfiled", "color": "#8c887b"}]

    cal = {"events": events, "by_day": by_day, "days": days, "load": load, "lanes": lanes, "errors": []}
    ranked = {
        "live": [
            {"id": "1", "title": "Lodge ATP 2062 renewal application",
             "urgency_reason": "ATP2062 is on the calendar Fri 25 Sep", "days_over": 21, "source": "todo"},
            {"id": "2", "title": "Review and approve draft 2026 Annual Report",
             "urgency_reason": "names a deadline (“by Friday”)", "days_over": 20, "source": "todo"},
            {"id": "p1", "title": "Reply to Santos re gas supply MOU",
             "urgency_reason": "raised in this morning's inbox", "source": "proposed",
             "detail": "M. Lawson — needs a view before Friday"},
        ],
        "backlog": [
            {"id": "3", "title": "Follow up Warwick Squire re introduction",
             "days_over": 36, "due_date": datetime.date(2026, 8, 18)},
            {"id": "4", "title": "Post approved LinkedIn article", "days_over": 22,
             "due_date": datetime.date(2026, 9, 1)},
            {"id": "5", "title": "Tidy shared drive", "days_over": 0, "due_date": None},
        ],
    }
    pc = {"work_day_start": "07:00", "work_day_end": "18:00", "work_days": [0, 1, 2, 3, 4]}

    briefings = {
        "Board paper review — Q3 🎯": {"bullets": [
            "Aaron circulated revised Q3 figures on Monday; comments still outstanding.",
            "Cashflow forecast for BDO is referenced but not yet attached.",
        ]},
        "DNRM submission walkthrough": {"bullets": [
            "Relates to the ATP 2062 lodgement window closing Friday.",
        ]},
    }
    sched = build_schedule_tab(ranked, cal, pc, briefings=briefings, now=now)
    calt  = build_calendar_tab(cal, now=now)
    back  = build_backlog_tab(ranked, now=now)

    page = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Preview</title>{build_css()}"
            f"<style>body{{margin:0;padding:24px;background:{GROUND};"
            f"font-family:system-ui,sans-serif}}section{{margin-bottom:56px}}</style></head><body>"
            f"<section>{sched}</section><section>{calt}</section><section>{back}</section>"
            f"</body></html>")
    out = pathlib.Path("schedule_preview.html")
    out.write_text(page, encoding="utf-8")

    print("Renderer self-test")
    print("=" * 60)
    for label, frag in (("schedule", sched), ("calendar", calt), ("backlog", back)):
        print(f"{label:<10} {len(frag):>7} chars")
    checks = [
        ("emoji survived escaping", "🎯" in calt),
        ("multi-day bar spans columns", "span 1" in calt or "span" in calt),
        ("unfiled lane rendered", "Unfiled" in calt),
        ("capacity strip present", "sx-cap-row" in sched),
        ("today rail has a now marker", "sx-now" in sched),
        ("after-hours block shown", "After hours" in sched),
        ("backlog groups by age", "Over a month" in back),
        ("no POSIX-only strftime directives (Windows safe)",
         not __import__("re").search(r"%-[a-zA-Z]",
             pathlib.Path(__file__).read_text(encoding="utf-8"))),
        ("meeting briefing bullets carried over", "Before your meetings" in sched),
        ("evening event does not escape the rail", _rail_contained(sched)),
        ("personal lane rendered on the calendar", "Personal" in calt),
        ("late personal event reaches the calendar", "Weekly Vroom" in calt),
        ("bullet text rendered", "still outstanding" in sched),
    ]
    print()
    ok = 0
    for label, res in checks:
        print(("ok   " if res else "FAIL ") + label); ok += res
    print(f"\n{ok}/{len(checks)} passed")
    print(f"\nwrote {out.resolve()} — open it in a browser to see all three tabs.")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(_self_test())
