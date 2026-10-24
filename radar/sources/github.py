"""GitHub: the trending page (stars gained today) plus search for new repos (free).

Search needs a token for a usable rate limit; the gh CLI's token is used when present.
"""

from __future__ import annotations

import html
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

from .. import config, net
from .common import iso_ts, log

_token_cache: str | None = None


def _token() -> str:
    global _token_cache
    if _token_cache is None:
        import os
        _token_cache = os.environ.get(config.GH_TOKEN_ENV, "")
        if not _token_cache:
            for gh in ("gh", str(config.HOME / ".local/bin/gh")):
                try:
                    _token_cache = subprocess.run([gh, "auth", "token"], capture_output=True, text=True,
                                                  timeout=10).stdout.strip()
                    if _token_cache:
                        break
                except (OSError, subprocess.SubprocessError):
                    continue
    return _token_cache


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if _token():
        h["Authorization"] = "Bearer " + _token()
    return h


def _repo_card(r: dict, query: str, stars_today: int | None = None) -> dict:
    full = r["full_name"]
    return {
        "id": f"github:{full.lower()}", "source": "github", "kind": "repo",
        "title": full, "text": (r.get("description") or "")[:400], "url": r.get("html_url") or f"https://github.com/{full}",
        "thumb": "", "author": (r.get("owner") or {}).get("login") or full.split("/")[0], "author_id": full.split("/")[0],
        "published_at": iso_ts(r.get("created_at") or ""),
        "metrics": {"stars": r.get("stargazers_count"), "forks": r.get("forks_count"),
                    "stars_today": stars_today, "issues": r.get("open_issues_count")},
        "extra": {"language": r.get("language"), "topics": (r.get("topics") or [])[:8],
                  "pushed_at": r.get("pushed_at"), "homepage": r.get("homepage") or "", "license": ((r.get("license") or {}).get("spdx_id"))},
        "query": query,
    }


def search(days: int = 14) -> list[dict]:
    d = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for q in config.GH_QUERIES:
        try:
            data = net.get_json("https://api.github.com/search/repositories",
                                params={"q": q.format(d=d), "sort": "stars", "order": "desc", "per_page": 50},
                                headers=_headers(), name="github")
            out += [_repo_card(r, "github:search") for r in data.get("items") or []]
        except net.FetchError as exc:
            log(f"github search {q}: {exc}")
        time.sleep(1)
    return out


_ROW = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)


def trending(since: str = "daily") -> list[dict]:
    """Repos on github.com/trending with today's star gain. Details are filled from the API."""
    try:
        page = net.get_text("https://github.com/trending", params={"since": since}, name="github")
    except net.FetchError as exc:
        log(f"github trending: {exc}")
        return []
    rows = []
    for block in _ROW.findall(page):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', block)
        if not m:
            continue
        gained = re.search(r"([\d,]+)\s+stars\s+(?:today|this week|this month)", block)
        rows.append((m.group(1), int(gained.group(1).replace(",", "")) if gained else None,
                     html.unescape(re.sub(r"<[^>]+>", "", (re.search(r'<p class="col-9[^"]*">(.*?)</p>', block, re.S) or [None, ""])[1])).strip()))
    out = []
    for full, gained, desc in rows:
        try:
            r = net.get_json(f"https://api.github.com/repos/{full}", headers=_headers(), name="github")
            out.append(_repo_card(r, "github:trending", stars_today=gained))
        except net.FetchError:
            out.append({"id": f"github:{full.lower()}", "source": "github", "kind": "repo", "title": full,
                        "text": desc, "url": f"https://github.com/{full}", "author": full.split("/")[0],
                        "author_id": full.split("/")[0], "published_at": None,
                        "metrics": {"stars_today": gained}, "extra": {}, "query": "github:trending"})
    return out
