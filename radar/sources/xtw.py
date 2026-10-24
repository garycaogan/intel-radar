"""X (Twitter) through TikHub search (billed per call).

Each query in ``config.X_QUERIES`` is an X advanced search ("Top" tab) limited to the last two
days, so the results are what people are actually engaging with. ``mention_count`` is the
breadth probe: how many distinct accounts talked about a term in the last day.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .. import config, net
from .common import count, iso_ts, log

SEARCH = "/api/v1/twitter/web/fetch_search_timeline"


def _card(t: dict, query: str) -> dict | None:
    tid = t.get("tweet_id")
    if not tid or t.get("type") not in (None, "tweet"):
        return None
    user = t.get("user_info") or {}
    sn = t.get("screen_name") or user.get("screen_name") or ""
    media = t.get("media") or {}
    thumb, shown = "", {}
    for kind in ("video", "animated_gif", "photo"):
        arr = media.get(kind) or []
        if arr:
            m0 = arr[0]
            thumb = m0.get("media_url_https") or ""
            shown = {"type": kind, "thumb": thumb}
            mp4s = sorted((v for v in m0.get("variants") or [] if v.get("content_type") == "video/mp4"),
                          key=lambda v: v.get("bitrate") or 0)
            if mp4s:   # the ~2 Mbps rendition (720p) plays quickly on a phone; fall back to the best there is
                fit = [v for v in mp4s if (v.get("bitrate") or 0) <= 2_500_000]
                shown["mp4"] = (fit or mp4s)[-1]["url"]
            info = m0.get("original_info") or {}
            if info.get("width") and info.get("height"):
                shown["aspect"] = round(info["width"] / info["height"], 3)
            break
    links = [u.get("expanded_url") for u in (t.get("entities") or {}).get("urls") or [] if u.get("expanded_url")]
    mentions = [m.get("screen_name") for m in (t.get("entities") or {}).get("user_mentions") or [] if m.get("screen_name")]
    return {
        "id": f"x:{tid}", "source": "x", "kind": "tweet",
        "title": (t.get("text") or "").strip()[:140], "text": (t.get("text") or "").strip()[:1200],
        "url": f"https://x.com/{sn}/status/{tid}", "thumb": thumb,
        "author": user.get("name") or sn, "author_id": sn,
        "published_at": iso_ts(t.get("created_at") or ""),
        "metrics": {"likes": count(t.get("favorites")), "retweets": count(t.get("retweets")),
                    "replies": count(t.get("replies")), "quotes": count(t.get("quotes")),
                    "bookmarks": count(t.get("bookmarks")), "views": count(t.get("views")),
                    "followers": count(user.get("followers_count"))},
        "extra": {"lang": t.get("lang"), "links": links[:5], "mentions": mentions[:8], "media": shown,
                  "has_video": bool(media.get("video")), "author_created": user.get("created_at")},
        "query": query,
    }


def _since(days: int = 2) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def search(queries=None, pages: int = 1) -> list[dict]:
    since = _since(2)
    out = []
    for name, q in queries or config.X_QUERIES:
        cursor = None
        for _ in range(pages):
            params = {"keyword": q.format(since=since), "search_type": "Top"}
            if cursor:
                params["cursor"] = cursor
            try:
                data = net.tikhub(SEARCH, params)
            except net.FetchError as exc:
                log(f"x search {name}: {exc}")
                break
            out += [c for c in (_card(t, f"x:{name}") for t in data.get("timeline") or []) if c]
            cursor = data.get("next_cursor")
            if not cursor:
                break
    return out


def mention_count(term: str, hours: int = 36) -> dict:
    """Latest tweets about ``term``: distinct authors and total views over the window (one page)."""
    data = net.tikhub(SEARCH, {"keyword": f"{term} since:{_since(2)}", "search_type": "Latest"})
    cutoff = time.time() - hours * 3600
    rows = [c for c in (_card(t, "") for t in data.get("timeline") or []) if c]
    recent = [c for c in rows if (c["published_at"] or 0) >= cutoff]
    span_h = None
    if len(recent) >= 2:
        ts = sorted(c["published_at"] for c in recent)
        span_h = round((ts[-1] - ts[0]) / 3600, 1)
    return {"tweets": len(recent), "authors": len({c["author_id"] for c in recent}),
            "views": sum(c["metrics"]["views"] or 0 for c in recent), "span_hours": span_h,
            "page_full": len(rows) >= 18}
