"""Small parsing helpers shared by collectors."""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone

from .. import config

_LANE = [t.lower() for t in config.AI_TERMS]
_STRONG = [t.lower() for t in config.STRONG_AI_TERMS]
_ASCII_WORD = re.compile(r"^[a-z0-9]+$")


def in_lane(text: str, terms: list[str] | None = None) -> bool:
    low = (text or "").lower()
    for t in terms or _LANE:
        if _ASCII_WORD.match(t):          # whole word for short latin terms ("ai" must not match "said")
            if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
                return True
        elif t in low:
            return True
    return False


_BLOCK = re.compile(r"nsfw|uncensored|porn|nude|hentai|face[\s_-]?swap|deepnude|undress|casino|gambl|slot|togel|judi|777|onlyfans|色情|裸|赌|成人短剧|成人内容|陪睡|内射|约炮|黄片", re.I)


def strong_ai(text: str) -> bool:
    """Says AI outright or names an AI tool; for general hot lists where 模型 / 生成 mean something else."""
    return in_lane(text, _STRONG)


_OFF = re.compile(config.OFF_LANE_ZH + "|" + config.OFF_LANE_EN, re.I)


def off_lane(text: str) -> bool:
    """Security, finance and infra stories: real news, but not for AI-video creators."""
    return bool(_OFF.search(text or ""))


def blocked(text: str) -> bool:
    """Adult, face-swap and gambling items never reach a page."""
    return bool(_BLOCK.search(text or ""))


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9,
          "万": 1e4, "亿": 1e8, "千": 1e3}


def count(value) -> int | None:
    """2040, "2,040", "1.3K views", "12万次观看" -> int."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(thousand|million|billion|万|亿|千|k|m|b)?", text)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * _UNITS.get(m.group(2) or "", 1))


_REL = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400,
        "month": 30 * 86400, "year": 365 * 86400}


def relative_ts(text: str, now: float | None = None) -> float | None:
    """"3 days ago" / "Streamed 10 hours ago" / "3天前" -> epoch seconds (approximate)."""
    if not text:
        return None
    now = now or time.time()
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago", text.lower())
    if m:
        return now - int(m.group(1)) * _REL[m.group(2)]
    m = re.search(r"(\d+)\s*(秒|分钟|小时|天|周|个月|年)前", text)
    if m:
        unit = {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400, "周": 7 * 86400, "个月": 30 * 86400,
                "年": 365 * 86400}[m.group(2)]
        return now - int(m.group(1)) * unit
    return None


def iso_ts(text: str) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:   # Twitter: "Tue Oct 20 00:17:47 +0000 2026"
        return datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y").timestamp()
    except ValueError:
        return None


def utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
