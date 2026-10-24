"""Bilibili through its public web API with a guest cookie (free).

- ``search``: lane keywords, last 7 days, most played first.
- ``popular`` / ``ranking``: the site-wide hot lists, kept only when the title is in the AI lane.
- ``hot_words``: 热搜, kept only when AI-related.
- ``window_count``: how many videos a term has inside a publish window (the novelty probe).
"""

from __future__ import annotations

import re
import time

from .. import config, net
from .common import in_lane, log, strong_ai

API = "https://api.bilibili.com"
_REF = {"Referer": "https://search.bilibili.com/"}
_warmed = False


def _warm() -> None:
    global _warmed
    if not _warmed:
        try:
            net.request("https://www.bilibili.com/", name="bili", timeout=15)
        except net.FetchError:
            pass
        _warmed = True


def _clean(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&amp;", "&").replace("&quot;", '"')


def _card(v: dict, query: str = "") -> dict:
    bvid = v.get("bvid")
    pic = v.get("pic") or ""
    stat = v.get("stat") or {}
    owner = v.get("owner") or {}
    plays = v.get("play") if v.get("play") is not None else stat.get("view")
    return {
        "id": f"bili:{bvid}", "source": "bili", "kind": "video",
        "title": _clean(v.get("title")), "text": _clean(v.get("description") or v.get("desc") or "")[:300],
        "url": f"https://www.bilibili.com/video/{bvid}",
        "thumb": ("https:" + pic) if pic.startswith("//") else pic.replace("http://", "https://"),
        "author": v.get("author") or owner.get("name") or "",
        "author_id": str(v.get("mid") or owner.get("mid") or ""),
        "published_at": float(v.get("pubdate") or v.get("created") or 0) or None,
        "metrics": {
            "views": _int(plays),
            "likes": _int(v.get("like") if v.get("like") is not None else stat.get("like")),
            "comments": _int(v.get("review") if v.get("review") is not None else stat.get("reply")),
            "danmaku": _int(v.get("video_review") if v.get("video_review") is not None else stat.get("danmaku")),
            "favorites": _int(v.get("favorites") if v.get("favorites") is not None else stat.get("favorite")),
        },
        "extra": {"duration": _dur(v.get("duration")), "tname": v.get("typename") or v.get("tname") or "",
                  "tag": v.get("tag") or ""},
        "query": query,
    }


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _dur(v):
    if isinstance(v, (int, float)):
        return int(v)
    parts = [int(p) for p in str(v or "").split(":") if p.isdigit()]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s or None


def _search(keyword: str, begin: int | None = None, end: int | None = None, order: str = "click",
            page: int = 1) -> dict:
    _warm()
    params = {"search_type": "video", "keyword": keyword, "order": order, "page": page}
    if begin:
        params.update(pubtime_begin_s=int(begin), pubtime_end_s=int(end or time.time()))
    data = net.get_json(API + "/x/web-interface/search/type", params=params, headers=_REF, name="bili")
    if data.get("code") != 0:
        raise net.FetchError(f"bili search code {data.get('code')}")
    return data.get("data") or {}


def search(keywords: list[str] | None = None, days: int = config.RECENT_DAYS) -> list[dict]:
    now = time.time()
    out = []
    for kw in keywords or config.BILI_KEYWORDS:
        for page in (1, 2) if kw in config.BILI_KEYWORDS_DEEP else (1,):
            try:
                d = _search(kw, now - days * 86400, now, page=page)
                out += [_card(v, f"bili:{kw}") for v in d.get("result") or [] if v.get("bvid")]
            except net.FetchError as exc:
                log(f"bili search {kw}: {exc}")
                break
            time.sleep(0.8)
    return out


def rank_partitions(rids: list[int] | None = None) -> list[dict]:
    """Partition rankings (config.BILI_RANK_RIDS), kept only when title or description is plainly about AI."""
    _warm()
    out = []
    for rid in rids or config.BILI_RANK_RIDS:
        try:
            d = net.get_json(API + "/x/web-interface/ranking/v2", params={"rid": rid, "type": "all"}, name="bili")
            out += [_card(v, f"bili:rank{rid}") for v in (d.get("data") or {}).get("list") or [] if v.get("bvid")]
        except net.FetchError as exc:
            log(f"bili rank {rid}: {exc}")
        time.sleep(0.5)
    return [it for it in out if strong_ai(it["title"] + " " + it["text"][:120])]


def popular(pages: int = 3) -> list[dict]:
    out = []
    for pn in range(1, pages + 1):
        try:
            d = net.get_json(API + "/x/web-interface/popular", params={"ps": 50, "pn": pn}, name="bili")
            out += [_card(v, "bili:popular") for v in (d.get("data") or {}).get("list") or []]
        except net.FetchError as exc:
            log(f"bili popular: {exc}")
        time.sleep(0.5)
    try:
        d = net.get_json(API + "/x/web-interface/ranking/v2", params={"rid": 0, "type": "all"}, name="bili")
        out += [_card(v, "bili:ranking") for v in (d.get("data") or {}).get("list") or []]
    except net.FetchError as exc:
        log(f"bili ranking: {exc}")
    return [it for it in out if in_lane(it["title"] + " " + it["text"] + " " + it["extra"].get("tname", ""))]


def hot_words() -> list[dict]:
    try:
        d = net.get_json(API + "/x/web-interface/search/square", params={"limit": 50}, name="bili")
    except net.FetchError as exc:
        log(f"bili hot words: {exc}")
        return []
    rows = (((d.get("data") or {}).get("trending") or {}).get("list")) or []
    now = time.time()
    out = []
    for i, r in enumerate(rows):
        kw = r.get("keyword") or r.get("show_name") or ""
        if not kw:
            continue
        out.append({"id": f"bili_hot:{kw}", "source": "bili_hot", "kind": "hotword", "title": r.get("show_name") or kw,
                    "text": "", "url": "https://search.bilibili.com/all?keyword=" + kw, "author": "", "author_id": "",
                    "published_at": now, "metrics": {"rank": i + 1, "heat": r.get("heat_score")},
                    "extra": {"in_lane": in_lane(kw)}, "query": "bili:hot"})
    return out


def followers(mid: str) -> int | None:
    try:
        d = net.get_json(API + "/x/relation/stat", params={"vmid": mid}, name="bili")
        return _int((d.get("data") or {}).get("follower"))
    except net.FetchError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def window_count(term: str, begin: float, end: float) -> dict:
    """Videos matching ``term`` published in [begin, end], most played first.

    ``n`` is the site's result count (fuzzy, capped at 1000). ``match`` is the precise part: of the
    first page (up to 20 most played), how many titles literally contain the term (and, when the term
    itself isn't an AI word, are AI-related by title or tags).
    """
    d = _search(term, begin, end)
    res = d.get("result") or []
    want = _norm(term)
    lane_term = in_lane(term)
    hits = [v for v in res if want and want in _norm(_clean(v.get("title")))
            and (lane_term or in_lane(_clean(v.get("title")) + " " + (v.get("tag") or "")))]
    plays = sorted((_int(v.get("play")) or 0 for v in hits), reverse=True)
    return {"n": int(d.get("numResults") or 0), "page": len(res), "match": len(hits), "top": plays[:3],
            "samples": [{"title": _clean(v.get("title")), "bvid": v.get("bvid"), "play": _int(v.get("play")),
                         "pubdate": v.get("pubdate")} for v in hits[:3]]}
