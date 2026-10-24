"""Deep dive on a day's video topics: when each format first shows up, and more of its hits.

    python3 -m radar deep [--day D] [--labels 新起,升温]   # all topics: ~4 min, ~$0.2 TikHub + ~¥0.1 DeepSeek

The daily run calls it for the topics labelled config.DEEP_DAILY_LABELS (新起 / 升温) only.

The model only proposes search terms (Chinese for B站 / 抖音, English for YouTube / TikTok) and, at the
end, writes a short note from the facts. Everything else is code:

- B站 (free): the last eight quarters, one search each (most played, publish-time window). A result counts
  when its title or tags contain the term (mature formats drop the word from titles and keep it as a tag).
  The oldest quarter with any is split into months and searched most-played and newest; the oldest match
  there is B站's "最早能查到".
- YouTube: two searches with no date limit (relevance, and most viewed of all time), title-matched.
- TikTok: most liked of all time, title-matched.
- 抖音: TikHub's widest window (about half a year), title containing the term.
Every match must say AI outright (strong AI words in title, tags or channel, or TikTok's AI-made flag);
without that, "vegetable family" returns 2018 nursery rhymes and "AI MV" returns official music videos.

"最早能查到" is the oldest matching video these searches return. Platform search ranks by popularity,
so a format can be older than that; the page says so.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from . import analyze, db, llm, net, pipeline
from .analyze import BJ, _bj
from .score import fmt_num
from .sources import bilibili, shortvideo, youtube
from .sources.common import in_lane, log, strong_ai

QUARTER = 91 * 86400
QUARTERS = 8
MONTH = QUARTER / 3

TERMS_PROMPT = """下面是 AI 情报雷达今天识别出的视频题材。为了查清每个题材最早什么时候出现、再多找一些同类爆款，请给每个题材写搜索词。

题材（编号 | 名字 | 说明 | 已有搜索词 | 代表作标题）：
{lines}

只输出一个 JSON 对象（不要 Markdown 代码块）：
{{"topics": [{{
  "id": 编号,
  "zh": "中文搜索词，2 到 6 个字，必须是这类视频标题里常见、又能代表这个形式的词（比如「大肥鱼」「蔬菜家族」「第一人称」）；已有的 B 站搜索词合适就沿用；这个题材在中文平台没有就写空字符串",
  "en": "英文搜索词，2 到 4 个词，YouTube / TikTok 上这类视频标题里常见的说法（比如 vegetable family、fruit baby、AI food slide）；已有的 YouTube 搜索词合适就沿用；海外没有就写空字符串",
  "home": "cn | overseas | both"
}}]}}

搜索词要和题材名一样具体，能把它和别的 AI 视频分开：题材是「AI 美食滑梯解压」就用「美食滑梯」/「food slide」，不要用「AI解压」；题材是「AI 游戏角色 MV」就用代表性的角色或游戏名，不要用「AI MV」。题材本身就是一个大类（比如「AI 漫剧连载」）时才用大类词。不要用「AI视频」「AI动画」这种泛词。中文输出。"""

NOTE_PROMPT = """你在给 AI 视频创作者讲每个题材的来龙去脉。下面是系统从各平台搜索查到的事实：日期、平台、标题、播放都来自搜索结果，你不能改，也不能编造没给出的事实。平台搜索按热度排序，所以「最早能查到」不一定是真正的源头，说法要留余地。

{blocks}

每个题材写三句话：
- origin：最早能查到是什么时候、在哪个平台、什么样的作品；之后是一直有，还是最近才突然多起来。
- spread：后来在哪些平台出现、长出了什么变体（换角色、换语种、换剧情）；看不出来就写空字符串。
- now：现在进场的时机（还在早期 / 正在起量 / 已经很卷），说出依据。

只输出一个 JSON 对象（不要 Markdown 代码块）：
{{"topics": [{{"id": 编号, "origin": "...", "spread": "...", "now": "..."}}]}}

用大白话，不要用「不是…而是…」句式，不要冒号清单。中文输出。"""


def _matches_bili(term: str, rows: list[dict]) -> list[dict]:
    want = bilibili._norm(term)
    lane_term = strong_ai(term)
    out, seen = [], set()
    for v in rows:
        title, tags = bilibili._clean(v.get("title")), v.get("tag") or ""
        if not v.get("bvid") or v["bvid"] in seen or want not in bilibili._norm(title + " " + tags):
            continue
        if not (lane_term or strong_ai(f"{title} {tags} {bilibili._clean(v.get('description'))[:120]}")):
            continue
        seen.add(v["bvid"])
        out.append(bilibili._card(v, f"bili:{term}"))
    return out


def _bili_search(term: str, begin: float, end: float, orders=("click",)) -> list[dict]:
    rows = []
    for order in orders:
        try:
            rows += bilibili._search(term, begin, end, order=order).get("result") or []
        except Exception as exc:
            log(f"deep bili {term}: {exc}")
        time.sleep(0.9)
    return _matches_bili(term, rows)


def bili_history(term: str, now: float) -> dict:
    """Matching videos per quarter over two years, then the oldest month that has any."""
    quarters, found = [], {}
    for k in range(QUARTERS - 1, -1, -1):          # oldest first
        end = now - k * QUARTER
        hits = _bili_search(term, end - QUARTER, end)
        quarters.append({"start": end - QUARTER, "end": end, "match": len(hits),
                         "top_views": max((h["metrics"]["views"] or 0 for h in hits), default=0)})
        for h in hits:
            found[h["id"]] = h
    first = next((q for q in quarters if q["match"]), None)
    earliest = None
    if first:
        for m in range(3):
            b = first["start"] + m * MONTH
            hits = _bili_search(term, b, b + MONTH, orders=("click", "pubdate"))
            for h in hits:
                found[h["id"]] = h
            if hits:
                earliest = min(hits, key=lambda h: h["published_at"] or now)
                break
        if earliest is None:     # the month split missed what the quarter search saw
            in_q = [h for h in found.values() if h["published_at"] and first["start"] <= h["published_at"] <= first["end"]]
            earliest = min(in_q, key=lambda h: h["published_at"]) if in_q else None
    return {"term": term, "quarters": quarters, "earliest": earliest, "hits": list(found.values()),
            "older_than_window": bool(quarters and quarters[0]["match"])}


def youtube_history(term: str) -> dict:
    rows = []
    for sort_by in (None, "view_count"):
        try:
            rows += youtube.search_any_time(term, sort_by)
        except Exception as exc:
            log(f"deep youtube {term}: {exc}")
    seen, hits = set(), []
    for c in rows:
        if c["id"] not in seen and youtube.title_matches(term, c["title"]) \
                and strong_ai(f"{c['title']} {c.get('text') or ''} {c.get('author') or ''}"):
            seen.add(c["id"])
            hits.append(c)
    dated = [h for h in hits if h.get("published_at")]
    return {"term": term, "hits": hits, "earliest": min(dated, key=lambda h: h["published_at"]) if dated else None,
            "approx_dates": True}


def tiktok_history(term: str) -> dict:
    try:
        rows = shortvideo.tiktok([term], publish_time=0)
    except Exception as exc:
        log(f"deep tiktok {term}: {exc}")
        rows = []
    hits = [c for c in rows if youtube.title_matches(term, c["title"])
            and (c["extra"].get("made_by_ai") or strong_ai(c["title"]))]
    dated = [h for h in hits if h.get("published_at")]
    return {"term": term, "hits": hits, "earliest": min(dated, key=lambda h: h["published_at"]) if dated else None}


def douyin_history(term: str) -> dict:
    try:
        rows = shortvideo.douyin([(term, 0)], per_query=300, date_type=0)
    except Exception as exc:
        log(f"deep douyin {term}: {exc}")
        rows = []
    want = bilibili._norm(term)
    hits = [c for c in rows if want in bilibili._norm(c["title"]) and strong_ai(c["title"])]
    dated = [h for h in hits if h.get("published_at")]
    return {"term": term, "hits": hits, "earliest": min(dated, key=lambda h: h["published_at"]) if dated else None,
            "window_days": 180}


def _sample(c: dict, now: float) -> dict:
    s = {"title": c["title"], "url": c["url"], "thumb": c.get("thumb") or "", "author": c.get("author") or "",
         "platform": analyze.platform(c), "duration": (c.get("extra") or {}).get("duration"),
         "vertical": bool((c.get("extra") or {}).get("vertical")),
         "views": c["metrics"].get("views"), "likes": c["metrics"].get("likes"), "followers": c["metrics"].get("followers"),
         "published": _bj(c.get("published_at"), "%Y-%m-%d"), "published_at": c.get("published_at")}
    s["age_h"] = round((now - c["published_at"]) / 3600, 1) if c.get("published_at") else None
    return s


PER_PLATFORM = {"B站": 4, "YouTube": 4, "YouTube Shorts": 3, "TikTok": 3, "抖音": 3}


def _hits(found: list[dict], today_urls: set, now: float) -> list[dict]:
    """The biggest hits per platform (plays; likes on 抖音), at most 14, today's samples left out."""
    by: dict[str, list] = {}
    for c in found:
        if c["url"] in today_urls:
            continue
        by.setdefault(analyze.platform(c), []).append(c)
    out = []
    for plat, rows in by.items():
        rows.sort(key=lambda c: -((c["metrics"].get("views") if c["metrics"].get("views") is not None
                                    else (c["metrics"].get("likes") or 0) * 20) or 0))
        out += rows[:PER_PLATFORM.get(plat, 3)]
    out.sort(key=lambda c: -((c["metrics"].get("views") if c["metrics"].get("views") is not None
                              else (c["metrics"].get("likes") or 0) * 20) or 0))
    return [_sample(c, now) for c in out[:14]]


def _fact_block(i: int, t: dict) -> str:
    lines = [f"T{i} | {t['name']} | {t['summary']}"]
    for plat, e in (t.get("earliest_by") or {}).items():
        lines.append(f"  {plat} 最早能查到：{e['published']}《{e['title'][:50]}》" +
                     (f" {fmt_num(e['views'])} 播放" if e.get("views") is not None else f" {fmt_num(e.get('likes'))} 赞"))
    q = t.get("bili_quarters") or []
    if q:
        lines.append("  B站每季度播放最高的结果里标题含「" + t["terms"]["zh"] + "」的条数（从两年前到现在）：" +
                     "、".join(f"{_bj(x['start'], '%Y-%m')}起 {x['match']}" for x in q))
    for h in t["hits"][:6]:
        lines.append(f"  爆款：{h['platform']} {h['published']}《{h['title'][:50]}》" +
                     (f" {fmt_num(h['views'])} 播放" if h.get("views") is not None else f" {fmt_num(h.get('likes'))} 赞"))
    return "\n".join(lines)


def run(day: str, labels=None) -> dict | None:
    """Deep-dive the day's video topics (only those with ``labels`` when given). None when nothing qualifies."""
    con = db.connect()
    row = con.execute("SELECT payload FROM briefs WHERE day=?", (day,)).fetchone()
    if not row:
        raise SystemExit(f"no brief for {day}")
    brief = json.loads(row["payload"])
    topics = [t for t in (brief.get("sections") or {}).get("video") or [] if not labels or t["label"] in labels]
    if not topics:
        log(f"deep: no video topics on {day}" + (f" labelled {'/'.join(labels)}" if labels else ""))
        return None
    mark = net.ledger.snapshot()
    now = time.time()
    lines = "\n".join(
        f"{i} | {t['name']} | {t['summary'][:80]} | B站「{t.get('bili_term') or ''}」YouTube「{t.get('youtube_term') or ''}」 | "
        + "；".join(s["title"][:30] for s in t["samples"][:3]) for i, t in enumerate(topics))
    terms = {x.get("id"): x for x in (llm.ask_json(TERMS_PROMPT.format(lines=lines), max_tokens=3000).get("topics") or [])}
    out = []
    for i, t in enumerate(topics):
        tm = terms.get(i) or {}
        zh = (tm.get("zh") or t.get("bili_term") or "").strip()
        en = (tm.get("en") or t.get("youtube_term") or "").strip()
        home = tm.get("home") or "both"
        log(f"deep {t['name']}: zh={zh!r} en={en!r} home={home}")
        found, earliest_by, d = [], {}, {"name": t["name"], "label": t["label"], "kind": t["kind"], "summary": t["summary"],
                                          "thread_id": t.get("thread_id"), "terms": {"zh": zh, "en": en, "home": home}}
        if zh:
            b = bili_history(zh, now)
            d["bili_quarters"], d["bili_older"] = b["quarters"], b["older_than_window"]
            found += b["hits"]
            if b["earliest"]:
                earliest_by["B站"] = b["earliest"]
            if home in ("cn", "both"):
                dy = douyin_history(zh)
                found += dy["hits"]
                if dy["earliest"]:
                    earliest_by["抖音"] = dy["earliest"]
        if en and home in ("overseas", "both"):
            yt = youtube_history(en)
            found += yt["hits"]
            if yt["earliest"]:
                earliest_by["YouTube"] = yt["earliest"]
            tk = tiktok_history(en)
            found += tk["hits"]
            if tk["earliest"]:
                earliest_by["TikTok"] = tk["earliest"]
        d["earliest_by"] = {p: _sample(c, now) for p, c in earliest_by.items()}
        first = min(earliest_by.items(), key=lambda kv: kv[1]["published_at"], default=None)
        d["earliest"] = {"platform": first[0], **_sample(first[1], now)} if first else None
        d["matched"] = {p: sum(1 for c in found if analyze.platform(c).startswith(p)) for p in ("B站", "YouTube", "TikTok", "抖音")}
        d["hits"] = _hits(found, {s["url"] for s in t["samples"]}, now)
        out.append(d)
    try:
        notes = {x.get("id"): x for x in (llm.ask_json(NOTE_PROMPT.format(
            blocks="\n\n".join(_fact_block(i, d) for i, d in enumerate(out))), max_tokens=6000).get("topics") or [])}
    except Exception as exc:
        log(f"deep notes failed: {exc}")
        notes = {}
    for i, d in enumerate(out):
        n = notes.get(i) or notes.get(f"T{i}") or {}
        d["note"] = {k: n.get(k) or "" for k in ("origin", "spread", "now")}
    thumbs = pipeline.cache_samples([s for d in out for s in d["hits"] + list(d["earliest_by"].values())])
    for d in out:     # the earliest card is a copy; point it at the cached cover too
        if d.get("earliest"):
            d["earliest"]["thumb"] = d["earliest_by"][d["earliest"]["platform"]]["thumb"]
    payload = {"day": day, "generated_bj": datetime.now(BJ).strftime("%Y-%m-%d %H:%M"),
               "topics": out, "labels": list(labels or []), "cost": net.ledger.summary(since=mark), "thumbs": thumbs}
    db.save_deep(con, day, payload)
    return payload
