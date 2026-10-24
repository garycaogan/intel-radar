"""抖音, TikTok and 小红书 through TikHub (billed per call; prices in config.TIKHUB_PRICES).

All three return vertical short videos with signed cover URLs that expire within hours to days, so
the pipeline caches the covers it shows (pipeline.cache_thumbs). 抖音 and 小红书 don't expose play
counts: their cards carry likes (and 抖音 the follower count) and are scored on those instead.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .. import config, net
from .common import count, in_lane, log

BJ = timezone(timedelta(hours=8))


def _bj_ts(text: str) -> float | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ).timestamp()
    except (TypeError, ValueError):
        return None


def _mmss(text) -> int | None:
    parts = [int(p) for p in str(text or "").split(":") if p.isdigit()]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s or None


def douyin(queries=None, per_query: int = config.DOUYIN_PER_QUERY, date_type: int = 7) -> list[dict]:
    """``date_type`` 7 = last week; 0 reaches back about half a year (the deep dive uses it)."""
    out = []
    for q, label in queries or config.DOUYIN_QUERIES:
        try:
            data = net.tikhub("/api/v1/douyin/search/fetch_video_search_v3",
                              {"query": q, "date_type": date_type, "label_type": label, "duration_type": 0}, post=True)
        except net.FetchError as exc:
            log(f"douyin search {q}: {exc}")
            continue
        rows = sorted(data.get("data") or [], key=lambda r: -(count(r.get("index")) or 0))[:per_query]
        for r in rows:
            vid = r.get("itemId")
            if not vid:
                continue
            out.append({
                "id": f"douyin:{vid}", "source": "douyin", "kind": "video",
                "title": (r.get("title") or "").strip()[:200], "text": "",
                "url": f"https://www.douyin.com/video/{vid}", "thumb": r.get("thumbnail") or "",
                "author": r.get("nickname") or "", "author_id": r.get("nickname") or "",
                "published_at": _bj_ts(r.get("createTime")),
                "metrics": {"views": None, "likes": count(r.get("likes")), "followers": count(r.get("fans")),
                            "index": count(r.get("index"))},
                "extra": {"duration": r.get("duration"), "vertical": True,
                          "low_fans_hot": bool(r.get("isLowFansButHot"))},
                "query": f"douyin:{q}" + (":低粉爆款" if label == 1 else ""),
            })
    return out


def tiktok(keywords=None, publish_time: int = 7) -> list[dict]:
    """``publish_time`` 7 = last week, 0 = any time (most liked first either way)."""
    out = []
    for kw in keywords or config.TIKTOK_KEYWORDS:
        try:
            data = net.tikhub("/api/v1/tiktok/app/v3/fetch_video_search_result",
                              {"keyword": kw, "offset": 0, "count": 20, "sort_type": 1, "publish_time": publish_time, "region": "US"})
        except net.FetchError as exc:
            log(f"tiktok search {kw}: {exc}")
            continue
        for row in data.get("search_item_list") or []:
            a = row.get("aweme_info") or {}
            vid = a.get("aweme_id")
            if not vid:
                continue
            desc = a.get("desc") or ""
            made_by_ai = bool((a.get("aigc_info") or {}).get("created_by_ai"))
            if not (made_by_ai or in_lane(desc)):
                continue
            st, au, v = a.get("statistics") or {}, a.get("author") or {}, a.get("video") or {}
            covers = (v.get("cover") or {}).get("url_list") or []
            handle = au.get("unique_id") or ""
            out.append({
                "id": f"tiktok:{vid}", "source": "tiktok", "kind": "video",
                "title": desc.strip()[:200], "text": "",
                "url": f"https://www.tiktok.com/@{handle}/video/{vid}", "thumb": covers[0] if covers else "",
                "author": au.get("nickname") or handle, "author_id": handle,
                "published_at": float(a.get("create_time") or 0) or None,
                "metrics": {"views": count(st.get("play_count")), "likes": count(st.get("digg_count")),
                            "comments": count(st.get("comment_count")), "favorites": count(st.get("collect_count")),
                            "followers": count(au.get("follower_count"))},
                "extra": {"duration": round((v.get("duration") or 0) / 1000) or None,
                          "vertical": (v.get("height") or 0) > (v.get("width") or 0), "made_by_ai": made_by_ai},
                "query": f"tiktok:{kw}",
            })
    return out


def xhs(keywords=None) -> list[dict]:
    out = []
    for kw in keywords or config.XHS_KEYWORDS:
        try:
            data = net.tikhub("/api/v1/xiaohongshu/app_v2/search_notes",
                              {"keyword": kw, "page": 1, "sort_type": "popularity_descending",
                               "note_type": "视频笔记", "time_filter": "一周内"})
        except net.FetchError as exc:
            log(f"xhs search {kw}: {exc}")
            continue
        items = ((data.get("data") or {}).get("items")) or []
        for it in items:
            n = it.get("note") if it.get("model_type") == "note" else None
            if not n or not n.get("id"):
                continue
            img = (n.get("images_list") or [{}])[0]
            user = n.get("user") or {}
            nid = n["id"]
            token = n.get("xsec_token") or ""
            out.append({
                "id": f"xhs:{nid}", "source": "xhs", "kind": "video",
                "title": (n.get("title") or n.get("desc") or "").strip()[:200], "text": (n.get("desc") or "")[:300],
                "url": f"https://www.xiaohongshu.com/explore/{nid}" + (f"?xsec_token={token}&xsec_source=pc_search" if token else ""),
                "thumb": img.get("url") or "",
                "author": user.get("nickname") or "", "author_id": user.get("userid") or user.get("red_id") or "",
                "published_at": float(n.get("timestamp") or 0) or None,
                "metrics": {"views": None, "likes": count(n.get("liked_count")), "favorites": count(n.get("collected_count")),
                            "comments": count(n.get("comments_count")), "followers": None},
                "extra": {"duration": _mmss(n.get("video_duration")),
                          "vertical": (img.get("height") or 0) > (img.get("width") or 0)},
                "query": f"xhs:{kw}",
            })
        time.sleep(0.5)
    return out
