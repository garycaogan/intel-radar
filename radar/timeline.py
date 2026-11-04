"""热点时间线 (site/timeline/): the big AI and AI-video events half a year either side of today.

Events are hand-kept in timeline/events.json (RADAR_TIMELINE_DIR), each with at least one source and, if you
add one, a picture of your own in timeline/images/. The two lanes (AI video, AI) each get their own section on
the page: the next big ones, the year as month cards you swipe sideways, and a month-by-month list with today's
line between the past (folded) and the coming months. A YouTube history section follows (timeline/youtube_hot.json
joined with the Google Trends curves in timeline/youtube_trends.json). The window moves with the render date.
The page itself is put together in radar/render.py (timeline_page, write_timeline); this module holds the data
and the HTML pieces.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

LANES = {"ai": "AI", "video": "AI 视频"}
STATUS = {"done": "已发生", "scheduled": "已定档", "expected": "预计", "unconfirmed": "待证实"}
HEAT = {3: "大热点", 2: "圈内大事", 1: "值得知道"}
WEEK = "一二三四五六日"
QUARTER = "一二三四"
BJ = dt.timezone(dt.timedelta(hours=8))
IMG_DIR = "img/"                 # pictures, relative to timeline/index.html
IMG_PUBLIC = "timeline/img/"     # the same pictures, relative to the site root (data/timeline.json)
_FILE_NAME = re.compile(r"[\w][\w.-]*")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def today_bj() -> dt.date:
    return dt.datetime.now(BJ).date()


# ---------- data ----------

def parse_date(text: str) -> tuple[dt.date, dt.date, str]:
    """'2026-11-19' -> that day; '2026-11' -> the month; '2027-Q1' -> the quarter. Returns (first, last, precision)."""
    s = str(text).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        d = dt.date(int(m[1]), int(m[2]), int(m[3]))
        return d, d, "day"
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        first = dt.date(int(m[1]), int(m[2]), 1)
        return first, month_end(first), "month"
    m = re.fullmatch(r"(\d{4})-Q([1-4])", s)
    if m:
        first = dt.date(int(m[1]), 3 * int(m[2]) - 2, 1)
        return first, month_end(add_months(first, 2)), "quarter"
    raise ValueError(f"date {s!r} is not YYYY-MM-DD, YYYY-MM or YYYY-Qn")


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def month_end(d: dt.date) -> dt.date:
    return add_months(d, 1) - dt.timedelta(days=1)


def window(today: dt.date) -> tuple[dt.date, dt.date]:
    """Six whole months before today's month and six after (13 months)."""
    first = add_months(today.replace(day=1), -6)
    return first, month_end(add_months(today.replace(day=1), 6))


def read_json(path: Path):
    """A data file, with the line and column in the message when it is not valid JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} 第 {exc.lineno} 行第 {exc.colno} 列不是合法的 JSON（{exc.msg}）") from None


def _picture_ok(img, img_dir: Path) -> bool:
    """An image entry names a plain file that exists in img_dir (no sub-paths, nothing outside it)."""
    name = str((img or {}).get("file", "")) if isinstance(img, dict) else ""
    return bool(_FILE_NAME.fullmatch(name)) and (img_dir / name).is_file()


def load(path: Path, img_dir: Path | None = None) -> tuple[list[dict], list[str]]:
    """Events from the JSON file, checked; a bad event is left out and named in the problems list.
    Pictures are looked up in img_dir (default: images/ next to the file)."""
    img_dir = img_dir or path.parent / "images"
    raw = read_json(path) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} 最外层要是一个对象，事件放在 \"events\" 里")
    events, problems, seen = [], [], set()
    for i, ev in enumerate(raw.get("events") or []):
        where = f"event {i + 1} ({ev.get('id', '?') if isinstance(ev, dict) else '?'})"
        try:
            ev = dict(ev)
            for key in ("id", "lane", "category", "date", "status", "title", "what", "why"):
                if not str(ev.get(key) or "").strip():
                    raise ValueError(f"missing {key}")
            if not re.fullmatch(r"[a-z0-9-]+", str(ev["id"])) or ev["id"] in seen:
                raise ValueError("id must be unique lowercase-dash")
            if ev["lane"] not in LANES:
                raise ValueError(f"lane {ev['lane']!r}")
            if ev["status"] not in STATUS:
                raise ValueError(f"status {ev['status']!r}")
            ev["heat"] = int(ev.get("heat") or 1)
            if ev["heat"] not in HEAT:
                raise ValueError(f"heat {ev['heat']!r}")
            ev["start"], ev["end"], ev["precision"] = parse_date(ev["date"])
            sources = [s for s in ev.get("sources") or []
                       if isinstance(s, dict) and str(s.get("url", "")).startswith("https://")]
            if not sources:
                raise ValueError("needs at least one https source")
            ev["sources"] = sources
        except (TypeError, ValueError) as exc:
            problems.append(f"{where}: {exc} (left out)")
            continue
        img = ev.get("image")
        if img and not _picture_ok(img, img_dir):
            problems.append(f"{where}: picture {(img or {}).get('file') if isinstance(img, dict) else img!r} "
                            f"not in {img_dir.name}/ (text cover used)")
            img = None
        ev["image"] = img or None
        seen.add(ev["id"])
        events.append(ev)
    events.sort(key=lambda e: (e["start"], e["end"], -e["heat"]))
    return events, problems


def in_window(events: list[dict], today: dt.date) -> list[dict]:
    first, last = window(today)
    return [e for e in events if e["end"] >= first and e["start"] <= last]


def is_past(ev: dict, today: dt.date) -> bool:
    """Above today's line: a dated event up to today, or a month/quarter that has fully gone by."""
    return ev["end"] <= today if ev["precision"] == "day" else ev["end"] < today


# ---------- labels ----------

def date_label(ev: dict, today: dt.date) -> str:
    d = ev["start"]
    year = f"{d.year} 年 " if d.year != today.year else ""
    if ev["precision"] == "day":
        return f"{year}{d.month} 月 {d.day} 日 · 周{WEEK[d.weekday()]}"
    if ev["precision"] == "month":
        return f"{year}{d.month} 月内"
    return f"{d.year} 年{QUARTER[(d.month - 1) // 3]}季度"


def status_of(ev: dict, today: dt.date) -> tuple[str, str]:
    """(css class, text). A scheduled day that has come counts as due until someone confirms it happened."""
    st = ev["status"]
    if st == "scheduled" and ev["precision"] == "day" and ev["start"] == today:
        return "today", "就是今天"
    if st == "scheduled" and ev["end"] < today:
        return "due", "日期已过，待确认"
    return {"done": "done", "scheduled": "sched", "expected": "exp", "unconfirmed": "unc"}[st], STATUS[st]


def countdown(ev: dict, today: dt.date) -> str:
    if ev["precision"] != "day" or ev["start"] <= today:
        return ""
    return f"还有 {(ev['start'] - today).days} 天"


def short_date(ev: dict) -> str:
    d = ev["start"]
    if ev["precision"] == "day":
        return f"{d.month}.{d.day}"
    if ev["precision"] == "month":
        return f"{d.month} 月"
    return f"{d.year % 100} 年 Q{(d.month - 1) // 3 + 1}"


# ---------- pictures ----------

def picture(e: dict, cls: str, w: int, h: int) -> str:
    """The event's picture, or a text cover in its lane's colours when it has none."""
    img = e.get("image") or {}
    if img.get("file"):
        host = re.sub(r"^www\.", "", urlsplit(str(img.get("from") or "")).hostname or "")
        title = f' title="图：{esc(host)}"' if host else ""
        return (f'<img class="{cls}" src="{IMG_DIR}{esc(img["file"])}" width="{w}" height="{h}" '
                f'loading="lazy" decoding="async" alt=""{title}>')
    return f'<span class="{cls} ph lane-{e["lane"]}" aria-hidden="true"><b>{esc(e["category"])}</b></span>'


def pictures(events: list[dict], topics: list[dict] = ()) -> list[str]:
    """File names of the pictures the page uses (to copy next to it)."""
    names = [(x.get("image") or {}).get("file") for x in list(events) + list(topics)]
    return sorted({n for n in names if n})


# ---------- next big ones ----------

def next_up(events: list[dict], today: dt.date, n: int = 3) -> str:
    """The nearest dated big ones (heat 3), topped up with heat 2 when fewer than n are coming."""
    coming = [e for e in events if not is_past(e, today) and e["heat"] >= 2 and e["status"] != "unconfirmed"]
    coming.sort(key=lambda e: (e["precision"] != "day", e["heat"] != 3, e["start"]))
    picked = sorted(coming[:n], key=lambda e: (e["precision"] != "day", e["start"]))
    cards = []
    for e in picked:
        if e["precision"] == "day":
            days = (e["start"] - today).days
            big = f'<b class="nx-n">{days}</b><span class="nx-u">天后</span>' if days else '<b class="nx-n">今天</b>'
        else:
            big = f'<b class="nx-n sm">{esc(date_label(e, today))}</b>'
        cards.append(
            f'<a class="nx lane-{e["lane"]}" href="#ev-{esc(e["id"])}">{picture(e, "nx-img", 640, 360)}'
            f'<span class="nx-body"><span class="nx-big">{big}</span>'
            f'<span class="nx-title">{esc(e["title"])}</span>'
            f'<span class="nx-date">{esc(date_label(e, today))} · {esc(e["category"])}</span></span></a>'
        )
    return "".join(cards)


# ---------- one lane's year as month cards ----------

RANK = {"day": 0, "month": 1, "quarter": 2}   # inside a month the dated ones come first, the vaguer ones after


def month_of(e: dict, today: dt.date) -> tuple[dt.date, bool]:
    """(month it is filed under, whether it is past). Today's month splits around today; a quarter still running
    is filed under today's month."""
    past = is_past(e, today)
    this_month = today.replace(day=1)
    m = e["start"].replace(day=1)
    if m == this_month or (e["precision"] == "quarter" and not past and m < this_month):
        m = this_month
    return m, past


def strip(events: list[dict], today: dt.date) -> str:
    """One card per month for one lane, swiped sideways: big ones (heat 3) with a large cover, the rest with a
    small one. Today's month carries the warm line; months with nothing stay as a narrow label."""
    first, last = window(today)
    this_month = today.replace(day=1)
    cols: dict[tuple, list] = {}
    for e in events:
        m, past = month_of(e, today)
        key = (m, not past) if m == this_month else (m, m > this_month)
        cols.setdefault(key, []).append(e)

    def item(e: dict) -> str:
        cls = (f'hs-ev{" big" if e["heat"] == 3 else ""}{" gone" if is_past(e, today) else ""}'
               f'{" maybe" if e["status"] in ("expected", "unconfirmed") else ""}')
        return (f'<a class="{cls}" href="#ev-{esc(e["id"])}">{picture(e, "hs-img", 112, 112)}'
                f'<span><b>{esc(e["title"])}</b><small>{esc(short_date(e))}</small></span></a>')

    def column(key: tuple, label: str, cls: str, extra: str = "") -> str:
        items = sorted(cols.get(key, []), key=lambda e: (RANK[e["precision"]], e["start"]))
        if len(items) > 4:   # a crowded month takes two columns so it stays low
            cls += " w2"
        if not items:
            cls += " empty"
        return (f'<div class="hs-col{cls}"><div class="hs-m">{extra}<b>{label}</b></div>'
                f'<div class="hs-items">{"".join(item(e) for e in items)}</div></div>')

    out, m = [], first
    while m <= last:
        label = f"{m.year} 年 1 月" if m.month == 1 else f"{m.month} 月"
        if m == this_month:
            if cols.get((m, False)):
                out.append(column((m, False), f"{m.month} 月 1–{today.day} 日", " gone"))
            out.append(column((m, True), label, " now", f'<span class="hs-now">今天 {today.month}.{today.day}</span>'))
        else:
            out.append(column((m, m > this_month), label, "" if m > this_month else " gone"))
        m = add_months(m, 1)
    return (f'<div class="hs-wrap"><button class="hs-go prev" type="button" aria-label="往前看" hidden>‹</button>'
            f'<div class="hs" tabindex="0" role="region" aria-label="前后各半年的大事，按月份排，可以左右滑">'
            f'<div class="hs-grid">{"".join(out)}</div></div>'
            f'<button class="hs-go next" type="button" aria-label="往后看" hidden>›</button></div>')


# ---------- one lane's month list ----------

def card(e: dict, today: dt.date) -> str:
    st_cls, st_text = status_of(e, today)
    cd = countdown(e, today)
    st = "" if st_cls == "done" else f'<span class="st">{st_text}</span>'
    hot = '<span class="hot">大热点</span>' if e["heat"] == 3 else ""
    links = "".join(
        f'<a href="{esc(s["url"])}" target="_blank" rel="noopener noreferrer">{esc(s.get("name") or "来源")}</a>'
        for s in e["sources"][:3]
    )
    iso = e["start"].isoformat() if e["precision"] == "day" else e["start"].strftime("%Y-%m")
    return f'''<article class="ev heat-{e["heat"]} st-{st_cls}" id="ev-{esc(e["id"])}">
  {picture(e, "ev-img", 320, 180)}
  <div class="ev-body">
  <header><time datetime="{iso}">{esc(date_label(e, today))}</time>{st}{f'<span class="cd">{cd}</span>' if cd else ''}{hot}</header>
  <h4>{esc(e["title"])}</h4>
  <p class="what">{esc(e["what"])}</p>
  <p class="why"><b>跟做视频的关系</b>{esc(e["why"])}</p>
  <footer><span class="cat">{esc(e["category"])}</span><span class="src">来源 {links}</span></footer>
  </div>
</article>'''


def blocks(events: list[dict], today: dt.date) -> list[dict]:
    """Month blocks in order; today's month splits in two around the today line."""
    out: dict[tuple, dict] = {}
    for e in events:
        m, past = month_of(e, today)
        out.setdefault((m, not past), {"month": m, "past": past, "items": []})["items"].append(e)
    for b in out.values():
        b["items"].sort(key=lambda e: (RANK[e["precision"]], e["start"]))
    return [out[k] for k in sorted(out)]


def month_list(events: list[dict], today: dt.date, key: str) -> str:
    """One lane's months with today's line between; the past half-year folds into one bar so the list starts near
    today. key prefixes the ids (two lanes share the page)."""
    past_rows, rows = [], []
    this_month = today.replace(day=1)
    all_blocks = blocks(events, today)
    for b in all_blocks:
        m = b["month"]
        bid = f'{key}-m-{m:%Y-%m}' + ("-next" if m == this_month and not b["past"] else "")
        sub = ""
        if m == this_month:
            sub = f"1–{today.day} 日" if b["past"] else "接下来"
        elif m.month == 1 or b is all_blocks[0]:
            sub = str(m.year)
        (past_rows if b["past"] else rows).append(
            f'<section class="tl-month{" gone" if b["past"] else ""}" id="{bid}">'
            f'<h4 class="tl-mh"><b>{m.month} 月</b>{f"<small>{sub}</small>" if sub else ""}</h4>'
            f'<div class="tl-cards">{"".join(card(e, today) for e in b["items"])}</div></section>')
    head = ""
    if past_rows:
        n = sum(len(b["items"]) for b in all_blocks if b["past"])
        first = all_blocks[0]["month"]
        head = (f'<details class="tl-pastbox" id="{key}-past"><summary class="tl-pastsum">'
                f'<b>过去半年 {n} 件</b><span>{first.year} 年 {first.month} 月到 {today.month} 月 {today.day} 日</span>'
                f'<span class="more"><span class="o">展开看</span><span class="c">收起</span></span></summary>'
                f'{"".join(past_rows)}</details>')
    return f'<div class="tl">{head}{today_line(today, key)}{"".join(rows)}</div>'


def today_line(today: dt.date, key: str) -> str:
    return (f'<div class="tl-today" id="{key}-today"><span class="tl-up">↑ 已经发生</span>'
            f'<span class="tl-pill"><span class="pulse"></span>今天 · {today.month} 月 {today.day} 日 周{WEEK[today.weekday()]}</span>'
            f'<span class="tl-down">接下来 ↓</span></div>')


# ---------- YouTube history: how the big waves rose and faded in YouTube search ----------

def load_youtube(path: Path, trends_path: Path, img_dir: Path | None = None) -> tuple[list[dict], dict, list[str]]:
    """Topics from youtube_hot.json joined with their Google Trends curve (youtube_trends.json)."""
    if not path.is_file() or not trends_path.is_file():
        return [], {}, []
    img_dir = img_dir or path.parent / "images"
    raw = read_json(path) or {}
    trends = read_json(trends_path) or {}
    if not isinstance(raw, dict) or not isinstance(trends, dict):
        raise ValueError(f"{path.name} 和 {trends_path.name} 最外层都要是一个对象")
    topics, problems = [], []
    for t in raw.get("topics") or []:
        if not isinstance(t, dict):
            problems.append("youtube topic ?: not an object (left out)")
            continue
        t = dict(t)
        series = (trends.get("series") or {}).get(t.get("keyword", ""))
        if not series or not series.get("values") or not t.get("sources") or not all(
                t.get(k) for k in ("id", "title", "what", "why")):
            problems.append(f"youtube topic {t.get('id')}: no trends series, no source or a missing field (left out)")
            continue
        img = t.get("image")
        if img and not _picture_ok(img, img_dir):
            problems.append(f"youtube topic {t.get('id')}: picture {(img or {}).get('file') if isinstance(img, dict) else img!r} "
                            f"missing (text cover used)")
            img = None
        t["image"] = img or None
        t["series"] = series
        topics.append(t)
    topics.sort(key=lambda t: -float(t["series"].get("ratio") or 0))
    return topics, trends, problems


def wave(values: list[int]) -> dict:
    """Peak week; weeks from rising to the peak (rising = first clearing a fifth of the way from the topic's own
    floor before the peak to the peak; None when it was already up when the curve starts); weeks at half the
    peak or more; and where it is now."""
    peak = max(range(len(values)), key=lambda i: values[i])
    floor = min(values[:peak]) if peak else values[0]
    bar = floor + 0.2 * (values[peak] - floor)
    rise = peak
    while rise > 0 and values[rise - 1] >= bar:
        rise -= 1
    up = None if peak < 4 or (rise == 0 and values[0] >= bar) else peak - rise   # peak at the very start: it rose before the curve
    return {"peak": peak, "up": up, "half": sum(1 for v in values if v >= 50), "now": values[-1]}


def week_date(trends: dict, i: int) -> dt.date:
    return dt.date.fromtimestamp(int(trends["start"]) + i * 7 * 86400 + 43200)


def spark(values: list[int], peak: int) -> str:
    w, h, top = 600, 84, 8
    n = max(len(values) - 1, 1)
    pts = [(i * w / n, h - 2 - v / 100 * (h - top - 2)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    px, py = pts[peak]
    return (f'<svg class="yt-spark" viewBox="0 0 {w} {h}" aria-hidden="true" focusable="false">'
            f'<polygon class="area" points="0,{h} {line} {w},{h}"/><polyline class="line" points="{line}"/>'
            f'<line class="pk" x1="{px:.1f}" y1="{top - 4}" x2="{px:.1f}" y2="{h}"/><circle class="pk" cx="{px:.1f}" cy="{py:.1f}" r="4.5"/></svg>')


def anchor_title(topics: list[dict]) -> str:
    """The topic whose peak counts as 1 (the anchor every other curve was compared with)."""
    return next((t["title"] for t in topics if abs(float(t["series"].get("ratio") or 0) - 1) < 0.001), "")


def youtube_compare(topics: list[dict], anchor_title: str) -> str:
    """Peak height of every wave against the anchor's peak, as bars."""
    top = max(float(t["series"].get("ratio") or 0) for t in topics) or 1
    rows = []
    for t in topics:
        r = float(t["series"].get("ratio") or 0)
        pct = r / top * 100
        width = f"w{int(pct) // 5 * 5}" if pct >= 5 else "w1"
        anchor = abs(r - 1) < 0.001
        label = "基准 1" if anchor else f"{r:.2f} 倍" if r < 0.1 else f"{r:.1f} 倍"
        rows.append(f'<li{" class=anchor" if anchor else ""}><a href="#yt-{esc(t["id"])}"><span class="yc-n">{esc(t["title"])}</span>'
                    f'<span class="yc-bar"><i class="{width}"></i></span><b>{label}</b></a></li>')
    return f'<ol class="yt-compare" aria-label="YouTube 搜索高峰对比，以{esc(anchor_title)}的高峰为 1">{"".join(rows)}</ol>'


def youtube_cards(topics: list[dict], trends: dict) -> str:
    cards = []
    start = dt.date.fromisoformat(str(trends.get("time", "2025-01-01"))[:10])
    got = dt.date.fromisoformat(str(trends.get("fetched", "2026-11-04"))[:10])
    for t in sorted(topics, key=lambda t: wave(t["series"]["values"])["peak"]):   # cards in the order the waves peaked
        v = t["series"]["values"]
        wv = wave(v)
        pk = week_date(trends, wv["peak"])
        links = "".join(f'<a href="{esc(s["url"])}" target="_blank" rel="noopener noreferrer">{esc(s.get("name") or "来源")}</a>'
                        for s in t["sources"][:3] if str(s.get("url", "")).startswith("https://"))
        rec = f'<p class="yt-rec">{esc(t["record"])}</p>' if t.get("record") else ""
        cards.append(f'''<article class="yt" id="yt-{esc(t["id"])}">
  <header>{picture({"image": t.get("image"), "lane": "video", "category": t.get("kind", "")}, "yt-img", 320, 180)}
    <div><p class="yt-kind">{esc(t.get("kind", ""))} · 引爆 {esc(t.get("start_label", ""))}</p><h3>{esc(t["title"])}</h3>
    <p class="yt-what">{esc(t["what"])}</p></div></header>
  <div class="yt-chart">{spark(v, wv["peak"])}<p class="yt-axis"><span>{start.year}.{start.month}</span><span>YouTube 搜索热度 · 关键词「{esc(t["keyword"])}」</span><span>{got.year}.{got.month}</span></p></div>
  <ul class="yt-stats"><li><b>{pk.year % 100}.{pk.month}.{pk.day}</b><span>高峰那周</span></li><li><b>{f'{wv["up"]} 周' if wv["up"] is not None else f'{start.year % 100}.{start.month} 前'}</b><span>冒头到见顶</span></li><li><b>{wv["half"]} 周</b><span>热度过半</span></li><li><b>{wv["now"]}%</b><span>现在剩下</span></li></ul>
  {rec}<details class="yt-more"><summary>做视频的人怎么跟 · 来源</summary><p class="yt-why">{esc(t["why"])}</p>
  <footer>来源 {links}</footer></details>
</article>''')
    return "".join(cards)


# ---------- for agents (data/timeline.json, data/youtube-history.json and the MCP tools) ----------

def public_json(events: list[dict], today: dt.date) -> dict:
    first, last = window(today)
    keep = ("id", "lane", "category", "date", "status", "heat", "title", "what", "why", "sources")
    return {
        "today": today.isoformat(), "window": [first.isoformat(), last.isoformat()],
        "lanes": LANES, "status": STATUS,
        "events": [{**{k: e[k] for k in keep}, "date": str(e["date"]), "precision": e["precision"],
                    "image": IMG_PUBLIC + e["image"]["file"] if e.get("image") else None} for e in events],
    }


def public_youtube(topics: list[dict], trends: dict) -> dict:
    """YouTube history for agents: the page's numbers per wave, without the raw curve."""
    out = []
    for t in topics:
        w = wave(t["series"]["values"])
        out.append({"id": t["id"], "title": t["title"], "kind": t.get("kind", ""), "start_label": t.get("start_label", ""),
                    "keyword": t["keyword"], "what": t["what"], "record": t.get("record", ""), "why": t["why"],
                    "sources": t["sources"], "ratio": float(t["series"].get("ratio") or 0),
                    "peak_week": week_date(trends, w["peak"]).isoformat(), "up": w["up"], "half": w["half"], "now": w["now"]})
    return {"fetched": trends.get("fetched", ""), "source": trends.get("source", ""), "anchor_title": anchor_title(topics),
            "topics": out}

