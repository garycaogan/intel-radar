"""YouTube through TikHub (billed per call; there is no local Data API key).

``search`` uses the v2 general search: this week's uploads, most viewed first, long videos and
Shorts in one response. Channel follower counts come from ``channel_followers`` for the few
candidates that need an outlier ratio.
"""

from __future__ import annotations

from .. import config, net
from .common import count, log, relative_ts

V2 = "/api/v1/youtube/web_v2/get_general_search_v2"


def _card(v: dict, query: str, short: bool = False) -> dict | None:
    vid = v.get("video_id") or v.get("id")
    if not vid:
        return None
    views = count(v.get("view_count") or v.get("view_count_text") or v.get("number_of_views"))
    published = relative_ts(v.get("published_time") or v.get("published_time_text") or "")
    if views is None and not published:
        return None      # live streams and ads carry neither
    url = v.get("url") or v.get("video_url") or ""
    is_short = short or "/shorts/" in url
    dur = v.get("duration") or v.get("length") or ""
    return {
        "id": f"youtube:{vid}", "source": "youtube", "kind": "video",
        "title": v.get("title") or "", "text": (v.get("description_snippet") or v.get("description") or "")[:300],
        "url": f"https://www.youtube.com/shorts/{vid}" if is_short else f"https://www.youtube.com/watch?v={vid}",
        "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "author": v.get("author") or v.get("channel_name") or "", "author_id": v.get("channel_id") or "",
        "published_at": published,
        "metrics": {"views": views},
        "extra": {"duration": _dur(dur), "short": is_short, "published_text": v.get("published_time") or ""},
        "query": query,
    }


def _dur(v):
    parts = [int(p) for p in str(v or "").split(":") if p.isdigit()]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s or None


def search(keywords: list[str] | None = None, upload_date: str = "this_week", duration: str | None = None) -> list[dict]:
    """``duration="short"`` keeps to videos under 4 minutes, which is how Shorts surface sorted by views."""
    out = []
    for kw in keywords or config.YT_KEYWORDS:
        params = {"keyword": kw, "type": "video", "upload_date": upload_date, "sort_by": "view_count"}
        if duration:
            params["duration"] = duration
        try:
            data = net.tikhub(V2, params)
        except net.FetchError as exc:
            log(f"youtube search {kw}: {exc}")
            continue
        for v in data.get("videos") or []:
            c = _card(v, f"youtube:{kw}" + (":short" if duration else ""))
            if c:
                if duration and (c["extra"]["duration"] or 999) <= 180:
                    c["extra"]["short"] = True
                out.append(c)
        for v in data.get("shorts") or []:
            c = _card(v, f"youtube:{kw}", short=True)
            if c:
                out.append(c)
    return out


def search_any_time(term: str, sort_by: str | None = None) -> list[dict]:
    """No upload-date limit: relevance order, or ``sort_by="view_count"`` for the biggest hits of all time."""
    params = {"keyword": term, "type": "video"}
    if sort_by:
        params["sort_by"] = sort_by
    data = net.tikhub(V2, params)
    out = [c for c in (_card(v, f"youtube:{term}") for v in data.get("videos") or []) if c]
    return out + [c for c in (_card(v, f"youtube:{term}", short=True) for v in data.get("shorts") or []) if c]


def channel_followers(channel_id: str) -> int | None:
    try:
        data = net.tikhub("/api/v1/youtube/web/get_channel_info", {"channel_id": channel_id})
    except net.FetchError as exc:
        log(f"youtube channel {channel_id}: {exc}")
        return None
    for key in ("subscriber_count", "subscriberCount", "subscribers", "subscriber_count_text"):
        if data.get(key) is not None:
            return count(data.get(key))
    info = data.get("channel") or data.get("info") or {}
    for key in ("subscriber_count", "subscriberCount", "subscribers"):
        if info.get(key) is not None:
            return count(info.get(key))
    return None


_STOP = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "my", "how", "i", "is", "by", "from", "video", "videos"}


def _tokens(term: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-z0-9]+|[^\x00-\x7f]+", term.lower()) if w not in _STOP and len(w) >= 2]


# Words a matching title may leave out; the distinctive ones ("tv", "food", "slide") must all be there.
_SOFT = {"ai", "code", "claude", "chatgpt", "gpt", "gemini", "3d", "2d", "animation", "animated", "cartoon", "story",
         "short", "film", "tutorial", "full", "guide", "free", "new", "satisfying"}


def title_matches(term: str, title: str) -> bool:
    """Every distinctive token of the term is in the title; at most one soft word may be missing."""
    toks = _tokens(term)
    if not toks:
        return False
    low = title.lower()
    missing = [t for t in toks if t not in low]
    return not missing or (len(missing) == 1 and missing[0] in _SOFT and len(toks) >= 2)


def window_count(term: str) -> dict:
    """This year's most relevant videos for ``term``; of those whose titles really match it, how many
    were published in the last 10 days. All recent = the format is new. (Sorting by views instead
    hides small new formats behind big old videos, and sorting by date hides the old ones.)"""
    import time
    year = net.tikhub(V2, {"keyword": term, "type": "video", "upload_date": "this_year"})
    yr = [c for c in (_card(v, "") for v in year.get("videos") or []) if c]
    hits = [c for c in yr if title_matches(term, c["title"])]
    now = time.time()
    recent = [c for c in hits if c.get("published_at") and now - c["published_at"] <= 10 * 86400]
    ages = [round((now - c["published_at"]) / 86400) for c in hits if c.get("published_at")]
    return {
        "year_n": len(yr), "match": len(hits), "match_recent": len(recent),
        "oldest_match_days": max(ages) if ages else None,
        "samples": [{"title": c["title"], "url": c["url"], "views": c["metrics"]["views"],
                     "published": c["extra"]["published_text"]} for c in hits[:3]],
    }
