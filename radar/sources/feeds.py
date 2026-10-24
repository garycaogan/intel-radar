"""Free news feeds: AI HOT and 量子位 (Chinese AI news) and Product Hunt's video / AI categories.

Only titles, links, dates and a short summary are kept; the model decides in the tools step which
ones matter to AI-video creators.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import config, net
from .common import iso_ts, log

_ATOM = "{http://www.w3.org/2005/Atom}"


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _rss_date(s: str) -> float | None:
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        return iso_ts(s)


def parse(xml: str) -> list[dict]:
    """RSS 2.0 items or Atom entries as {title, url, published_at, summary}."""
    root = ET.fromstring(xml)
    rows = []
    for it in root.iter("item"):
        rows.append({"title": _text(it.findtext("title")), "url": (it.findtext("link") or "").strip(),
                     "published_at": _rss_date(it.findtext("pubDate") or ""),
                     "summary": _text(it.findtext("description"))[:300]})
    for en in root.iter(_ATOM + "entry"):
        link = en.find(_ATOM + "link")
        rows.append({"title": _text(en.findtext(_ATOM + "title")), "url": link.get("href", "") if link is not None else "",
                     "published_at": iso_ts(en.findtext(_ATOM + "published") or en.findtext(_ATOM + "updated") or ""),
                     "summary": re.sub(r"\s*Discussion\s*\|\s*Link\s*$", "",   # Product Hunt's footer
                                       _text(en.findtext(_ATOM + "content") or en.findtext(_ATOM + "summary")))[:300]})
    return [r for r in rows if r["title"] and r["url"]]


def news(feeds=None, hours: float = config.NEWS_HOURS) -> list[dict]:
    cutoff = time.time() - hours * 3600
    out, seen = [], set()
    for source, name, url in feeds or config.NEWS_FEEDS:
        try:
            rows = parse(net.get_text(url, name="feeds", timeout=20))
        except (net.FetchError, ET.ParseError) as exc:
            log(f"feed {name} {url}: {exc}")
            continue
        for r in rows:
            if r["url"] in seen or (r["published_at"] or 0) < cutoff:
                continue
            seen.add(r["url"])
            out.append({"id": f"{source}:{r['url']}", "source": source, "kind": "news", "title": r["title"][:160],
                        "text": r["summary"], "url": r["url"], "thumb": "", "author": name, "author_id": source,
                        "published_at": r["published_at"], "metrics": {},
                        "extra": {"source_name": name}, "query": f"feed:{source}"})
    return out
