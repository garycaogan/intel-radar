"""Static site: index.html (latest day), d/<day>.html per day, data/<day>.json.

Server-rendered HTML; a little JS swaps a thumbnail for the platform's player on click (so a page
with forty videos loads forty images, not forty players), filters labels and tracks the tabs.
Styles: system fonts, one green accent, 1px borders, light shadows.
``data/<day>.json`` is the reader's copy of the day: run costs, failures and timings stay in the database and log.
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import config, db, share
from .score import fmt_num

WEEK = "一二三四五六日"


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _age(h) -> str:
    if h is None:
        return ""
    if h < 1:
        return "刚发"
    return f"{int(h)} 小时前" if h < 48 else f"{int(h // 24)} 天前"


def _dur(s) -> str:
    if not s:
        return ""
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


LABEL_CLASS = {"新起": "l-new", "爆发": "l-new", "升温": "l-up", "热议": "l-up", "持续": "l-keep",
               "关注": "l-watch", "观察": "l-watch", "回落": "l-down"}


def label(text: str) -> str:
    return f'<span class="label {LABEL_CLASS.get(text, "l-watch")}">{e(text)}</span>' if text else ""


# ------------------------------------------------------------------ media

PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>'
OPEN_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
             '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>')

_BV = re.compile(r"bilibili\.com/video/(BV[0-9A-Za-z]+)")
_YT = re.compile(r"youtube\.com/(?:watch\?v=|shorts/)([\w-]{11})")
_DY = re.compile(r"douyin\.com/(?:video|note)/(\d+)")
_TT = re.compile(r"tiktok\.com/@[^/]+/video/(\d+)")


def media_of(s: dict) -> dict:
    """How a sample plays on the page: an embeddable player URL, a direct mp4 (X), or a link out."""
    url = s.get("url") or ""
    m = s.get("media") or {}
    out = {"thumb": s.get("thumb") or m.get("thumb") or "", "url": url, "kind": "link", "src": "",
           "vertical": bool(s.get("vertical") or (m.get("aspect") or 1.7) < 0.9)}
    if (b := _BV.search(url)):
        out.update(kind="iframe", src=f"https://player.bilibili.com/player.html?isOutside=true&bvid={b[1]}"
                                       "&autoplay=1&danmaku=0&high_quality=1")
    elif (y := _YT.search(url)):
        out.update(kind="iframe", src=f"https://www.youtube-nocookie.com/embed/{y[1]}?autoplay=1&rel=0&playsinline=1")
    elif (d := _DY.search(url)):
        # 抖音's player is laid out like a phone screen (the page gives it a 9:16 box when it starts) and
        # autoplays a black frame, so it opens on its own poster and play button
        out.update(kind="iframe", src=f"https://open.douyin.com/player/video?vid={d[1]}&autoplay=0")
    elif (t := _TT.search(url)):
        out.update(kind="iframe", src=f"https://www.tiktok.com/player/v1/{t[1]}?autoplay=1&rel=0")
    elif m.get("mp4"):
        out.update(kind="video", src=m["mp4"])
    return out


_PREFIX = ""     # "" on index.html, "../" on d/<day>.html; locally cached covers live in site/img/


def _img(url: str, cls: str = "ph", alt: str = "") -> str:
    if not url:
        return ""
    if not url.startswith(("https://", "http://")):
        url = _PREFIX + url
    return (f'<img class="{cls}" src="{e(url)}" alt="{e(alt)}" loading="lazy" decoding="async" '
            f'referrerpolicy="no-referrer">')


SHARE_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V3"/><path d="M7.5 7.5 12 3l4.5 4.5"/>'
              '<path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg>')


def share_btn(item: str, title: str) -> str:
    """Opens the share card for one item (head-1, video-<id>, x-<id>, repo-1); the picture is drawn on click."""
    return (f'<button class="share-btn" type="button" data-share="{e(item)}" aria-haspopup="dialog" '
            f'aria-label="生成分享卡片：{e((title or "")[:40])}">{SHARE_ICON}<span>分享</span></button>')


PLAT_KEY = {"B站": "bili", "YouTube": "yt", "YouTube Shorts": "yt", "抖音": "dy", "TikTok": "tt", "小红书": "xhs",
            "X": "x", "X 视频": "x"}


def player(s: dict, badge: str = "", big: bool = False) -> str:
    """A 16:9 box: thumbnail + play button that becomes the platform player in place."""
    md = media_of(s)
    title = s.get("title") or ""
    vcls = " v" if md["vertical"] else ""
    back = _img(md["thumb"], "bg") if md["vertical"] else ""
    corner = (f'<span class="m-plat" data-p="{PLAT_KEY.get(badge, "")}">{e(badge)}</span>' if badge else "") + \
             (f'<span class="m-dur">{e(_dur(s.get("duration")))}</span>' if s.get("duration") else "")
    inner = f'{back}{_img(md["thumb"], "ph", "")}{corner}'
    if md["kind"] == "link":
        if not md["thumb"]:
            return ""
        return (f'<div class="media{vcls}{" big" if big else ""}"><a class="player" href="{e(md["url"])}" target="_blank" '
                f'rel="noopener" aria-label="打开原帖：{e(title[:40])}">{inner}<span class="m-open">{OPEN_ICON}</span></a></div>')
    return (f'<div class="media{vcls}{" big" if big else ""}"><button class="player" type="button" data-kind="{md["kind"]}" '
            f'data-src="{e(md["src"])}" aria-label="播放：{e(title[:40])}">{inner}<span class="pbtn">{PLAY_ICON}</span></button></div>')


def _vmeta(s: dict) -> str:
    bits = [s.get("platform") or ""]
    if s.get("views") is not None:
        bits.append(f"{fmt_num(s['views'])} 播放")
    elif s.get("likes") is not None:
        bits.append(f"{fmt_num(s['likes'])} 赞")
    if s.get("age_h") is not None:
        bits.append(_age(s["age_h"]))
    return " · ".join(b for b in bits if b)


def _small_hit(s: dict) -> bool:
    return (s.get("vsr") or 0) >= 20 and (s.get("followers") or 0) < 100000


def vcard(s: dict) -> str:
    who = (s.get("author") or "")[:18]
    fol = f" · {fmt_num(s['followers'])} 粉" if s.get("followers") is not None else ""
    hit = '<b class="small-hit">小号爆款</b>' if _small_hit(s) else ""
    return f"""<div class="vcard">{player(s, s.get('platform') or '')}
  <a class="v-title" href="{e(s['url'])}" target="_blank" rel="noopener">{e(s['title'])}</a>
  <span class="v-meta">{e(_vmeta(s))}</span>
  <span class="v-meta">{e(who)}{e(fol)}{hit}</span></div>"""


def tweet_card(s: dict) -> str:
    md = s.get("media") or {}
    has_media = bool(md.get("mp4") or md.get("thumb") or s.get("thumb"))
    media = player({**s, "thumb": md.get("thumb") or s.get("thumb")}, "X 视频" if md.get("mp4") else "") if has_media else ""
    return f"""<div class="tweet">
  <a class="tw-head" href="{e(s['url'])}" target="_blank" rel="noopener"><b>{e(s.get('author'))}</b><span>@{e(s.get('handle'))} · {e(fmt_num(s.get('followers')))} 粉 · {e(s.get('published'))}</span></a>
  <p class="tw-text">{e(s['title'])}</p>{media}
  <span class="tw-meta">浏览 {e(fmt_num(s.get('views')))} · 赞 {e(fmt_num(s.get('likes')))} · 收藏 {e(fmt_num(s.get('bookmarks')))}</span>
</div>"""


repo_image = share.repo_image


# ------------------------------------------------------------------ headline

_why = share.why
_resolve = share.resolve


def _facts(vt: list, xt: list, tt: list) -> str:
    """Numbers next to the media, all from code."""
    if vt:
        t = vt[0]
        m = t["metrics"]
        bits = [f"{m['authors']} 个账号", f"合计 {fmt_num(m['views_total'])} 播放"]
        o = m.get("outlier") or {}
        if (m.get("max_vsr") or 0) >= 5 and o.get("followers") is not None:
            bits.append(f"{fmt_num(o['followers'])} 粉的号单条 {fmt_num(o['views'])}")
        return label(t["label"]) + "<span>" + e(" · ".join(bits)) + "</span>"
    if xt:
        t = xt[0]
        b = t.get("breadth") or {}
        if b.get("authors") and b.get("span_hours") is not None and b.get("tweets", 0) >= 15:
            line = f"X 上 {b['authors']} 个账号 {b['span_hours']} 小时内在聊"
        else:
            line = f"{t['metrics']['authors']} 个账号 · 合计 {fmt_num(t['metrics']['views_total'])} 浏览"
        return label(t["label"]) + "<span>" + e(line) + "</span>"
    if tt:
        r = tt[0]
        if r.get("stars") is not None:
            extra = f" · 今日 +{fmt_num(r['stars_today'])}" if r.get("stars_today") else ""
            return f"<span>★ {e(fmt_num(r['stars']))}{e(extra)}</span>"
    return ""


def _head_media(vt: list, xt: list, tt: list) -> tuple[str, str]:
    """(main player, thumbnail strip). Videos first, then an X post with media, then a repo's preview image."""
    if vt:
        samples = [s for t in vt for s in t["samples"]]
        s0 = samples[0]
        main = player(s0, s0.get("platform") or "", big=True) + (
            f'<a class="cap" href="{e(s0["url"])}" target="_blank" rel="noopener"><b>{e(s0["title"])}</b>'
            f'<span>{e(_vmeta(s0))}{e(" · " + (s0.get("author") or "")[:18]) if s0.get("author") else ""}'
            f'{e(" · " + fmt_num(s0["followers"]) + " 粉") if s0.get("followers") is not None else ""}</span></a>')
        rest = samples[1:4]
        strip = "".join(
            f'<button class="mini" type="button" data-kind="{media_of(s)["kind"]}" data-src="{e(media_of(s)["src"])}" '
            f'aria-label="播放：{e(s["title"][:40])}">{_img(s.get("thumb"), "ph")}<span class="mini-t">{e(_vmeta(s))}</span></button>'
            for s in rest if media_of(s)["kind"] != "link")
        return main, (f'<div class="strip">{strip}</div>' if strip else "")
    for t in xt:
        for s in t["samples"]:
            md = s.get("media") or {}
            if md.get("mp4") or md.get("thumb") or s.get("thumb"):
                return player({**s, "thumb": md.get("thumb") or s.get("thumb")}, "X", big=True), ""
    for r in tt:
        img = repo_image(r.get("url"))
        if img:
            return (f'<div class="media repo-media big"><a class="player" href="{e(r["url"])}" target="_blank" rel="noopener" '
                    f'aria-label="打开仓库 {e(r["name"])}">{_img(img, "ph", r["name"])}</a></div>'), ""
    return "", ""


GRADE_WORD = {"S": "必看", "A": "推荐", "B": "参考", "C": "观察"}
LABEL_ORDER = {"新起": 0, "爆发": 0, "升温": 1, "热议": 1, "持续": 2, "关注": 2, "观察": 3, "回落": 4}


def by_label(topics: list[dict]) -> list[dict]:
    """新起 / 爆发 first, then 升温 / 热议, 持续, 观察; the analysis order (weight) inside each."""
    return sorted(topics, key=lambda t: LABEL_ORDER.get(t.get("label"), 5))


def headline_html(brief: dict) -> str:
    items = brief.get("headline") or []
    if not items:
        return '<p class="empty">今天的头条还没整理出来，下面各分区可以先看。</p>'
    sec = brief.get("sections") or {}
    out = []
    for i, h in enumerate(items):
        vt, xt, tt = _resolve(h, sec)
        main, strip = _head_media(vt, xt, tt)
        seen_names, links = set(), []
        for l in h.get("links") or []:
            if l["name"] not in seen_names:
                seen_names.add(l["name"])
                links.append(l)
        chips = "".join(
            f'<a class="chip" href="#{e(l["section"])}-{e(l.get("thread_id") or "")}">{e(l["name"])}</a>'
            if l["section"] in ("video", "x") else
            f'<a class="chip" href="{e(l["url"])}" target="_blank" rel="noopener">{e(l["name"])} {OPEN_ICON}</a>'
            for l in links)
        grade = (h.get("grade") or "C").strip().upper()[:1]
        facts = _facts(vt, xt, tt)
        comment = h.get("comment") or ""
        cls = "hl lead" if i == 0 and main else ("hl" if main else "hl no-media")
        out.append(f"""
<article class="card {cls}" id="head-{i + 1}">
  {f'<div class="hl-media">{main}{strip}</div>' if main else ''}
  <div class="hl-body">
    <div class="hl-top"><span class="grade g-{e(grade.lower())}">{e(GRADE_WORD.get(grade, "观察"))}</span><span class="verdict">{e(h.get('verdict'))}</span>{share_btn(f"head-{i + 1}", h.get("title"))}</div>
    <h3>{e(h.get('title'))}</h3>
    {f'<div class="facts">{facts}</div>' if facts else ''}
    <p class="why">{e(_why(h))}</p>
    {f'<p class="pt"><b class="k-next">下一步</b>{e(h["next_step"])}</p>' if h.get('next_step') else ''}
    {f'<p class="pt"><b class="k-risk">注意</b>{e(h["risk"])}</p>' if h.get('risk') else ''}
    {f'<details class="more"><summary>展开完整点评</summary><p>{e(comment)}</p></details>' if comment else ''}
    {f'<div class="chips">{chips}</div>' if chips else ''}
  </div>
</article>""")
    return '<div class="hl-grid">' + "".join(out) + "</div>"


# ------------------------------------------------------------------ sections

def _outlier(m: dict) -> str:
    o = m.get("outlier") or {}
    if (m.get("max_vsr") or 0) < 5 or o.get("followers") is None:
        return ""
    return f"{fmt_num(o['followers'])} 粉的号单条 {fmt_num(o['views'])} 播放"


def _seen(t: dict) -> str:
    return f"第 {t.get('days_seen', 1)} 天出现" if t.get("days_seen", 1) > 1 else "首次出现"


def _more_btn(n: int, shown: int, what: str) -> str:
    return (f'<button class="more-btn" type="button" aria-expanded="false" data-less="收起">再看 {n - shown} {what}</button>'
            if n > shown else "")


def video_html(topics: list[dict], deep_href: str = "", deep_ids: set | None = None) -> str:
    if not topics:
        return '<p class="empty">今天没有识别出视频题材。</p>'
    out = []
    for t in topics:
        m = t["metrics"]
        facts = [f"{m['videos']} 条视频", f"{m['authors']} 个账号", " / ".join(m["platforms"])]
        if m.get("views_total"):
            facts.append(f"合计 {fmt_num(m['views_total'])} 播放")
        if m.get("likes_total"):
            facts.append(f"抖音/小红书合计 {fmt_num(m['likes_total'])} 赞")
        if m.get("vph_max"):
            facts.append(f"最快每小时 {fmt_num(m['vph_max'])} 播放")
        if _outlier(m):
            facts.append(_outlier(m))
        samples = t["samples"]
        out.append(f"""
<article class="card topic" id="video-{e(t.get('thread_id'))}" data-label="{e(t['label'])}">
  <header class="topic-head">
    <div class="topic-title">{label(t['label'])}<h3>{e(t['name'])}</h3><span class="kind">{e(t['kind'])}</span></div>
    <div class="head-r"><span class="seen">{e(_seen(t))}{f' · <a class="deep-link" href="{e(deep_href)}#t{e(t.get("thread_id"))}">起源和更多爆款 →</a>' if deep_href and t.get("thread_id") in (deep_ids or ()) else ''}</span>{share_btn(f"video-{t.get('thread_id')}", t['name']) if t.get('thread_id') is not None else ''}</div>
  </header>
  <p class="summary">{e(t['summary'])}</p>
  <div class="vids" data-shown="4">{''.join(vcard(s) for s in samples)}</div>
  {_more_btn(len(samples), 4, '条')}
  <div class="topic-foot"><span class="facts-line">{e(' · '.join(facts))}</span>
    <details class="basis"><summary>判断依据</summary><ul>{''.join(f'<li>{e(b)}</li>' for b in t['basis'])}</ul></details></div>
</article>""")
    return "".join(out)


def x_html(topics: list[dict]) -> str:
    if not topics:
        return '<p class="empty">今天没有识别出 X 话题。</p>'
    out = []
    for t in topics:
        m = t["metrics"]
        samples = t["samples"]
        out.append(f"""
<article class="card topic" id="x-{e(t.get('thread_id'))}" data-label="{e(t['label'])}">
  <header class="topic-head">
    <div class="topic-title">{label(t['label'])}<h3>{e(t['name'])}</h3><span class="kind">{e(t['kind'])}</span></div>
    <div class="head-r"><span class="seen">{e(_seen(t))}</span>{share_btn(f"x-{t.get('thread_id')}", t['name']) if t.get('thread_id') is not None else ''}</div>
  </header>
  <p class="summary">{e(t['summary'])}</p>
  <div class="tweets" data-shown="3">{''.join(tweet_card(s) for s in samples)}</div>
  {_more_btn(len(samples), 3, '条推文')}
  <div class="topic-foot"><span class="facts-line">{e(f"{m['tweets']} 条推文 · {m['authors']} 个账号 · 合计 {fmt_num(m['views_total'])} 浏览 · {fmt_num(m['likes_total'])} 赞")}</span>
    <details class="basis"><summary>判断依据</summary><ul>{''.join(f'<li>{e(b)}</li>' for b in t['basis'])}</ul></details></div>
</article>""")
    return "".join(out)


def news_html(items: list[dict]) -> str:
    if not items:
        return '<p class="empty">今天没有和 AI 视频创作相关的资讯或新产品。</p>'
    rows = "".join(f"""
<li class="news-item">
  <div class="n-top"><span class="tag">{e(n.get('source_name'))}</span><span class="n-time">{e(n.get('published'))}</span>{f'<span class="n-votes">▲ {e(fmt_num(n["votes"]))}</span>' if n.get('votes') else ''}</div>
  <a class="n-title" href="{e(n['url'])}" target="_blank" rel="noopener">{e(n['name'])}</a>
  {f'<p>{e(n["zh"])}</p>' if n.get('zh') and n['zh'] != n['name'] else ''}{f'<p class="use">{e(n["use"])}</p>' if n.get('use') else ''}
</li>""" for n in items)
    return f'<ul class="card news">{rows}</ul>'


def tools_html(tl: dict) -> str:
    repos = "".join(f"""
<div class="repo-wrap" id="repo-{i + 1}"><a class="repo" href="{e(r['url'])}" target="_blank" rel="noopener">
  <div class="media repo-media">{_img(repo_image(r['url']), 'ph', r['name'])}</div>
  <div class="repo-body">
    <div class="repo-name">{e(r['name'])}</div>
    <div class="repo-tags"><span class="tag">{e(r['group'])}</span>{'<span class="tag t-hot">今日上榜</span>' if r.get('from_trending') else ''}</div>
    <p>{e(r['zh'])}</p>{f'<p class="use">{e(r["use"])}</p>' if r.get('use') else ''}
    <div class="tool-meta">★ {e(fmt_num(r.get('stars')))}{(' · 今日 +' + e(fmt_num(r['stars_today']))) if r.get('stars_today') else ''}{(' · 日均 +' + e(fmt_num(r['stars_per_day']))) if r.get('stars_per_day') else ''} · 创建 {e(r.get('created'))}{(' · ' + e(r['language'])) if r.get('language') else ''}</div>
  </div>
</a>{share_btn(f"repo-{i + 1}", r['name'])}</div>""" for i, r in enumerate(tl.get("repos") or []))
    models = "".join(f"""
<li class="tool">
  <div class="tool-main"><a href="{e(r['url'])}" target="_blank" rel="noopener">{e(r['name'])}</a><span class="tag">{'模型' if r['kind'] == 'model' else 'Space'}</span></div>
  <p>{e(r['zh'])}</p>{f'<p class="use">{e(r["use"])}</p>' if r.get('use') else ''}
  <div class="tool-meta">♥ {e(fmt_num(r.get('likes')))}{(' · 下载 ' + e(fmt_num(r['downloads']))) if r.get('downloads') else ''} · 创建 {e(r.get('created'))}</div>
</li>""" for r in tl.get("models") or [])
    stories = "".join(f"""
<li class="tool">
  <div class="tool-main"><a href="{e(r['url'])}" target="_blank" rel="noopener">{e(r['name'])}</a></div>
  <p>{e(r['zh'])}</p>{f'<p class="use">{e(r["use"])}</p>' if r.get('use') else ''}
  <div class="tool-meta"><a href="{e(r.get('hn_url'))}" target="_blank" rel="noopener">HN {e(r.get('points'))} 分 · {e(r.get('comments'))} 评论</a> · {e(r.get('published'))}</div>
</li>""" for r in tl.get("stories") or [])
    return f"""
<h3 class="sub-title">GitHub 新仓库</h3>
<div class="repos">{repos or '<p class="empty">今天没有对口的新仓库。</p>'}</div>
<div class="tools-grid">
  <section class="card"><h3 class="col-title">Hugging Face 热门</h3><ul class="tools">{models or '<li class="empty">无</li>'}</ul></section>
  <section class="card"><h3 class="col-title">Hacker News 讨论</h3><ul class="tools">{stories or '<li class="empty">无</li>'}</ul></section>
</div>"""


def hot_html(words: list[dict]) -> str:
    if not words:
        return '<p class="empty">B站热搜和抖音热榜里今天没有 AI 相关的词。</p>'
    return '<div class="chips">' + "".join(
        f'<a class="chip" href="{e(w["url"])}" target="_blank" rel="noopener">{e(w["platform"])} #{e(w["rank"])} · {e(w["word"])}</a>'
        for w in words) + "</div>"


PLATFORM_NAMES = {"bili": "B站", "youtube": "YouTube", "douyin": "抖音", "tiktok": "TikTok", "xhs": "小红书", "x": "X",
                  "github": "GitHub", "hf": "Hugging Face", "hn": "Hacker News"}

LABEL_HELP = [
    ("新起", "以前几乎没有、最近突然冒出来。至少要两条独立信号同时成立，比如这个词以前搜不到、这周突然多了，同时有好几个账号在做或者有小号爆了。"),
    ("升温", "以前就有，最近一周明显变多。"),
    ("持续", "一直有人做、一直有人看的常青题材。"),
    ("观察", "只有一条信号，先放着看。"),
    ("爆发", "X 上一件事在十几个小时里被十几个账号同时提到。"),
]


def about_html(brief: dict) -> str:
    """How to read the page. Readers get the method, never the run's costs, vendors or failures."""
    st = brief.get("stats") or {}
    plats = "、".join(n for k, n in PLATFORM_NAMES.items() if (st.get("by_source") or {}).get(k))
    legend = "".join(f'<div class="lg"><dt>{label(name)}</dt><dd>{e(text)}</dd></div>' for name, text in LABEL_HELP)
    return f"""
<div class="about">
  <div class="about-col"><h3>标签怎么来的</h3><dl class="legend">{legend}</dl></div>
  <div class="about-col"><h3>看了哪些地方</h3>
    <p>{e(plats or 'B站、YouTube、抖音、TikTok、小红书、X、GitHub')}，以及中文 AI 资讯和 Product Hunt 的新产品，只看公开内容。视频看最近 7 天发布的，X 看最近两天，开源仓库看最近两周新建的。</p>
    <p>新起和升温的题材会再往回查两年，找最早能查到的视频和更多同类爆款，入口在题材卡片右上角。</p></div>
  <div class="about-col"><h3>数字和判断</h3>
    <p>播放、点赞、粉丝这些数字都来自平台公开页面，由程序计算；题材归类、标题和点评由 AI 写，每张卡片的「判断依据」写着触发了哪几条信号。</p>
    <p>只有一个账号说、又查不到来源的收入说法，不会放进头条。</p></div>
</div>
<p class="about-foot">本期整理了 {e(f"{st.get('items') or 0:,}")} 条公开内容，北京时间 {e(brief.get('generated_bj'))} 生成。内容由程序和 AI 自动整理，仅供参考。</p>"""


# ------------------------------------------------------------------ page

CSS = """
:root{--brand-deep:#006659;--brand-700:#087f73;--brand-600:#248f80;--brand-500:#36a590;--brand-100:#d9f5ef;--brand-50:#e9fffc;
--canvas:#f2f5f6;--surface:#fff;--subtle:#f7f9fa;--border:#e2e8ea;--border-strong:#cbd7d9;--text:#242528;--text-2:#3b3d40;--muted:#757980;
--info:#2877c7;--success:#228b65;--warning:#b96913;--danger:#c43b4d;--r-sm:8px;--r-md:12px;--r-lg:16px;--ink:#0d1517;
--shadow-sm:0 1px 2px rgba(17,55,51,.04),0 5px 16px rgba(17,55,51,.05);--shadow-md:0 14px 36px rgba(17,72,65,.10)}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:108px}
body{margin:0;background:var(--canvas);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif;-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:inherit;text-decoration:none}button{font:inherit;color:inherit}
a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--brand-600);outline-offset:2px;border-radius:6px}
.nav{position:sticky;top:0;z-index:20;height:56px;background:rgba(255,255,255,.92);backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--border)}
@supports not (backdrop-filter:blur(1px)){.nav{background:#fff}}
.nav-in,.wrap{max-width:1240px;margin:0 auto;padding:0 24px}
.nav-in{height:56px;display:flex;align-items:center;gap:16px}
.brand{display:flex;align-items:center;gap:10px;font-weight:720;font-size:16px;white-space:nowrap}
.brand-mark{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--brand-500),var(--brand-deep));display:grid;place-items:center}
.brand small{font-weight:500;color:var(--muted);font-size:13px}
.daypick{margin-left:auto;display:flex;align-items:center;gap:6px}
.daypick a,.daypick select{height:36px;min-width:40px;border:1px solid var(--border);background:#fff;border-radius:var(--r-sm);display:inline-grid;place-items:center;color:var(--text-2);font:inherit;font-size:14px;padding:0 10px;cursor:pointer}
.daypick a[aria-disabled=true]{color:#b5babf;pointer-events:none}
.mast{background:radial-gradient(circle at 8% 0%,rgba(54,165,144,.18),transparent 36%),radial-gradient(circle at 92% 10%,rgba(40,119,199,.08),transparent 30%),linear-gradient(135deg,#f5fffc 0%,#f8fbff 58%,#fffaf0 100%);border-bottom:1px solid var(--border)}
.mast .wrap{padding-top:26px;padding-bottom:22px}
.mast-top{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px}
.eyebrow{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:680;color:var(--brand-deep);background:#fff;border:1px solid var(--brand-100);border-radius:999px;padding:3px 11px}
.eyebrow::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--brand-500);box-shadow:0 0 0 3px rgba(54,165,144,.18)}
.mast-date{font-size:13px;color:var(--muted)}
.mast h1{margin:12px 0 0;font-size:30px;line-height:1.3;font-weight:780;letter-spacing:.01em}
.mast .lead{max-width:860px;margin:10px 0 0;font-size:16px;line-height:1.75;color:var(--text-2)}
.rising{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:16px}
.rising-k{font-size:13px;font-weight:700;color:var(--text-2);margin-right:2px}
.rise{display:inline-flex;align-items:center;gap:8px;min-height:36px;padding:4px 12px 4px 5px;background:#fff;border:1px solid var(--border);border-radius:999px;box-shadow:var(--shadow-sm);font-size:14px;font-weight:620;transition:border-color .15s,transform .15s}
.rise i{font-style:normal;color:var(--muted);font-weight:500}
.rise:hover{border-color:var(--brand-500);transform:translateY(-1px)}.rise:hover i{color:var(--brand-deep)}
.scan{margin:14px 0 0;font-size:13px;color:var(--muted)}
.tabs{position:sticky;top:56px;z-index:15;background:rgba(242,245,246,.96);border-bottom:1px solid var(--border)}
.tabs-in{max-width:1240px;margin:0 auto;padding:0 24px;display:flex;gap:2px}
.tabs a{padding:11px 12px;font-size:14px;color:var(--text-2);border-bottom:2px solid transparent;font-weight:560;white-space:nowrap}
.tabs a:hover{color:var(--brand-deep)}.tabs a.on{color:var(--brand-deep);border-bottom-color:var(--brand-600)}
main .wrap{padding-top:4px;padding-bottom:64px}
section.block{padding-top:26px}
.block-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.block-head h2{margin:0;font-size:21px;font-weight:740;display:flex;align-items:baseline;gap:10px}.block-head p{margin:0;color:var(--muted);font-size:14px}
.sec-no{font-size:13px;font-weight:700;color:var(--brand-600);font-variant-numeric:tabular-nums;letter-spacing:.04em}
.filters{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.filters button{height:32px;padding:0 12px;border-radius:999px;border:1px solid var(--border);background:#fff;font-size:13px;color:var(--text-2);cursor:pointer}
.filters button[aria-pressed=true]{background:var(--brand-deep);border-color:var(--brand-deep);color:#fff}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);box-shadow:var(--shadow-sm);padding:18px 20px}
/* media: a 16:9 box that holds a thumbnail, then the player */
.media{position:relative;aspect-ratio:16/9;border-radius:var(--r-md);overflow:hidden;background:var(--ink)}
.media .player{position:absolute;inset:0;display:block;width:100%;height:100%;padding:0;border:0;background:none;cursor:pointer}
.media img.ph{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.media.v img.ph{object-fit:contain}
.media img.bg{position:absolute;inset:-10%;width:120%;height:120%;object-fit:cover;filter:blur(18px) brightness(.62);transform:scale(1.05)}
.media.tall{aspect-ratio:9/16;width:min(100%,46vh);margin:0 auto}
.media.noimg{background:linear-gradient(135deg,#21363a,#0d1517)}.media.noimg img{display:none}
.media iframe,.media video{position:absolute;inset:0;width:100%;height:100%;border:0;background:#000}
.pbtn{position:absolute;left:50%;top:50%;width:52px;height:52px;margin:-26px 0 0 -26px;border-radius:50%;background:rgba(10,18,20,.62);color:#fff;display:grid;place-items:center;box-shadow:0 4px 14px rgba(0,0,0,.25);transition:background .15s,transform .15s}
.pbtn svg{width:24px;height:24px;margin-left:3px}
.m-open{position:absolute;top:8px;right:8px;width:30px;height:30px;border-radius:8px;background:rgba(10,18,20,.62);color:#fff;display:grid;place-items:center}.m-open svg{width:16px;height:16px}
a.player:hover .m-open{background:var(--brand-600)}
.big .pbtn{width:64px;height:64px;margin:-32px 0 0 -32px}.big .pbtn svg{width:30px;height:30px}
.player:hover .pbtn{background:var(--brand-600);transform:scale(1.06)}
.m-plat,.m-dur{position:absolute;bottom:8px;font-size:12px;line-height:1;padding:5px 7px;border-radius:6px;background:rgba(10,18,20,.66);color:#fff}
.m-plat{left:8px;display:inline-flex;align-items:center;gap:5px}
.m-plat[data-p]::before{content:"";width:6px;height:6px;border-radius:50%;background:#fff}
.m-plat[data-p=bili]::before{background:#fb7299}.m-plat[data-p=yt]::before{background:#ff3b3b}.m-plat[data-p=dy]::before{background:#fe2c55}
.m-plat[data-p=tt]::before{background:#25f4ee}.m-plat[data-p=xhs]::before{background:#ff2442}.m-plat[data-p=x]::before{background:#e7e9ea}.m-dur{right:8px;font-variant-numeric:tabular-nums}
/* headline */
.hl-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.hl{display:flex;flex-direction:column;gap:14px;padding:14px 14px 18px}
.hl.lead{grid-column:1/-1;display:grid;grid-template-columns:minmax(0,7fr) minmax(0,5fr);gap:22px;padding:16px}
.hl.no-media{padding:18px 20px}
.hl-media{display:flex;flex-direction:column;gap:8px;min-width:0}
.hl-body{display:flex;flex-direction:column;gap:8px;min-width:0;padding:0 6px}
.hl.lead .hl-body{padding:6px 6px 4px 0}
.hl-top{display:flex;align-items:center;gap:10px}
.grade{flex:none;height:26px;padding:0 10px;border-radius:7px;display:inline-grid;place-items:center;font-weight:720;font-size:13px;letter-spacing:.04em}
.g-s{background:var(--brand-deep);color:#fff}.g-a{background:var(--brand-100);color:var(--brand-deep)}.g-b{background:#e6f0fa;color:var(--info)}.g-c{background:var(--subtle);color:var(--muted);border:1px solid var(--border)}
.verdict{color:var(--brand-deep);font-weight:680;font-size:14.5px}

.hl h3{margin:0;font-size:19px;line-height:1.4;font-weight:720}.hl.lead h3{font-size:23px}
.facts{font-size:13px;line-height:1.7;color:var(--muted);font-variant-numeric:tabular-nums}.facts .label{margin-right:8px}
.why{margin:0;font-size:15px;line-height:1.7;color:var(--text-2)}
.pt{margin:0;font-size:14px;line-height:1.65;color:var(--text-2);display:flex;gap:8px;align-items:baseline}
.pt b{flex:none;font-size:12px;padding:1px 7px;border-radius:6px;font-weight:650}
.k-next{background:var(--brand-50);color:var(--brand-deep);border:1px solid var(--brand-100)}.k-risk{background:#fff4ea;color:var(--warning);border:1px solid #f4dfc6}
.more{font-size:14px;color:var(--text-2)}.more summary,.basis summary{cursor:pointer;color:var(--brand-700);font-weight:600;width:max-content;font-size:13px;list-style:none;padding:4px 0}
.more summary::-webkit-details-marker,.basis summary::-webkit-details-marker{display:none}
.more summary::after,.basis summary::after{content:" ›";display:inline-block;transition:transform .15s}
.more[open] summary::after,.basis[open] summary::after{transform:rotate(90deg)}
.more p{margin:4px 0 0;line-height:1.75}
.cap{display:flex;flex-direction:column;gap:1px;font-size:13px;line-height:1.45;min-width:0}
.cap b{font-weight:620;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cap span{color:var(--muted);font-size:12px}
.cap:hover b{color:var(--brand-deep)}
.strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.mini{position:relative;aspect-ratio:16/9;border:0;padding:0;border-radius:10px;overflow:hidden;background:var(--ink);cursor:pointer}
.mini img.ph{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.92;transition:opacity .15s}
.mini:hover img.ph{opacity:1}.mini.on{outline:2px solid var(--brand-500);outline-offset:1px}
.mini-t{position:absolute;left:0;right:0;bottom:0;padding:14px 7px 5px;font-size:11.5px;line-height:1.3;color:#fff;text-align:left;background:linear-gradient(transparent,rgba(0,0,0,.72));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding-top:2px}
.chip{display:inline-flex;align-items:center;gap:4px;font-size:13px;border:1px solid var(--border);background:var(--subtle);border-radius:999px;padding:3px 10px;color:var(--text-2);min-height:28px}
.chip svg{width:12px;height:12px;color:var(--muted)}a.chip:hover{border-color:var(--brand-500);color:var(--brand-deep)}
/* topics */
.topic{margin-bottom:16px}
.topic-head{display:flex;align-items:center;gap:12px;justify-content:space-between}
.topic-title{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0}.topic-title h3{margin:0;font-size:18px;font-weight:700}
.kind{font-size:12px;color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:1px 7px}
.seen{font-size:13px;color:var(--muted);white-space:nowrap}.deep-link{color:var(--brand-700);font-weight:600}.deep-link:hover{color:var(--brand-deep)}
.label{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;white-space:nowrap}
.l-new{background:var(--brand-deep);color:#fff}.l-up{background:#fff1e3;color:#9a560c;border:1px solid #f1d6b6}
.l-keep{background:#e9f2fb;color:#1f62a6;border:1px solid #cfe0f3}.l-watch{background:var(--subtle);color:var(--muted);border:1px solid var(--border)}
.l-down{background:#f6eef0;color:#a2394a;border:1px solid #ecd3d8}
.summary{margin:6px 0 14px;color:var(--text-2);line-height:1.7}
.vids{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.vids:not(.all)>.vcard:nth-child(n+5){display:none}
.vcard{display:flex;flex-direction:column;gap:3px;min-width:0}
.vcard .media{margin-bottom:5px}
.v-title{font-size:13.5px;line-height:1.45;font-weight:620;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.v-title:hover{color:var(--brand-deep)}
.small-hit{color:var(--warning);font-weight:650;margin-left:6px}
.v-meta{font-size:12px;color:var(--muted);line-height:1.45;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.more-btn{margin-top:12px;height:34px;padding:0 14px;border-radius:999px;border:1px solid var(--border);background:#fff;font-size:13px;color:var(--text-2);cursor:pointer}
.more-btn:hover{border-color:var(--brand-500);color:var(--brand-deep)}
.topic-foot{margin-top:14px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:6px 18px;flex-wrap:wrap;align-items:baseline}
.facts-line{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.basis{font-size:13px;color:var(--text-2)}.basis ul{margin:4px 0 0;padding-left:20px}.basis li{margin:2px 0}.basis[open]{flex-basis:100%}
.tweets{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:start}
.tweets:not(.all)>.tweet:nth-child(n+4){display:none}
.tweet{display:flex;flex-direction:column;gap:8px;border:1px solid var(--border);border-radius:var(--r-md);padding:12px 14px;background:#fff;min-width:0}
.tw-head{font-size:13px;display:flex;flex-wrap:wrap;gap:0 6px}.tw-head span{color:var(--muted)}.tw-head:hover b{color:var(--brand-deep)}
.tw-text{margin:0;font-size:14px;line-height:1.6;color:var(--text-2);display:-webkit-box;-webkit-line-clamp:6;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-line}
.tw-meta{font-size:12px;color:var(--muted)}
/* news, tools */
.news{list-style:none;margin:0;padding:4px 20px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:28px}
.news-item{padding:12px 0;border-top:1px solid var(--border)}
.news-item:nth-child(-n+2){border-top:0}
.n-top{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted)}.n-votes{color:var(--warning);font-weight:650}
.n-title{display:block;margin-top:4px;font-weight:650;font-size:15px;line-height:1.5}.n-title:hover{color:var(--brand-deep)}
.news-item p{margin:3px 0 0;font-size:13.5px;color:var(--text-2);line-height:1.6}.news-item p.use,.tool p.use,.repo p.use{color:var(--brand-700)}
.sub-title{margin:0 0 10px;font-size:16px;font-weight:700}
.repos{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:16px}
.repo{display:flex;flex-direction:column;background:#fff;border:1px solid var(--border);border-radius:var(--r-lg);box-shadow:var(--shadow-sm);overflow:hidden;transition:border-color .15s,box-shadow .15s}
.repo:hover{border-color:var(--brand-500);box-shadow:var(--shadow-md)}
.repo .media{border-radius:0;aspect-ratio:2/1;background:var(--subtle);border-bottom:1px solid var(--border)}
.repo-media img.ph{object-fit:cover}
.repo-body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:4px}
.repo-name{font-weight:680;word-break:break-all}.repo-tags{display:flex;gap:6px;flex-wrap:wrap}
.repo p{margin:2px 0 0;font-size:14px;color:var(--text-2);line-height:1.6}
.tools-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
.col-title{margin:0 0 4px;font-size:16px;font-weight:700}
ul.tools{list-style:none;margin:0;padding:0}
.tool{padding:11px 0;border-top:1px solid var(--border)}.tool:first-child{border-top:0}
.tool-main{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.tool-main a{font-weight:650;word-break:break-all}.tool-main a:hover{color:var(--brand-deep)}
.tool p{margin:3px 0 0;font-size:14px;color:var(--text-2);line-height:1.6}
.tool-meta{margin-top:4px;font-size:12px;color:var(--muted)}.tool-meta a:hover{color:var(--brand-deep)}
.tag{font-size:11.5px;color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:0 6px;white-space:nowrap}.t-hot{color:#9a560c;border-color:#f1d6b6;background:#fff8f0}
.about{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr) minmax(0,1fr);gap:16px}
.about-col{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);box-shadow:var(--shadow-sm);padding:18px 20px}
.about-col h3{margin:0 0 10px;font-size:15.5px;font-weight:700}
.about-col p{margin:0 0 10px;font-size:14px;line-height:1.75;color:var(--text-2)}.about-col p:last-child{margin:0}
.legend{margin:0;display:flex;flex-direction:column;gap:9px}
.lg{display:grid;grid-template-columns:52px 1fr;gap:10px;align-items:baseline}.lg dt{margin:0}.lg dd{margin:0;font-size:13.5px;line-height:1.65;color:var(--text-2)}
.about-foot{margin:12px 2px 0;font-size:12.5px;color:var(--muted)}
.empty{color:var(--muted);font-size:14px}
footer{color:var(--muted);font-size:13px;text-align:center;padding:28px 16px 36px;border-top:1px solid var(--border);margin-top:8px}footer b{color:var(--text-2);font-weight:650}
/* share cards */
.share-btn{position:relative;flex:none;display:inline-flex;align-items:center;gap:5px;height:32px;padding:0 12px 0 10px;border:1px solid var(--border);border-radius:999px;background:#fff;color:var(--text-2);font-size:13px;font-weight:600;cursor:pointer;transition:border-color .15s,color .15s}
.share-btn::after{content:"";position:absolute;inset:-5px -3px}
.share-btn svg{width:15px;height:15px}.share-btn:hover{border-color:var(--brand-500);color:var(--brand-deep)}
.hl-top .share-btn{margin-left:auto}
.head-r{display:flex;align-items:center;gap:12px;flex:none}
.repo-wrap{position:relative;display:flex;border-radius:var(--r-lg)}.repo-wrap>.repo{flex:1;min-width:0}
.repo-wrap .share-btn{position:absolute;top:10px;right:10px;background:rgba(255,255,255,.95);box-shadow:var(--shadow-sm)}
.card.flash,.repo-wrap.flash{animation:flash 2.4s ease-out 1 both}
@keyframes flash{0%{box-shadow:0 0 0 0 rgba(54,165,144,.6),var(--shadow-md)}30%{box-shadow:0 0 0 12px rgba(54,165,144,.16),var(--shadow-md)}100%{box-shadow:0 0 0 3px var(--brand-500),var(--shadow-md)}}
html.sd-lock{overflow:hidden}
.sd{width:min(440px,calc(100vw - 24px));max-width:none;max-height:calc(100vh - 24px);max-height:calc(100dvh - 24px);padding:0;border:0;border-radius:var(--r-lg);background:var(--surface);color:var(--text);box-shadow:0 24px 64px rgba(13,21,23,.28);overflow:hidden}
.sd[open]{display:flex;flex-direction:column}.sd [hidden]{display:none}.sd::backdrop{background:rgba(13,21,23,.58)}
.sd-head{display:flex;align-items:center;gap:12px;padding:8px 8px 8px 20px;border-bottom:1px solid var(--border)}
.sd-head h2{margin:0;font-size:16px;font-weight:700;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sd-x{flex:none;width:40px;height:40px;border:0;border-radius:var(--r-sm);background:none;display:grid;place-items:center;color:var(--muted);cursor:pointer}
.sd-x:hover{background:var(--subtle);color:var(--text)}.sd-x svg{width:20px;height:20px}
.sd-stage{flex:1 1 auto;min-height:0;overflow:auto;background:var(--canvas);padding:16px;display:grid;place-items:center}
.sd-frame{position:relative;width:min(100%,calc((100vh - 236px) * .75));width:min(100%,calc((100dvh - 236px) * .75));aspect-ratio:3/4;border-radius:10px;overflow:hidden;background:#fff;box-shadow:var(--shadow-md)}
.sd-img{position:absolute;inset:0;width:100%;height:100%;display:block;-webkit-touch-callout:default;user-select:auto}
.sd-wait{position:absolute;inset:0;margin:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;font-size:14px;color:var(--muted);background:linear-gradient(135deg,#f5fffc,#f8fbff 58%,#fffaf0)}
.sd-wait::before{content:"";width:28px;height:28px;border-radius:50%;border:3px solid var(--brand-100);border-top-color:var(--brand-600);animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.sd-foot{padding:10px 16px 16px;display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--border)}
.sd-tip{margin:0;min-height:20px;font-size:13px;line-height:1.5;color:var(--muted);text-align:center}
.sd.inapp .sd-tip{color:var(--brand-deep);font-weight:650;font-size:14px}
.sd-acts{display:grid;grid-template-columns:1fr 1fr;gap:10px}.sd.inapp .sd-acts{grid-template-columns:1fr}.sd.inapp .sd-save{display:none}
.sd-acts button{height:44px;border-radius:var(--r-sm);font-size:15px;font-weight:650;cursor:pointer}
.sd-save{background:var(--brand-deep);border:1px solid var(--brand-deep);color:#fff}.sd-save:hover{background:var(--brand-700)}
.sd-copy{background:#fff;border:1px solid var(--border-strong);color:var(--text-2)}.sd-copy:hover{border-color:var(--brand-500);color:var(--brand-deep)}
.sd-acts button:disabled{background:var(--subtle);border-color:var(--border);color:#9aa0a6;cursor:default}
.toast{position:fixed;left:50%;bottom:24px;z-index:40;max-width:calc(100vw - 32px);transform:translate(-50%,16px);opacity:0;pointer-events:none;background:var(--text);color:#fff;font-size:14px;padding:10px 16px;border-radius:999px;box-shadow:var(--shadow-md);transition:opacity .2s,transform .2s}
.toast.on{opacity:1;transform:translate(-50%,0)}
@media (max-width:1024px){.about{grid-template-columns:1fr 1fr}.about-col:first-child{grid-column:1/-1}}
@media (max-width:1024px){.tweets{grid-template-columns:repeat(2,minmax(0,1fr))}.repos{grid-template-columns:repeat(2,minmax(0,1fr))}.hl.lead{grid-template-columns:1fr}}
@media (max-width:760px){.nav-in,.wrap,.tabs-in{padding:0 16px}.brand small{display:none}
.mast .wrap{padding-top:18px;padding-bottom:16px}.mast h1{font-size:23px;margin-top:10px}.mast .lead{font-size:15px}
.rise{font-size:13.5px}.about{grid-template-columns:1fr}
.hl-grid{grid-template-columns:1fr;gap:12px}.hl,.hl.lead{padding:10px 10px 16px;gap:12px}.hl-body{padding:0 4px}.hl h3,.hl.lead h3{font-size:18px}
.card{padding:14px}.topic-head{align-items:flex-start}.topic-title{flex:1}.head-r{gap:8px}.seen{font-size:0}.seen .deep-link{font-size:13px}
.share-btn{height:34px;padding:0 10px 0 9px}
.filters{margin-left:0;width:100%}
/* phone: samples become a swipeable row so a topic stays one screen tall */
.vids,.tweets{display:flex;align-items:flex-start;overflow-x:auto;scroll-snap-type:x mandatory;gap:10px;margin:0 -14px;padding:0 14px 4px;scrollbar-width:none}
.vids::-webkit-scrollbar,.tweets::-webkit-scrollbar{display:none}
.vids>.vcard,.vids:not(.all)>.vcard:nth-child(n+4),.vids:not(.all)>.vcard:nth-child(n+5){display:flex;flex:0 0 78%;scroll-snap-align:start}
.tweets>.tweet,.tweets:not(.all)>.tweet:nth-child(n+4){display:flex;flex:0 0 86%;scroll-snap-align:start}
.more-btn{display:none}
.news{grid-template-columns:1fr;padding:2px 14px}.news-item:nth-child(2){border-top:1px solid var(--border)}
.repos{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:10px;margin:0 -16px 14px;padding:0 16px 4px;scrollbar-width:none}
.repos::-webkit-scrollbar{display:none}.repo-wrap{flex:0 0 82%;scroll-snap-align:start}
.tools-grid{grid-template-columns:1fr}
.tabs-in{overflow-x:auto;scrollbar-width:none}.tabs-in::-webkit-scrollbar{display:none}.tabs a{padding:10px 10px}
.daypick select{max-width:128px}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
"""

JS = """
(function(){
var cur=null;
function mount(box,kind,src,label){
  if(!box||!src)return;
  if(cur&&cur.box!==box){cur.box.innerHTML=cur.html;cur.box.classList.remove('tall');}
  if(!cur||cur.box!==box){cur={box:box,html:box.innerHTML};}
  var el;
  if(kind==='video'){el=document.createElement('video');el.controls=true;el.autoplay=true;el.playsInline=true;el.preload='metadata';el.src=src;}
  else{el=document.createElement('iframe');el.allow='autoplay; encrypted-media; picture-in-picture; fullscreen';
    /* players get the page origin (YouTube refuses embeds without one); everything else, X's mp4 included, gets no referrer */
    el.allowFullscreen=true;el.referrerPolicy='strict-origin-when-cross-origin';el.title=label||'视频';el.src=src;}
  box.classList.toggle('tall',src.indexOf('open.douyin.com')>=0);
  box.innerHTML='';box.appendChild(el);
}
document.addEventListener('click',function(ev){
  var p=ev.target.closest('button.player');
  if(p){mount(p.closest('.media'),p.dataset.kind,p.dataset.src,p.getAttribute('aria-label'));return;}
  var m=ev.target.closest('.mini');
  if(m){var card=m.closest('.hl-media');card.querySelectorAll('.mini').forEach(function(x){x.classList.toggle('on',x===m)});
    mount(card.querySelector('.media'),m.dataset.kind,m.dataset.src,m.getAttribute('aria-label'));return;}
  var b=ev.target.closest('.more-btn');
  if(b){var list=b.previousElementSibling,open=list.classList.toggle('all');b.setAttribute('aria-expanded',open);
    var t=b.textContent;b.textContent=b.dataset.less;b.dataset.less=t;return;}
  var f=ev.target.closest('.filters button');
  if(f){var bar=f.parentNode,want=f.dataset.label,target=document.getElementById(bar.dataset.target);
    bar.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',x===f?'true':'false')});
    target.querySelectorAll('.topic').forEach(function(c){c.hidden=!!want&&want.split(',').indexOf(c.dataset.label)<0;});}
});
document.addEventListener('error',function(ev){var t=ev.target;if(t.tagName==='IMG'){var m=t.closest('.media,.mini');if(m)m.classList.add('noimg');}},true);
var sel=document.getElementById('daysel');if(sel){sel.addEventListener('change',function(){location.href=sel.value;});}
var links=[].slice.call(document.querySelectorAll('.tabs a'));
var obs=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting){links.forEach(function(a){
  var on=a.getAttribute('href')==='#'+en.target.id;a.classList.toggle('on',on);if(on&&a.scrollIntoView&&a.parentNode.scrollWidth>a.parentNode.clientWidth)a.parentNode.scrollLeft=a.offsetLeft-16;})}})},{rootMargin:'-140px 0px -60% 0px'});
links.forEach(function(a){var s=document.querySelector(a.getAttribute('href'));if(s)obs.observe(s);});
})();
"""

SHARE_JS = r"""
(function(){
/* Share cards: a 1080x1440 PNG drawn on a canvas from the specs in #share-data (radar/share.py). */
var src=document.getElementById('share-data'),dlg=document.getElementById('share-dlg');
if(!src||!dlg)return;
var D=JSON.parse(src.textContent),M=D.meta,ITEMS={};D.items.forEach(function(x){ITEMS[x.id]=x;});
var W=1080,H=1440,FF='-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif';
var LB={'新起':['#006659','#fff'],'爆发':['#006659','#fff'],'升温':['#fff1e3','#9a560c'],'热议':['#fff1e3','#9a560c'],'持续':['#e9f2fb','#1f62a6'],
  '关注':['#f7f9fa','#757980'],'观察':['#f7f9fa','#757980'],'回落':['#f6eef0','#a2394a'],'今日上榜':['#fff8f0','#9a560c']};
var GR={'必看':['#006659','#fff'],'推荐':['#d9f5ef','#006659'],'参考':['#e6f0fa','#2877c7'],'观察':['#f7f9fa','#757980']};
var PC={bili:'#fb7299',yt:'#ff3b3b',dy:'#fe2c55',tt:'#25f4ee',xhs:'#ff2442',x:'#e7e9ea'};
var UA=navigator.userAgent,INAPP=/MicroMessenger|Lark|Feishu|DingTalk|QQ\/|Weibo|AlipayClient|aweme|BytedanceWebview/i.test(UA),
  MOBILE=/iPhone|iPad|iPod|Android|Mobile/i.test(UA)||(navigator.maxTouchPoints>1&&/Macintosh/.test(UA));
function font(c,w,s){c.font=w+' '+s+'px '+FF;}
function rr(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath();}
var NOSTART='，。、；：？！）】」』》”’,.;:?!)%…';
function wrap(c,s,w,max){   /* greedy lines: CJK by character, latin by word; the last line gets an ellipsis */
  var tk=[];((s||'').replace(/\s+/g,' ').trim().match(/[A-Za-z0-9][\w.+\-'%\/:#@&]*|[\s\S]/gu)||[]).forEach(function(t){
    if(t.length>1&&c.measureText(t).width>w)tk.push.apply(tk,Array.from(t));else tk.push(t);});
  var L=[],cur='';
  for(var i=0;i<tk.length;i++){var t=tk[i];
    if(!cur&&t===' ')continue;
    if(!cur||c.measureText(cur+t).width<=w||NOSTART.indexOf(t)>=0){cur+=t;continue;}
    L.push(cur.replace(/ +$/,''));cur=t===' '?'':t;
    if(L.length===max)break;}
  if(L.length<max){if(cur)L.push(cur);return L;}
  var last=L[max-1];while(last&&c.measureText(last+'…').width>w)last=last.slice(0,-1);
  L[max-1]=last.replace(/[\s，。、；：,.;:]+$/,'')+'…';return L;}
function clip(c,s,w){return wrap(c,s,w,1)[0]||'';}
function pill(c,x,y,h,text,bg,fg,size,weight,border){font(c,weight,size);var w=Math.round(c.measureText(text).width+h*.8);
  rr(c,x,y,w,h,h/2);c.fillStyle=bg;c.fill();if(border){c.strokeStyle=border;c.lineWidth=2;c.stroke();}
  c.fillStyle=fg;c.textBaseline='middle';c.fillText(text,x+h*.4,y+h/2+1);c.textBaseline='top';return w;}
function backdrop(c){var g=c.createLinearGradient(0,0,W,H);g.addColorStop(0,'#f5fffc');g.addColorStop(.58,'#f8fbff');g.addColorStop(1,'#fffaf0');
  c.fillStyle=g;c.fillRect(0,0,W,H);
  [[90,0,660,'rgba(54,165,144,.2)'],[1000,150,520,'rgba(40,119,199,.09)']].forEach(function(r){
    var q=c.createRadialGradient(r[0],r[1],0,r[0],r[1],r[2]);q.addColorStop(0,r[3]);q.addColorStop(1,'rgba(255,255,255,0)');c.fillStyle=q;c.fillRect(0,0,W,H);});}
function logo(c,x,y,s){var g=c.createLinearGradient(x,y,x+s,y+s);g.addColorStop(0,'#36a590');g.addColorStop(1,'#006659');
  rr(c,x,y,s,s,s*.29);c.fillStyle=g;c.fill();var k=s*16/28/24;
  c.save();c.translate(x+s*6/28,y+s*6/28);c.scale(k,k);c.strokeStyle='#fff';c.lineWidth=2.2;c.lineCap='round';
  ['M12 3a9 9 0 0 1 9 9','M12 7a5 5 0 0 1 5 5','M3 12a9 9 0 0 0 9 9'].forEach(function(d){c.stroke(new Path2D(d));});
  c.beginPath();c.arc(12,12,3,0,Math.PI*2);c.stroke();c.restore();}
function fit(c,img,sx,sy,sw,sh,x,y,w,h){var r=Math.max(w/sw,h/sh),dw=w/r,dh=h/r;c.drawImage(img,sx+(sw-dw)/2,sy+(sh-dh)/2,dw,dh,x,y,w,h);}
function cover(c,it,img,x,y,w,h){
  var flat=it.pkey==='gh';   /* GitHub's preview image is a white 2:1 card: no shade, no corner badge */
  c.save();rr(c,x,y,w,h,24);c.clip();
  var g=c.createLinearGradient(x,y,x+w,y+h);g.addColorStop(0,'#14443e');g.addColorStop(1,'#0d1517');c.fillStyle=flat?'#fff':g;c.fillRect(x,y,w,h);
  if(img){var iw=img.naturalWidth,ih=img.naturalHeight,sx=0,sw=iw;
    if(it.vertical||ih>iw*1.1){if(iw>ih){sw=Math.min(iw,ih*9/16);sx=(iw-sw)/2;}   /* a Shorts thumbnail is a 9:16 frame inside a 4:3 one */
      var t=document.createElement('canvas');t.width=24;t.height=Math.max(8,Math.round(24*h/w));fit(t.getContext('2d'),img,sx,0,sw,ih,0,0,t.width,t.height);
      c.imageSmoothingEnabled=true;c.drawImage(t,x-30,y-30,w+60,h+60);c.fillStyle='rgba(8,14,16,.4)';c.fillRect(x,y,w,h);
      var dw=Math.min(w,h*sw/ih);c.drawImage(img,sx,0,sw,ih,x+(w-dw)/2,y,dw,h);}
    else fit(c,img,0,0,iw,ih,x,y,w,h);}
  else if(it.quote&&it.quote.t){c.fillStyle='rgba(54,165,144,.55)';font(c,800,200);c.textAlign='right';c.fillText('\u201d',x+w-36,y+6);c.textAlign='left';
    c.fillStyle='#fff';font(c,500,34);var n=Math.max(1,Math.floor((h-260)/54)),ql=wrap(c,it.quote.t,w-120,n);
    ql.forEach(function(l,i){c.fillText(l,x+60,y+112+i*54);});
    c.fillStyle='rgba(255,255,255,.66)';font(c,500,24);c.fillText(clip(c,'\u2014 '+it.quote.who,w-120),x+60,y+112+ql.length*54+18);}
  else{c.strokeStyle='rgba(54,165,144,.32)';c.lineWidth=3;for(var k=1;k<=6;k++){c.beginPath();c.arc(x+w-60,y+h+40,k*95,Math.PI,Math.PI*1.5);c.stroke();}}
  if(!flat&&(img||it.plat)){var s2=c.createLinearGradient(0,y+h-160,0,y+h);s2.addColorStop(0,'rgba(0,0,0,0)');s2.addColorStop(1,'rgba(0,0,0,.5)');c.fillStyle=s2;c.fillRect(x,y+h-160,w,160);}
  c.restore();
  if(flat){rr(c,x+1,y+1,w-2,h-2,23);c.strokeStyle='#e2e8ea';c.lineWidth=2;c.stroke();}
  if(it.label){var lb=LB[it.label]||LB['观察'];c.save();c.shadowColor='rgba(0,0,0,.22)';c.shadowBlur=14;c.shadowOffsetY=3;pill(c,x+24,y+24,54,it.label,lb[0],lb[1],28,760);c.restore();}
  if(it.plat&&!flat){var tx=it.plat+(it.cap?' \u00b7 '+it.cap:'');font(c,600,23);var pw=c.measureText(tx).width+58;
    rr(c,x+24,y+h-70,pw,46,12);c.fillStyle='rgba(10,18,20,.7)';c.fill();
    c.fillStyle=PC[it.pkey]||'#fff';c.beginPath();c.arc(x+46,y+h-47,6,0,Math.PI*2);c.fill();
    c.fillStyle='#fff';c.textBaseline='middle';c.fillText(tx,x+62,y+h-46);c.textBaseline='top';}}
function draw(it,img){
  var cv=document.createElement('canvas');cv.width=W;cv.height=H;var c=cv.getContext('2d');c.textBaseline='top';
  backdrop(c);
  logo(c,48,40,64);
  c.fillStyle='#242528';font(c,780,36);c.fillText(M.site,128,44);
  c.fillStyle='#757980';font(c,500,22);c.fillText((M.by?M.by+' · ':'')+'每日 AI 视频情报',128,90);
  c.textAlign='right';c.fillStyle='#242528';font(c,740,32);c.fillText(M.date,W-48,46);
  c.fillStyle='#757980';font(c,500,22);c.fillText(M.week+' · 第 '+M.issue+' 期',W-48,90);c.textAlign='left';
  var px=32,py=140,pw=W-64,ph=H-py-32,stub=232,sy=py+ph-stub;
  c.save();c.shadowColor='rgba(17,72,65,.14)';c.shadowBlur=48;c.shadowOffsetY=16;rr(c,px,py,pw,ph,32);c.fillStyle='#fff';c.fill();c.restore();
  /* the text decides its height; the cover takes what is left, up to 16:9 */
  var bx=px+48,bw=pw-96,st=(it.stats||[]).length?112:0;
  font(c,760,54);var tl=wrap(c,it.title,bw,2);font(c,400,30);var wl=wrap(c,it.line,bw,3);
  function body(){return 36+46+24+tl.length*72+16+wl.length*48+(st?30+st:0)+36;}
  var cx=px+24,cy=py+24,cw=pw-48,full=Math.round(cw/(it.pkey==='gh'?2:16/9)),ch=sy-cy-body();
  if(ch<450&&wl.length>2){wl=wrap(c,it.line,bw,2);ch=sy-cy-body();}
  var spare=Math.max(0,ch-full);ch-=spare;
  cover(c,it,img,cx,cy,cw,ch);
  var y=cy+ch+36+Math.round(spare/2);
  if(it.grade){var g=GR[it.grade]||GR['观察'],gw=pill(c,bx,y,46,it.grade,g[0],g[1],25,740);
    c.fillStyle='#006659';font(c,700,28);c.textBaseline='middle';c.fillText(clip(c,it.verdict,bw-gw-20),bx+gw+18,y+24);c.textBaseline='top';}
  else{var sw=pill(c,bx,y,46,it.sec||'',"#e9fffc","#006659",24,700,'#d9f5ef');
    c.fillStyle='#757980';font(c,500,25);c.textBaseline='middle';c.fillText(clip(c,it.sub||'',bw-sw-20),bx+sw+18,y+24);c.textBaseline='top';}
  y+=46+24;
  c.fillStyle='#242528';font(c,760,54);tl.forEach(function(l,i){c.fillText(l,bx,y+9+i*72);});y+=tl.length*72+16;
  c.fillStyle='#3b3d40';font(c,400,30);wl.forEach(function(l,i){c.fillText(l,bx,y+9+i*48);});
  if(st){var ty=sy-36-st,n=it.stats.length,colw=bw/n;rr(c,bx,ty,bw,st,20);c.fillStyle='#f7f9fa';c.fill();c.strokeStyle='#e2e8ea';c.lineWidth=2;c.stroke();
    it.stats.forEach(function(s,i){var x=bx+colw*i+28,size=46;font(c,760,size);while(size>30&&c.measureText(s[0]).width>colw-44){size-=2;font(c,760,size);}
      c.fillStyle=i===0?'#006659':'#242528';c.fillText(s[0],x,ty+18+(46-size)/2);
      c.fillStyle='#757980';font(c,500,22);c.fillText(clip(c,s[1],colw-44),x,ty+74);
      if(i){c.fillStyle='#e2e8ea';c.fillRect(bx+colw*i,ty+24,2,st-48);}});}
  /* ticket stub: perforation with notches, then the QR code */
  c.save();c.beginPath();c.arc(px,sy,18,0,Math.PI*2);c.arc(px+pw,sy,18,0,Math.PI*2);c.clip();backdrop(c);c.restore();
  c.strokeStyle='#e2e8ea';c.lineWidth=2;c.beginPath();c.arc(px,sy,18,-Math.PI/2,Math.PI/2);c.stroke();c.beginPath();c.arc(px+pw,sy,18,Math.PI/2,Math.PI*1.5);c.stroke();
  c.save();c.setLineDash([12,10]);c.strokeStyle='#cbd7d9';c.lineWidth=2;c.beginPath();c.moveTo(px+34,sy);c.lineTo(px+pw-34,sy);c.stroke();c.restore();
  var q=it.qr,n2=q.n,mod=Math.floor(190/n2),qs=mod*n2,qx=px+pw-48-qs,qy=Math.round(sy+(stub-qs)/2);
  c.fillStyle='#0d1517';
  for(var i=0;i<n2*n2;i++){if((parseInt(q.m.charAt(i>>2),16)>>(3-(i&3)))&1)c.fillRect(qx+(i%n2)*mod,qy+Math.floor(i/n2)*mod,mod,mod);}
  var tw=qx-36-bx;
  c.fillStyle='#242528';font(c,760,36);c.fillText('扫码看完整情报',bx,sy+52);
  c.fillStyle='#757980';font(c,500,24);c.fillText(clip(c,'原作品、完整数据和判断依据都在这一期',tw),bx,sy+110);
  c.fillStyle='#087f73';font(c,650,24);c.fillText(clip(c,M.host+(M.by?' · '+M.by+'出品':''),tw),bx,sy+152);
  return cv;}
function load(u,cb){if(!u){cb(null);return;}var img=new Image(),done=false;
  function fin(x){if(!done){done=true;cb(x);}}
  if(/^https?:/.test(u))img.crossOrigin='anonymous';   /* covers not copied next to the page: export only works with CORS */
  img.referrerPolicy='no-referrer';img.onload=function(){fin(img.naturalWidth?img:null);};img.onerror=function(){fin(null);};
  img.src=u;setTimeout(function(){fin(null);},8000);}
var $=function(s){return dlg.querySelector(s);},imgEl=$('.sd-img'),wait=$('.sd-wait'),tip=$('.sd-tip'),saveB=$('.sd-save'),copyB=$('.sd-copy');
var cur=null,opener=null;
dlg.classList.toggle('inapp',INAPP);
if(MOBILE&&!INAPP&&navigator.canShare)saveB.textContent='保存到相册';
function say(t){tip.textContent=t;}
function hint(){say(INAPP?'长按上面的图片，选「保存图片」或直接发给朋友':MOBILE?'也可以长按上面的图片保存':'1080 × 1440，适合朋友圈和小红书');}
function open(id,btn){var it=ITEMS[id];if(!it)return;cur=it;opener=btn;
  $('#sd-t').textContent='分享卡片 · '+it.title;imgEl.hidden=true;imgEl.removeAttribute('src');wait.hidden=false;saveB.disabled=copyB.disabled=true;say('');
  if(dlg.showModal){if(!dlg.open)dlg.showModal();}else dlg.setAttribute('open','');
  document.documentElement.classList.add('sd-lock');
  if(it.png){shown(it);return;}
  load(it.cover,function(img){var url;
    try{url=draw(it,img).toDataURL('image/png');}catch(e){url=draw(it,null).toDataURL('image/png');}   /* a tainted canvas: draw without the cover */
    it.png=url;if(cur===it)shown(it);});}
function shown(it){imgEl.src=it.png;imgEl.alt=it.title+' 的分享卡片';imgEl.hidden=false;wait.hidden=true;saveB.disabled=copyB.disabled=false;hint();}
function fname(it){return (M.site+'-'+M.day+'-'+it.title).replace(/[\\\/:*?"<>|\s]+/g,'')+'.png';}
function bytes(u){var b=atob(u.split(',')[1]),a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a;}
function save(){var it=cur;if(!it||!it.png)return;var name=fname(it),data=bytes(it.png);
  if(MOBILE&&navigator.canShare){var f=new File([data],name,{type:'image/png'});
    if(navigator.canShare({files:[f]})){navigator.share({files:[f]}).then(function(){say('选「存储图像」就能存进相册');},
      function(e){if(!e||e.name!=='AbortError')say('没能打开系统分享，长按上面的图片保存');});return;}}
  var url=URL.createObjectURL(new Blob([data],{type:'image/png'})),a=document.createElement('a');
  a.href=url;a.download=name;dlg.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(url);},30000);
  say(MOBILE?'如果没有开始下载，长按上面的图片保存':'已保存：'+name);}
function copy(){var u=cur&&cur.url;if(!u)return;
  function old(){var t=document.createElement('textarea');t.value=u;t.setAttribute('readonly','');t.style.cssText='position:fixed;top:0;opacity:0';
    dlg.appendChild(t);t.select();var ok=false;try{ok=document.execCommand('copy');}catch(e){}t.remove();say(ok?'链接已复制':u);}
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(u).then(function(){say('链接已复制');},old);else old();}
function closed(){document.documentElement.classList.remove('sd-lock');cur=null;if(opener&&opener.focus)opener.focus({preventScroll:true});}
function close(){if(dlg.close&&dlg.open)dlg.close();else{dlg.removeAttribute('open');closed();}}
dlg.addEventListener('close',closed);
dlg.addEventListener('click',function(ev){if(ev.target===dlg)close();});
$('.sd-x').addEventListener('click',close);saveB.addEventListener('click',save);copyB.addEventListener('click',copy);
document.addEventListener('click',function(ev){var b=ev.target.closest('[data-share]');if(b){ev.preventDefault();open(b.dataset.share,b);}});
/* landing from a card's QR code: #intel-radar?day=YYYY-MM-DD&item=video-123 (any prefix before the ?) */
var toast=document.getElementById('toast'),tt=0;
function note(t){if(!toast)return;toast.textContent=t;toast.classList.add('on');clearTimeout(tt);tt=setTimeout(function(){toast.classList.remove('on');},3200);}
function land(){var h=location.hash,q=h.indexOf('?');if(q<0)return;var p={};
  h.slice(q+1).split('&').forEach(function(kv){var i=kv.indexOf('=');if(i>0)p[kv.slice(0,i)]=decodeURIComponent(kv.slice(i+1));});
  if(!p.item)return;
  if(p.day&&p.day!==M.day){var o=[].filter.call(document.querySelectorAll('#daysel option'),function(x){return x.textContent===p.day;})[0];
    if(o){location.replace(o.value+h);return;}note('没找到 '+p.day+' 那一期，先看最新一期');return;}
  var t=document.getElementById(p.item);if(!t){note('这一期里没找到分享的那一条');return;}
  if(t.hidden){var bar=document.querySelector('.filters[data-target="'+t.parentNode.id+'"] button');if(bar)bar.click();}
  t.scrollIntoView({block:'start',behavior:'instant'});t.classList.remove('flash');void t.offsetWidth;t.classList.add('flash');
  note('已定位到分享的那一条');}
land();window.addEventListener('hashchange',land);
})();
"""

LOGO = ('<span class="brand-mark" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" '
        'stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 3a9 9 0 0 1 9 9"/>'
        '<path d="M12 7a5 5 0 0 1 5 5"/><path d="M3 12a9 9 0 0 0 9 9"/></svg></span>')



CLOSE_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>')


def share_html(brief: dict) -> str:
    """The share dialog, the toast and the card specs the share buttons draw from."""
    data = json.dumps(share.specs(brief, _PREFIX), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<dialog class="sd" id="share-dlg" aria-labelledby="sd-t">
  <div class="sd-head"><h2 id="sd-t">分享卡片</h2><button class="sd-x" type="button" aria-label="关闭">{CLOSE_ICON}</button></div>
  <div class="sd-stage"><div class="sd-frame"><img class="sd-img" alt="" hidden><p class="sd-wait" role="status">正在生成卡片…</p></div></div>
  <div class="sd-foot"><p class="sd-tip" role="status" aria-live="polite"></p>
    <div class="sd-acts"><button class="sd-save" type="button" disabled>保存图片</button><button class="sd-copy" type="button" disabled>复制链接</button></div></div>
</dialog>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script type="application/json" id="share-data">{data}</script>"""


def _filters(target: str, labels: list[tuple[str, str]], counts: dict) -> str:
    btn = ['<button type="button" data-label="" aria-pressed="true">全部</button>']
    for name, value in labels:
        n = sum(counts.get(v, 0) for v in value.split(","))
        if n:
            btn.append(f'<button type="button" data-label="{e(value)}" aria-pressed="false">{e(name)} {n}</button>')
    return f'<div class="filters" role="group" aria-label="按标签筛选" data-target="{e(target)}">{"".join(btn)}</div>'


def _rising(videos: list[dict], xs: list[dict]) -> str:
    """The first thing to read: today's 新起 / 升温 formats and X bursts, each a jump to its card."""
    picks = [("video", t) for t in by_label(videos) if t["label"] in ("新起", "升温")] + \
            [("x", t) for t in xs if t["label"] == "爆发"]
    picks.sort(key=lambda p: LABEL_ORDER.get(p[1]["label"], 5))
    if not picks:
        return ""
    chips = "".join(
        f'<a class="rise" href="#{sec}-{e(t.get("thread_id"))}">{label(t["label"])}<span>{e(t["name"])}</span>'
        f'<i aria-hidden="true">→</i></a>' for sec, t in picks[:6])
    return f'<div class="rising"><span class="rising-k">今天冒头</span>{chips}</div>'


def _scan_line(brief: dict, videos: list, xs: list) -> str:
    st = brief.get("stats") or {}
    plats = [n for k, n in PLATFORM_NAMES.items() if (st.get("by_source") or {}).get(k)]
    where = "、".join(plats[:6]) + (" 等" if len(plats) > 6 else "")
    return (f"扫描了 {where} {st.get('items') or 0:,} 条公开内容，归出 {len(videos)} 个视频题材、{len(xs)} 个 X 话题"
            if plats else "")


def _sec(no: int, sid: str, title: str, sub: str, extra: str = "") -> str:
    return (f'<div class="block-head"><h2><span class="sec-no">{no:02d}</span>{e(title)}</h2>'
            f'<p>{e(sub)}</p>{extra}</div>')


def page(brief: dict, days: list[str], at_root: bool = False, deep_ids: set | None = None) -> str:
    """``at_root`` is index.html; day pages live in d/. Links are relative so the same files work
    at 127.0.0.1:8792/, under a sub-path behind a proxy, or copied to static hosting."""
    global _PREFIX
    day = brief["day"]
    dpath = "d/" if at_root else ""
    home = "./" if at_root else "../"
    _PREFIX = "" if at_root else "../"
    d = datetime.strptime(day, "%Y-%m-%d")
    sec = brief.get("sections") or {}
    videos, xs = by_label(sec.get("video") or []), by_label(sec.get("x") or [])
    tl, news = sec.get("tools") or {}, sec.get("news") or []
    vc, xc = {}, {}
    for t in videos:
        vc[t["label"]] = vc.get(t["label"], 0) + 1
    for t in xs:
        xc[t["label"]] = xc.get(t["label"], 0) + 1
    idx = days.index(day) if day in days else -1
    newer = days[idx - 1] if idx > 0 else None
    older = days[idx + 1] if 0 <= idx < len(days) - 1 else None
    options = "".join(f'<option value="{dpath}{e(x)}.html"{" selected" if x == day else ""}>{e(x)}</option>' for x in days)
    issue = (brief.get("history_days") or 0) + 1
    blocks = [("headline", "今日头条", "从下面各分区挑出来最值得看的，点画面直接播放", headline_html(brief), ""),
              ("video", "视频新题材", "各平台最近 7 天的 AI 视频，按题材归类，冒头的排在前面",
               f'<div id="video-list">{video_html(videos, f"{home}deep/{day}.html", deep_ids)}</div>',
               _filters('video-list', [('新起', '新起'), ('升温', '升温'), ('持续', '持续'), ('观察', '观察'), ('回落', '回落')], vc)),
              ("x", "X 热议", "中英文 AI 圈最近两天值得知道的事", f'<div id="x-list">{x_html(xs)}</div>',
               _filters('x-list', [('爆发', '爆发'), ('热议', '热议'), ('关注', '关注')], xc))]
    if news:
        blocks.append(("news", "资讯与新产品", "中文 AI 资讯和 Product Hunt 新产品里，和 AI 视频、内容创作有关的", news_html(news), ""))
    blocks += [("tools", "开源与工具", "对 AI 视频和内容创作有用的新仓库、模型和讨论", tools_html(tl), ""),
               ("hot", "平台热搜", "B站热搜、抖音热榜此刻和 AI 相关的词", hot_html(sec.get('hotwords') or []), ""),
               ("about", "关于这份情报", "标签怎么来的、看了哪些地方", about_html(brief), "")]
    tabs = "".join(f'<a href="#{sid}">{e(title)}</a>' for sid, title, *_ in blocks)
    body = "".join(f'<section class="block" id="{sid}">{_sec(i + 1, sid, title, sub, extra)}{html_}</section>'
                   for i, (sid, title, sub, html_, extra) in enumerate(blocks))
    scan = _scan_line(brief, videos, xs)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(config.SITE_NAME)} · {e(day)}</title><meta name="robots" content="noindex">
<meta name="referrer" content="no-referrer">
<style>{CSS}</style></head>
<body>
<nav class="nav"><div class="nav-in">
  <a class="brand" href="{home}">{LOGO}{e(config.SITE_NAME)}<small>{e(_by(" · "))}每日</small></a>
  <div class="daypick">
    <a href="{(dpath + e(older) + '.html') if older else '#'}" aria-label="前一天" aria-disabled="{'false' if older else 'true'}">‹</a>
    <select id="daysel" aria-label="选择日期">{options}</select>
    <a href="{(dpath + e(newer) + '.html') if newer else '#'}" aria-label="后一天" aria-disabled="{'false' if newer else 'true'}">›</a>
  </div>
</div></nav>
<header class="mast"><div class="wrap">
  <div class="mast-top"><span class="eyebrow">每日 AI 视频情报 · 第 {issue} 期</span>
    <span class="mast-date">{d.month} 月 {d.day} 日 星期{WEEK[d.weekday()]} · 北京时间 {e((brief.get('generated_bj') or '')[-5:])} 更新</span></div>
  <h1>今天值得看的 AI 信号</h1>
  {f'<p class="lead">{e(brief.get("summary"))}</p>' if brief.get('summary') else ''}
  {_rising(sec.get("video") or [], sec.get("x") or [])}
  {f'<p class="scan">{e(scan)}</p>' if scan else ''}
</div></header>
<div class="tabs"><div class="tabs-in">{tabs}</div></div>
<main><div class="wrap">{body}</div></main>
<footer><b>{e(config.SITE_NAME)}</b> · {e(_by("出品 · "))}整理自各平台公开内容，每天早上更新</footer>
{share_html(brief)}
<script>{JS}</script>
<script>{SHARE_JS}</script>
</body></html>"""


# ------------------------------------------------------------------ deep dive page

DEEP_CSS = """
.back{display:inline-flex;align-items:center;gap:6px;font-size:14px;color:var(--brand-700);font-weight:600;margin-bottom:8px}
.caveat{font-size:13px;color:var(--muted);margin:10px 0 0;max-width:900px;line-height:1.7}
.jump{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.deep{margin-top:16px;scroll-margin-top:72px}
.deep-grid{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:24px;margin-top:14px}
.first{display:flex;flex-direction:column;gap:6px}
.first-k{font-size:12.5px;color:var(--muted)}
.first-v{font-size:26px;font-weight:760;font-variant-numeric:tabular-nums;line-height:1.2}
.first-v small{font-size:14px;font-weight:600;color:var(--text-2);margin-left:8px}
.first .vcard{margin-top:6px}
.per{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--muted);margin-top:4px}.per b{color:var(--text-2);font-weight:620}
.story p{margin:0 0 10px;line-height:1.75;color:var(--text-2)}.story p b{display:inline-block;font-size:12px;padding:1px 7px;border-radius:6px;margin-right:8px;background:var(--subtle);border:1px solid var(--border);color:var(--text-2);font-weight:650}
.story p.now b{background:var(--brand-50);color:var(--brand-deep);border-color:var(--brand-100)}
.chart{margin:4px 0 14px}
.chart-t{font-size:13px;color:var(--text-2);font-weight:620;margin-bottom:8px}
.bars{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:2px;align-items:end;height:92px;border-bottom:1px solid var(--border-strong)}
.bar{position:relative;display:flex;flex-direction:column;justify-content:flex-end;height:100%;cursor:default}
.bar i{display:block;background:var(--brand-600);border-radius:4px 4px 0 0;min-height:0}
.bar.zero i{height:2px!important;background:var(--border);border-radius:1px}
.bar em{font-style:normal;font-size:11.5px;color:var(--text-2);text-align:center;font-variant-numeric:tabular-nums;margin-bottom:2px}
.bar .tip{position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:var(--text);color:#fff;font-size:12px;padding:4px 8px;border-radius:6px;white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .12s;z-index:2}
.bar:hover .tip,.bar:focus .tip{opacity:1}
.bar:nth-child(-n+2) .tip{left:0;transform:none}.bar:nth-last-child(-n+2) .tip{left:auto;right:0;transform:none}
.bar-x{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:2px;font-size:11px;color:var(--muted);text-align:center;margin-top:4px;font-variant-numeric:tabular-nums}
.chart-n{font-size:12px;color:var(--muted);margin-top:6px}
.chart-empty p{margin:0;padding:12px 14px;border:1px dashed var(--border-strong);border-radius:var(--r-md);font-size:13.5px;color:var(--text-2);background:var(--subtle)}
.hits-t{margin:18px 0 10px;font-size:15px;font-weight:700}
@media (max-width:900px){.deep-grid{grid-template-columns:1fr}}
"""


def _quarter_label(ts: float) -> str:
    d = datetime.fromtimestamp(ts, timezone(timedelta(hours=8)))
    return f"{d.year % 100:02d}.{d.month:02d}"


def _bars(qs: list[dict], term: str) -> str:
    if not qs:
        return ""
    if not any(q["match"] for q in qs):     # an all-zero chart is noise; say it in one line
        return (f'<div class="chart chart-empty"><div class="chart-t">B站「{e(term)}」</div>'
                f'<p>两年来 B站播放靠前的视频里，标题和标签都没有这个词：中文平台基本还没人做。</p></div>')
    top = max(q["match"] for q in qs) or 1
    peak = max(range(len(qs)), key=lambda i: qs[i]["match"])
    cells = []
    for i, q in enumerate(qs):
        tip = f"{_quarter_label(q['start'])} 起三个月：{q['match']} 条"
        value = f'<em>{q["match"]}</em>' if q["match"] and (i == peak or i == len(qs) - 1) else ""   # label peak and latest only
        cells.append(f'<div class="bar{" zero" if not q["match"] else ""}" tabindex="0" aria-label="{e(tip)}">'
                     f'<span class="tip">{e(tip)}</span>{value}'
                     f'<i style="height:{max(4, round(q["match"] / top * 80))}px"></i></div>')
    counts = "、".join(f"{_quarter_label(q['start'])} {q['match']}" for q in qs)
    return f"""<div class="chart"><div class="chart-t">B站「{e(term)}」两年来每季度的同类视频</div>
  <div class="bars">{''.join(cells)}</div><div class="bar-x">{''.join(f'<span>{e(_quarter_label(q["start"]))}</span>' for q in qs)}</div>
  <div class="chart-n">每根柱子是那个季度播放最高的结果里，标题或标签含这个词的 AI 视频条数（最多 20）。逐季：{e(counts)}</div></div>"""


def deep_page(dp: dict, brief: dict) -> str:
    global _PREFIX
    _PREFIX = "../"
    day = dp["day"]
    d = datetime.strptime(day, "%Y-%m-%d")
    sections = []
    for t in dp["topics"]:
        first = t.get("earliest")
        if first:
            older = t.get("bili_older") and first["platform"] == "B站"
            first_html = (f'<div class="first-k">最早能查到</div><div class="first-v">{e(first["published"][:7])}'
                          f'<small>{e(first["platform"])}{"，两年前就有" if older else ""}</small></div>')
            per = "".join(f'<span><b>{e(p)}</b> {e(x["published"][:7])}{"（约）" if p.startswith("YouTube") else ""}'
                          f'{"（近半年内）" if p == "抖音" else ""}</span>' for p, x in (t.get("earliest_by") or {}).items())
            first_html += f'<div class="per">{per}</div>{vcard(first)}'
        else:
            first_html = '<div class="first-k">最早能查到</div><p class="empty">各平台搜不到标题对得上的旧视频。</p>'
        n = t.get("note") or {}
        story = "".join(f'<p class="{cls}"><b>{lab}</b>{e(n[k])}</p>'
                        for k, lab, cls in (("origin", "起源", ""), ("spread", "扩散", ""), ("now", "现在", "now")) if n.get(k))
        hits = t.get("hits") or []
        terms = t.get("terms") or {}
        term_line = " / ".join(x for x in (f"中文「{terms.get('zh')}」" if terms.get("zh") else "",
                                            f"英文「{terms.get('en')}」" if terms.get("en") else "") if x)
        sections.append(f"""
<section class="card deep" id="t{e(t.get('thread_id'))}">
  <header class="topic-head"><div class="topic-title">{label(t['label'])}<h3>{e(t['name'])}</h3><span class="kind">{e(t['kind'])}</span></div>
    <span class="seen">搜索词 {e(term_line)}</span></header>
  <div class="deep-grid">
    <div class="first">{first_html}</div>
    <div>{_bars(t.get('bili_quarters') or [], terms.get('zh') or '')}<div class="story">{story or '<p class="empty">这个题材的说明没有生成。</p>'}</div></div>
  </div>
  <h4 class="hits-t">更多同类爆款 <span class="facts-line">{e(' · '.join(f'{p} {c} 条' for p, c in (t.get('matched') or {}).items() if c))}</span></h4>
  <div class="vids" data-shown="4">{''.join(vcard(s) for s in hits) or '<p class="empty">没有找到今天之外的同类爆款。</p>'}</div>
  {_more_btn(len(hits), 4, '条')}
</section>""")
    jump = "".join(f'<a class="chip" href="#t{e(t.get("thread_id"))}">{e(t["name"])}</a>' for t in dp["topics"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>题材深挖 · {e(config.SITE_NAME)} · {e(day)}</title><meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">
<style>{CSS}{DEEP_CSS}</style></head>
<body>
<nav class="nav"><div class="nav-in"><a class="brand" href="../">{LOGO}{e(config.SITE_NAME)}<small>题材深挖</small></a></div></nav>
<header class="mast"><div class="wrap">
  <a class="back" href="../d/{e(day)}.html#video">← 回到 {d.month} 月 {d.day} 日的情报</a>
  <h1>这些题材是从哪来的</h1>
  <p class="lead">把 {d.month} 月 {d.day} 日{('标「' + '」「'.join(dp['labels']) + '」的') if dp.get('labels') else '识别出的'} {len(dp['topics'])} 个视频题材往回查：最早什么时候出现、后来怎么扩散，再多找一批同类爆款，点画面直接播放。</p>
  <p class="caveat">「最早能查到」是各平台搜索能翻到的最早一条。平台搜索按热度排序，真正的源头可能更早；YouTube 只给“几个月前”这种时间，日期是估算；抖音只能查近半年。北京时间 {e(dp.get('generated_bj'))} 整理。</p>
  <div class="jump">{jump}</div>
</div></header>
<main><div class="wrap">{''.join(sections)}</div></main>
<footer><b>{e(config.SITE_NAME)}</b> · {e(_by("出品 · "))}整理自各平台公开内容</footer>
<script>{JS}</script>
</body></html>"""


PUBLIC_STATS = ("items", "by_source")


def public_brief(brief: dict) -> dict:
    """The day as readers may see it: run costs, call counts, failures and timings are left out."""
    return {**brief, "stats": {k: v for k, v in (brief.get("stats") or {}).items() if k in PUBLIC_STATS}}


def _by(suffix: str) -> str:
    """The publisher followed by ``suffix``, or nothing when RADAR_SITE_BY is empty."""
    return config.SITE_BY + suffix if config.SITE_BY else ""


def write_site(con, brief: dict) -> None:
    config.ensure_dirs()
    days = db.brief_days(con)
    if brief["day"] not in days:
        days = sorted(days + [brief["day"]], reverse=True)
    dp = db.load_deep(con, brief["day"])
    if dp:
        (config.SITE_DIR / "deep").mkdir(exist_ok=True)
        (config.SITE_DIR / "deep" / f"{brief['day']}.html").write_text(deep_page(dp, brief), encoding="utf-8")
    ids = {t.get("thread_id") for t in dp["topics"]} if dp else set()
    (config.SITE_DIR / "d" / f"{brief['day']}.html").write_text(page(brief, days, deep_ids=ids), encoding="utf-8")
    (config.SITE_DIR / "data" / f"{brief['day']}.json").write_text(json.dumps(public_brief(brief), ensure_ascii=False, indent=1),
                                                                 encoding="utf-8")
    if brief["day"] == days[0]:
        (config.SITE_DIR / "index.html").write_text(page(brief, days, at_root=True, deep_ids=ids), encoding="utf-8")
    # older pages get a fresh date list so their pickers include the new day
    for other in days[1:8]:
        r = con.execute("SELECT payload FROM briefs WHERE day=?", (other,)).fetchone()
        if r:
            odp = db.load_deep(con, other)
            (config.SITE_DIR / "d" / f"{other}.html").write_text(
                page(json.loads(r["payload"]), days, deep_ids={t.get("thread_id") for t in odp["topics"]} if odp else set()),
                encoding="utf-8")
    for p in config.SITE_DIR.rglob("*"):
        try:
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
        except OSError:
            pass
