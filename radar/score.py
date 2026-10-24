"""Deterministic per-item signals. The model never produces numbers; everything shown comes from here.

- video: views per hour since publish (VPH, age floored at 6 h), views ÷ followers (VSR, followers
  floored at 1000 so a brand-new account doesn't explode the ratio), like/favourite rate. Platforms that
  hide plays (抖音, 小红书) get likes per hour and likes ÷ followers instead, ranked only among themselves.
- tweet: views, likes, bookmarks (a bookmark-heavy tweet is usually a tutorial or resource).
- repo: stars per day since creation, stars gained today (trending), star gain since our last snapshot.
- heat: a 0–100 percentile mix inside each source so B站 plays and X likes can sit in one list.
"""

from __future__ import annotations

import math
import time

from . import db


def _pct_ranks(values: list[float | None]) -> list[float]:
    present = sorted(v for v in values if v is not None)
    if not present:
        return [0.0] * len(values)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
            continue
        lo = sum(1 for x in present if x < v)
        eq = sum(1 for x in present if x == v)
        out.append(100.0 * (lo + 0.5 * eq) / len(present))
    return out


def derive(it: dict, now: float, con=None) -> dict:
    m = it.get("metrics") or {}
    age_h = max((now - it["published_at"]) / 3600, 0) if it.get("published_at") else None
    s: dict = {"age_h": round(age_h, 1) if age_h is not None else None}
    if it["kind"] == "video":
        views = m.get("views")
        s["vph"] = round(views / max(age_h, 6), 1) if views is not None and age_h is not None else None
        f = m.get("followers")
        s["vsr"] = round(views / max(f, 1000), 2) if views is not None and f is not None else None
        likes = m.get("likes")
        if views is None and likes is not None and age_h is not None:   # 抖音 / 小红书 show likes, not plays
            s["lph"] = round(likes / max(age_h, 6), 1)
            s["lsr"] = round(likes / max(f, 1000), 3) if f is not None else None
        if views:
            s["engage"] = round(((m.get("likes") or 0) + (m.get("favorites") or 0)) / views, 4) if m.get("likes") is not None else None
    elif it["kind"] == "tweet":
        s["views"] = m.get("views")
        s["likes"] = m.get("likes")
        s["bookmarks"] = m.get("bookmarks")
    elif it["kind"] == "repo":
        stars = m.get("stars")
        if stars is not None and age_h is not None:
            s["stars_per_day"] = round(stars / max(age_h / 24, 1), 1)
        s["stars_today"] = m.get("stars_today")
        if con is not None and stars is not None:
            prev = db.previous_snap(con, it["id"], now, min_gap=12 * 3600)
            if prev and prev[1].get("stars") is not None:
                days = max((now - prev[0]) / 86400, 0.5)
                s["stars_gain_per_day"] = round((stars - prev[1]["stars"]) / days, 1)
    return s


def heat(items: list[dict]) -> None:
    """Fill it["signals"]["heat"] (0–100) per source."""
    by_source: dict[str, list[dict]] = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)
    for src, group in by_source.items():
        sig = [g["signals"] for g in group]
        if src in ("douyin", "xhs"):
            lph = _pct_ranks([s.get("lph") for s in sig])
            lsr = _pct_ranks([s.get("lsr") for s in sig])
            for g, a, b in zip(group, lph, lsr):
                g["signals"]["heat"] = round(0.6 * a + 0.4 * b if g["signals"].get("lsr") is not None else a, 1)
        elif src in ("bili", "youtube", "tiktok"):
            vph = _pct_ranks([s.get("vph") for s in sig])
            vsr = _pct_ranks([s.get("vsr") for s in sig])
            for g, a, b in zip(group, vph, vsr):
                g["signals"]["heat"] = round(0.6 * a + 0.4 * b if g["signals"].get("vsr") is not None else a, 1)
        elif src == "x":
            v = _pct_ranks([s.get("views") for s in sig])
            lk = _pct_ranks([s.get("likes") for s in sig])
            bm = _pct_ranks([s.get("bookmarks") for s in sig])
            for g, a, b, c in zip(group, v, lk, bm):
                g["signals"]["heat"] = round(0.45 * a + 0.35 * b + 0.2 * c, 1)
        elif src == "github":
            spd = _pct_ranks([s.get("stars_per_day") for s in sig])
            tod = _pct_ranks([s.get("stars_today") for s in sig])
            for g, a, b in zip(group, spd, tod):
                g["signals"]["heat"] = round(max(a, b), 1)
        else:
            ranks = [-(g["metrics"].get("rank") or 999) for g in group] if any(g["metrics"].get("rank") for g in group) \
                else [g["metrics"].get("points") or g["metrics"].get("likes") or 0 for g in group]
            for g, a in zip(group, _pct_ranks(ranks)):
                g["signals"]["heat"] = round(a, 1)


def annotate(items: list[dict], con=None, now: float | None = None) -> list[dict]:
    now = now or time.time()
    for it in items:
        it["signals"] = derive(it, now, con)
    heat(items)
    return items


def fmt_num(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    if n >= 1e8:
        return f"{n / 1e8:.1f}亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f}万"
    return str(int(round(n))) if n >= 100 or n == int(n) else f"{n:.1f}"


def log_scale(n) -> float:
    return math.log10(1 + max(n or 0, 0))
