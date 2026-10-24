"""Share cards: one 1080×1440 picture per item that a reader saves and posts (朋友圈, 小红书).

The browser draws the picture (render.SHARE_JS) from the specs built here, so the page needs no image
library. Everything on a card comes from the stored brief: titles and comments from the analysis, numbers
from code, never a cost, a vendor or an endpoint. The QR code points at the item on the public site
(``config.SHARE_URL``); covers are copied next to the page so the canvas can export them.
"""

from __future__ import annotations

import functools
import hashlib
import html
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import config, net
from .qrcodegen import QrCode
from .score import fmt_num

WEEK = "一二三四五六日"
SECTION = {"head": "今日头条", "video": "视频新题材", "x": "X 热议", "repo": "开源与工具"}
PLAT_KEY = {"B站": "bili", "YouTube": "yt", "YouTube Shorts": "yt", "抖音": "dy", "TikTok": "tt", "小红书": "xhs", "X": "x",
            "GitHub": "gh"}

_YT = re.compile(r"youtube\.com/(?:watch\?v=|shorts/)([\w-]{11})")
_GH = re.compile(r"https://github\.com/([^/]+/[^/#?]+)")


def link(day: str, item: str) -> str:
    return config.SHARE_URL.format(day=day, item=item)


def host() -> str:
    return urllib.parse.urlsplit(config.SHARE_URL).netloc


@functools.lru_cache(maxsize=512)
def qr(text: str) -> dict:
    """The QR code as {"n": modules per side, "m": row-major modules, 4 to a hex digit, dark = 1}."""
    code = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n = code.get_size()
    bits = "".join("1" if code.get_module(x, y) else "0" for y in range(n) for x in range(n))
    bits += "0" * (-len(bits) % 4)
    return {"n": n, "m": "".join(f"{int(bits[i:i + 4], 2):x}" for i in range(0, len(bits), 4))}


# ------------------------------------------------------------------ what a headline points to

def resolve(h: dict, sec: dict) -> tuple[list, list, list]:
    """The topics and tools a headline points to, looked up in the day's sections."""
    vids = {t.get("thread_id"): t for t in sec.get("video") or []}
    xs = {t.get("thread_id"): t for t in sec.get("x") or []}
    tl = sec.get("tools") or {}
    tools = {r["url"]: r for k in ("repos", "models", "stories") for r in tl.get(k) or []}
    tools.update({r["url"]: r for r in sec.get("news") or []})
    vt, xt, tt = [], [], []
    for l in h.get("links") or []:
        if l["section"] == "video" and l.get("thread_id") in vids:
            vt.append(vids[l["thread_id"]])
        elif l["section"] == "x" and l.get("thread_id") in xs:
            xt.append(xs[l["thread_id"]])
        elif l.get("url") in tools:
            tt.append(tools[l["url"]])
    return vt, xt, tt


def why(h: dict) -> str:
    """The one-line reason. Briefs from before the ``why`` field fall back to the comment's second sentence
    (the first one repeats the verdict)."""
    if h.get("why"):
        return h["why"]
    parts = [p for p in re.split(r"(?<=[。！？])", h.get("comment") or "") if p.strip()]
    if len(parts) > 1 and len(parts[0]) <= 14:
        parts = parts[1:]
    return "".join(parts[:1])


# ------------------------------------------------------------------ numbers (code, never the model)

def num(n) -> str:
    """fmt_num without the trailing .0 that reads badly in big type (1337.0万 → 1337万)."""
    return re.sub(r"\.0(?=[万亿])", "", fmt_num(n))


def _fans(n) -> str:
    s = num(n)
    return s + ("粉" if s[-1] in "万亿" else " 粉")


def video_stats(m: dict) -> list[list[str]]:
    o = m.get("outlier") or {}
    hit = (m.get("max_vsr") or 0) >= 5 and o.get("followers") is not None and o.get("views")
    # when one video is nearly the whole total (two accounts, one of them on 抖音 with likes only) say it once
    total = m.get("views_total") and not (hit and o["views"] >= 0.95 * m["views_total"])
    picks = [[str(m["authors"]), "个账号在做"] if m.get("authors") else None,
             [num(m["views_total"]), "合计播放"] if total else None,
             [num(o["views"]), f"{_fans(o['followers'])}的号单条播放"] if hit else None,
             [num(m["likes_total"]), "合计点赞"] if m.get("likes_total") else None,
             [num(m["vph_max"]), "最快每小时播放"] if m.get("vph_max") else None,
             [str(m["videos"]), "条同类视频"] if m.get("videos") else None]
    return [p for p in picks if p][:3]


def x_stats(t: dict) -> list[list[str]]:
    m, b = t.get("metrics") or {}, t.get("breadth") or {}
    if b.get("authors") and b.get("span_hours") is not None and b.get("tweets", 0) >= 15:
        first = [str(b["authors"]), f"个账号 {round(b['span_hours'])} 小时内在聊"]
    else:
        first = [str(m["authors"]), "个账号在说"] if m.get("authors") else None
    picks = [first,
             [num(m["views_total"]), "推文合计浏览"] if m.get("views_total") else None,
             [num(m["likes_total"]), "合计点赞"] if m.get("likes_total") else None]
    return [p for p in picks if p][:3]


def repo_stats(r: dict) -> list[list[str]]:
    picks = [[num(r["stars"]), "GitHub 星标"] if r.get("stars") is not None else None,
             ["+" + num(r["stars_today"]), "今日新增星标"] if r.get("stars_today") else
             ["+" + num(r["stars_per_day"]), "日均新增星标"] if r.get("stars_per_day") else None,
             [r["created"][5:], "创建日期"] if r.get("created") else None]
    return [p for p in picks if p][:3]


# ------------------------------------------------------------------ covers

def repo_image(url: str) -> str:
    m = _GH.match(url or "")
    return f"https://opengraph.githubassets.com/1/{m[1]}" if m else ""


def _vertical(s: dict) -> bool:
    return bool(s.get("vertical") or ((s.get("media") or {}).get("aspect") or 1.7) < 0.9)


def _cap(s: dict) -> str:
    if s.get("views") is not None:
        return f"{num(s['views'])} {'浏览' if s.get('handle') else '播放'}"
    if s.get("likes") is not None:
        return f"{num(s['likes'])} 赞"
    return ""


def _video_cover(s: dict) -> dict:
    thumb = s.get("thumb") or ""
    cands = [thumb] if thumb else []
    y = _YT.search(s.get("url") or "")
    if y and not _vertical(s) and thumb.startswith("http"):
        cands.insert(0, f"https://i.ytimg.com/vi/{y[1]}/maxresdefault.jpg")    # hqdefault is 480 px, letterboxed
    return {"cands": cands, "vertical": _vertical(s), "plat": s.get("platform") or "", "cap": _cap(s)}


def _x_cover(samples: list[dict]) -> dict:
    for s in samples:
        thumb = (s.get("media") or {}).get("thumb") or s.get("thumb")
        if thumb:
            return {"cands": [thumb], "vertical": _vertical(s), "plat": "X", "cap": _cap(s)}
    s = samples[0] if samples else {}
    return {"cands": [], "plat": "X", "cap": _cap(s),
            "quote": {"t": re.sub(r"\s*https://t\.co/\S+", "", html.unescape(s.get("title") or "")).strip(), "who": f"{s.get('author') or ''} @{s.get('handle') or ''}".strip()}}


def _repo_cover(r: dict) -> dict:
    img = repo_image(r.get("url"))
    return {"cands": [img] if img else [], "plat": "GitHub", "cap": ""}


def _name(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _local(url: str) -> str:
    """img/<name>.<ext> when the cover was copied next to the page, else ''."""
    if not url.startswith("http"):
        return url
    for ext in ("jpg", "webp", "png"):
        if (config.SITE_DIR / "img" / f"{_name(url)}.{ext}").exists():
            return f"img/{_name(url)}.{ext}"
    return ""


def _pick(cands: list[str]) -> str:
    """The first cached candidate, else the first remote one (the browser then tries it with CORS)."""
    for u in cands:
        if (loc := _local(u)):
            return loc
    return next((u for u in cands if u.startswith("http")), "")


def cache_covers(brief: dict) -> dict:
    """Copy each card's cover into site/img/ (same names as pipeline.cache_samples) so the canvas that draws
    the card stays exportable: an image from another origin without CORS headers taints it. Covers that are
    already local are skipped; the first candidate that downloads wins."""
    from .pipeline import _image_ext        # pipeline imports render, which imports this module
    img_dir = config.SITE_DIR / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    jobs = list({tuple(c["cands"]): None for c in (spec["_cover"] for spec in _raw_specs(brief))
                 if c["cands"] and not any(_local(u) for u in c["cands"])})      # a headline shares its topic's cover

    def fetch(cands):
        for url in cands:
            try:
                raw = net.request(url, name="thumbs", timeout=20, retries=1)
            except net.FetchError:
                continue
            ext = _image_ext(raw)
            if ext and len(raw) <= 3_000_000:
                (img_dir / f"{_name(url)}.{ext}").write_bytes(raw)
                return True
        return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        got = sum(pool.map(fetch, jobs))
    return {"wanted": len(jobs), "cached": got}


# ------------------------------------------------------------------ specs

def _raw_specs(brief: dict) -> list[dict]:
    sec = brief.get("sections") or {}
    out = []
    for i, h in enumerate(brief.get("headline") or []):
        vt, xt, tt = resolve(h, sec)
        grade = (h.get("grade") or "C").strip().upper()[:1]
        spec = {"id": f"head-{i + 1}", "grade": {"S": "必看", "A": "推荐", "B": "参考"}.get(grade, "观察"),
                "verdict": h.get("verdict") or "", "title": h.get("title") or "", "line": why(h), "label": "",
                "stats": [], "_cover": {"cands": [], "plat": "", "cap": ""}}
        if vt:
            spec.update(label=vt[0].get("label") or "", stats=video_stats(vt[0]["metrics"]),
                        _cover=_video_cover(vt[0]["samples"][0]) if vt[0].get("samples") else spec["_cover"])
        elif xt:
            spec.update(label=xt[0].get("label") or "", stats=x_stats(xt[0]),
                        _cover=_x_cover([s for t in xt for s in t.get("samples") or []]))
        elif tt:
            spec.update(stats=repo_stats(tt[0]) if tt[0].get("stars") is not None else [], _cover=_repo_cover(tt[0]))
        out.append(spec)
    for t in sec.get("video") or []:
        if t.get("thread_id") is None:
            continue
        out.append({"id": f"video-{t['thread_id']}", "sec": SECTION["video"], "sub": t.get("kind") or "",
                    "label": t.get("label") or "", "title": t.get("name") or "", "line": t.get("summary") or "",
                    "stats": video_stats(t["metrics"]),
                    "_cover": _video_cover(t["samples"][0]) if t.get("samples") else {"cands": [], "plat": "", "cap": ""}})
    for t in sec.get("x") or []:
        if t.get("thread_id") is None:
            continue
        out.append({"id": f"x-{t['thread_id']}", "sec": SECTION["x"], "sub": t.get("kind") or "",
                    "label": t.get("label") or "", "title": t.get("name") or "", "line": t.get("summary") or "",
                    "stats": x_stats(t), "_cover": _x_cover(t.get("samples") or [])})
    for i, r in enumerate((sec.get("tools") or {}).get("repos") or []):
        line = (r.get("zh") or r.get("desc") or "").rstrip("。")
        if r.get("use"):
            line += "。" + r["use"].rstrip("。")
        out.append({"id": f"repo-{i + 1}", "sec": SECTION["repo"], "sub": r.get("group") or "",
                    "label": "今日上榜" if r.get("from_trending") else "", "title": r.get("name") or "",
                    "line": line + "。", "stats": repo_stats(r), "_cover": _repo_cover(r)})
    return out


def specs(brief: dict, prefix: str = "") -> dict:
    """What the page embeds for its share buttons: one spec per card plus the masthead bits. ``prefix`` is
    the page's path back to the site root (local covers live in site/img/)."""
    day = brief["day"]
    d = datetime.strptime(day, "%Y-%m-%d")
    items = []
    for spec in _raw_specs(brief):
        c = spec.pop("_cover")
        cover = _pick(c["cands"])
        spec.update(cover=(prefix + cover) if cover and not cover.startswith("http") else cover,
                    vertical=bool(c.get("vertical")), plat=c.get("plat") or "", pkey=PLAT_KEY.get(c.get("plat"), ""),
                    cap=c.get("cap") or "", url=link(day, spec["id"]), qr=qr(link(day, spec["id"])))
        if c.get("quote") and not cover:
            spec["quote"] = c["quote"]
        items.append(spec)
    meta = {"day": day, "site": config.SITE_NAME, "by": config.SITE_BY, "date": f"{d.month} 月 {d.day} 日",
            "week": "星期" + WEEK[d.weekday()], "issue": (brief.get("history_days") or 0) + 1, "host": host()}
    return {"meta": meta, "items": items}
