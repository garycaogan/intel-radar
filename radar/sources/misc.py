"""Smaller free sources: Hugging Face trending, Hacker News, and the Douyin hot list (one TikHub call)."""

from __future__ import annotations

import time

from .. import net
from .common import in_lane, iso_ts, log


def hf_trending(limit: int = 30) -> list[dict]:
    """Models and Spaces sorted by Hugging Face's trendingScore."""
    out = []
    for kind, path in (("model", "models"), ("space", "spaces")):
        try:
            rows = net.get_json(f"https://huggingface.co/api/{path}", params={"sort": "trendingScore", "limit": limit}, name="hf")
        except net.FetchError as exc:
            log(f"hf trending {kind}: {exc}")
            continue
        for i, r in enumerate(rows if isinstance(rows, list) else []):
            rid = r.get("id")
            if not rid:
                continue
            url = f"https://huggingface.co/{rid}" if kind == "model" else f"https://huggingface.co/spaces/{rid}"
            out.append({
                "id": f"hf:{kind}:{rid}", "source": "hf", "kind": kind,
                "title": rid, "text": " ".join(t for t in (r.get("pipeline_tag"), r.get("library_name"), r.get("sdk")) if t),
                "url": url, "thumb": "", "author": rid.split("/")[0], "author_id": rid.split("/")[0],
                "published_at": iso_ts(r.get("createdAt") or ""),
                "metrics": {"likes": r.get("likes"), "downloads": r.get("downloads"),
                            "trending": r.get("trendingScore"), "rank": i + 1},
                "extra": {"pipeline": r.get("pipeline_tag"), "tags": [t for t in r.get("tags") or [] if ":" not in t][:6]},
                "query": f"hf:{kind}",
            })
    return out


def hn(hours: int = 36, min_points: int = 80) -> list[dict]:
    since = int(time.time() - hours * 3600)
    try:
        data = net.get_json("https://hn.algolia.com/api/v1/search",
                            params={"tags": "story", "numericFilters": f"created_at_i>{since},points>{min_points}",
                                    "hitsPerPage": 100}, name="hn")
    except net.FetchError as exc:
        log(f"hn: {exc}")
        return []
    out = []
    for h in data.get("hits") or []:
        title = h.get("title") or ""
        if not in_lane(title + " " + (h.get("url") or "")):
            continue
        oid = h.get("objectID")
        out.append({
            "id": f"hn:{oid}", "source": "hn", "kind": "story", "title": title, "text": "",
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}", "thumb": "",
            "author": h.get("author") or "", "author_id": h.get("author") or "",
            "published_at": float(h.get("created_at_i") or 0) or None,
            "metrics": {"points": h.get("points"), "comments": h.get("num_comments")},
            "extra": {"hn_url": f"https://news.ycombinator.com/item?id={oid}", "show_hn": title.lower().startswith("show hn")},
            "query": "hn",
        })
    return out


def douyin_hot() -> list[dict]:
    try:
        data = net.tikhub("/api/v1/douyin/web/fetch_hot_search_result", {})
    except net.FetchError as exc:
        log(f"douyin hot: {exc}")
        return []
    rows = ((data.get("data") or data).get("word_list")) or []
    now = time.time()
    out = []
    for i, r in enumerate(rows):
        w = r.get("word") or ""
        if not w:
            continue
        out.append({"id": f"douyin_hot:{w}", "source": "douyin_hot", "kind": "hotword", "title": w, "text": "",
                    "url": "https://www.douyin.com/search/" + w, "author": "", "author_id": "", "published_at": now,
                    "metrics": {"rank": i + 1, "heat": r.get("hot_value")}, "extra": {"in_lane": in_lane(w)},
                    "query": "douyin:hot"})
    return out
