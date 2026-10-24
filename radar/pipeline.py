"""One daily run: collect → store → enrich → score → analyze → brief → render."""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import analyze, config, db, net, render, score, share
from .analyze import BJ
from .sources import bilibili, feeds, github, misc, shortvideo, xtw, youtube
from .sources.common import blocked, log

COLLECTORS = {
    "bili_search": lambda: bilibili.search(),
    "bili_popular": lambda: bilibili.popular(),
    "bili_hot": lambda: bilibili.hot_words(),
    "bili_rank": lambda: bilibili.rank_partitions(),
    "youtube": lambda: youtube.search(),
    "youtube_short": lambda: youtube.search(config.YT_SHORT_KEYWORDS, duration="short"),
    "douyin": lambda: shortvideo.douyin(),
    "tiktok": lambda: shortvideo.tiktok(),
    "xhs": lambda: shortvideo.xhs(),
    "x": lambda: xtw.search(pages=config.X_PAGES),
    "github_trending": lambda: github.trending(),
    "github_search": lambda: github.search(),
    "hf": lambda: misc.hf_trending(),
    "hn": lambda: misc.hn(),
    "douyin_hot": lambda: misc.douyin_hot(),
    "news": lambda: feeds.news(),
}


def collect(only: list[str] | None = None) -> tuple[list[dict], dict]:
    report = {}
    names = [n for n in COLLECTORS if not only or n in only]

    def run(name):
        t = time.time()
        try:
            rows = COLLECTORS[name]()
            return name, rows, {"n": len(rows), "secs": round(time.time() - t, 1)}
        except Exception as exc:
            log(f"collector {name} failed: {exc}")
            return name, [], {"n": 0, "error": f"{type(exc).__name__}: {exc}"[:200]}

    # B站 collectors share one cookie and rate limit, so they run in one worker; the rest in parallel.
    bili = [n for n in names if n.startswith("bili")]
    rest = [n for n in names if not n.startswith("bili")]
    items = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(lambda ns=bili: [run(n) for n in ns])] + [pool.submit(lambda n=n: [run(n)]) for n in rest]
        for f in futures:
            for name, rows, rep in f.result():
                items += rows
                report[name] = rep
    return dedupe(items), report


def dedupe(items: list[dict]) -> list[dict]:
    """One row per id; the queries that surfaced it are merged."""
    seen: dict[str, dict] = {}
    for it in items:
        if blocked((it.get("title") or "") + " " + (it.get("text") or "")):
            continue
        cur = seen.get(it["id"])
        if cur is None:
            it["queries"] = [it.get("query")] if it.get("query") else []
            seen[it["id"]] = it
        else:
            if it.get("query") and it["query"] not in cur["queries"]:
                cur["queries"].append(it["query"])
            if it.get("extra", {}).get("short"):
                cur["extra"]["short"] = True
            for k, v in (it.get("metrics") or {}).items():
                if v is not None and cur["metrics"].get(k) is None:
                    cur["metrics"][k] = v
    return list(seen.values())


def enrich_followers(con, items: list[dict], yt_budget: int = 30) -> dict:
    """Follower counts for the videos most likely to be picked (cached a few days)."""
    vids = [it for it in items if it["kind"] == "video" and it["signals"].get("age_h") is not None
            and it["signals"]["age_h"] <= 8 * 24]
    stats = {"bili": 0, "youtube": 0}
    bili = sorted((v for v in vids if v["source"] == "bili"), key=lambda v: -(v["signals"].get("vph") or 0))[:90]
    for v in bili:
        if not v.get("author_id"):
            continue
        key = f"bili:{v['author_id']}"
        f = db.author_followers(con, key)
        if f is None:
            f = bilibili.followers(v["author_id"])
            db.save_author(con, key, v.get("author") or "", f)
            stats["bili"] += 1
            time.sleep(0.25)
        v["metrics"]["followers"] = f
    yt = sorted((v for v in vids if v["source"] == "youtube"), key=lambda v: -(v["signals"].get("vph") or 0))[:60]
    asked = set()
    for v in yt:
        cid = v.get("author_id")
        if not cid:
            continue
        key = f"youtube:{cid}"
        f = db.author_followers(con, key, max_age=7 * 86400)
        if f is None and cid not in asked and len(asked) < yt_budget:
            asked.add(cid)
            f = youtube.channel_followers(cid)
            db.save_author(con, key, v.get("author") or "", f)
            stats["youtube"] += 1
        v["metrics"]["followers"] = f
    return stats


_SIGNED = ("抖音", "TikTok", "小红书")     # cover URLs that expire within hours to days


def _image_ext(raw: bytes) -> str | None:
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None          # HEIC and anything else a browser may not show


def cache_thumbs(sections: dict) -> dict:
    """Download the covers the page shows from platforms with expiring links into site/img/ and point the
    samples there, so tomorrow's page still has its pictures. Files older than 21 days are removed."""
    return cache_samples([s for t in sections.get("video") or [] for s in t.get("samples") or []])


def cache_samples(samples: list[dict]) -> dict:
    config.ensure_dirs()
    img_dir = config.SITE_DIR / "img"
    img_dir.mkdir(exist_ok=True)
    samples = [s for s in samples if s.get("platform") in _SIGNED and (s.get("thumb") or "").startswith("http")]

    def fetch(s):
        name = hashlib.sha1(s["url"].encode()).hexdigest()[:16]
        for ext in ("jpg", "webp", "png"):
            if (img_dir / f"{name}.{ext}").exists():
                return s, f"img/{name}.{ext}"
        try:
            raw = net.request(s["thumb"], name="thumbs", timeout=20, retries=1)
        except net.FetchError:
            return s, None
        ext = _image_ext(raw)
        if not ext or len(raw) > 2_000_000:
            return s, None
        (img_dir / f"{name}.{ext}").write_bytes(raw)
        return s, f"img/{name}.{ext}"

    ok = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for s, local in pool.map(fetch, samples):
            if local:
                s["thumb"] = local
                ok += 1
    cutoff = time.time() - 21 * 86400
    for f in img_dir.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    return {"wanted": len(samples), "cached": ok}


def history_days(con) -> int:
    r = con.execute("SELECT COUNT(DISTINCT day) AS n FROM briefs").fetchone()
    return int(r["n"] or 0)


def run(day: str | None = None, only: list[str] | None = None, skip_llm: bool = False, reuse_hours: float = 0) -> dict:
    """``reuse_hours`` re-analyzes items already collected in that window instead of collecting again."""
    started = time.time()
    day = day or datetime.now(BJ).strftime("%Y-%m-%d")
    con = db.connect()
    run_id = con.execute("INSERT INTO runs(day, started, status) VALUES(?,?,?)", (day, started, "running")).lastrowid
    con.commit()
    report: dict = {"errors": []}
    try:
        if reuse_hours:
            items = db.load_recent(con, time.time() - reuse_hours * 3600)
            last = con.execute("SELECT stats FROM runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1").fetchone()
            prev = json.loads(last["stats"]) if last and last["stats"] else {}
            report["collect"], report["new_items"] = prev.get("collect") or {}, prev.get("new_items")
            # the page reports what the collection cost as well as this re-analysis
            report["reused"] = {"cost": prev.get("reused", {}).get("cost") or prev.get("cost"), "hours": reuse_hours}
            log(f"reusing {len(items)} items from the last {reuse_hours} h")
            if only:   # refresh just these collectors on top of the stored items
                fresh, rep = collect(only)
                db.upsert_items(con, fresh)
                report["collect"] = {**report["collect"], **rep}
                items = fresh + items
            items = dedupe(items)
        else:
            items, report["collect"] = collect(only)
            log(f"collected {len(items)} items")
            report["new_items"] = db.upsert_items(con, items)
        score.annotate(items, con)
        report["followers"] = enrich_followers(con, items)
        score.annotate(items, con)
        db.upsert_items(con, [it for it in items if it["kind"] == "video"])   # store follower counts
        hist = history_days(con)
        sections: dict = {"hotwords": analyze.hot_words(items)}
        videos = [it for it in items if it["kind"] == "video"]
        tweets = [it for it in items if it["kind"] == "tweet"]
        repos = [it for it in items if it["kind"] == "repo"]
        hf = [it for it in items if it["source"] == "hf"]
        hn = [it for it in items if it["source"] == "hn"]
        news = [it for it in items if it["kind"] == "news"]
        if skip_llm:
            sections.update(video=[], x=[], news=[], tools={"repos": [], "models": [], "stories": []})
            head = {"summary": "", "headline": []}
        else:
            con.execute("DELETE FROM topics WHERE day=?", (day,))   # a rerun replaces the day's topics
            con.commit()
            steps = {
                "video": lambda: analyze.video_topics(con, day, videos, report, hist),
                "x": lambda: analyze.x_topics(con, day, tweets, report, hist),
                "tools": lambda: analyze.tools(repos, hf, hn, report, news),
            }
            for name, fn in steps.items():
                try:
                    sections[name] = fn()
                except Exception as exc:
                    log(f"step {name} failed: {exc}")
                    report["errors"].append(f"{name}: {type(exc).__name__}: {exc}"[:300])
                    sections[name] = {"repos": [], "models": [], "stories": [], "news": []} if name == "tools" else []
            sections["news"] = sections["tools"].pop("news", [])
            try:
                head = analyze.headline(sections["video"], sections["x"], sections["tools"], sections["news"])
            except Exception as exc:
                log(f"headline failed: {exc}")
                report["errors"].append(f"headline: {type(exc).__name__}: {exc}"[:300])
                head = {"summary": "", "headline": []}
        if not skip_llm:
            report["thumbs"] = cache_thumbs(sections)
            try:        # share cards draw their covers on a canvas, which needs same-origin copies
                report["card_covers"] = share.cache_covers({"day": day, "headline": head["headline"], "sections": sections})
            except Exception as exc:
                log(f"card covers failed: {exc}")
        counts = {}
        for it in items:
            counts[it["source"]] = counts.get(it["source"], 0) + 1
        brief = {
            "version": 1, "day": day, "generated_at": time.time(),
            "generated_bj": datetime.now(BJ).strftime("%Y-%m-%d %H:%M"),
            "history_days": hist, "summary": head["summary"], "headline": head["headline"],
            "sections": sections,
            "stats": {"items": len(items), "by_source": counts, "new_items": report.get("new_items"),
                      "collect": report.get("collect"), "followers": report.get("followers"), "thumbs": report.get("thumbs"),
                      "card_covers": report.get("card_covers"), "reused": report.get("reused"),
                      "cost": net.ledger.summary(), "errors": report["errors"], "dropped": report.get("dropped") or {},
                      "secs": round(time.time() - started)},
        }
        db.save_brief(con, day, brief)
        render.write_site(con, brief)
        status = "ok" if not report["errors"] else "partial"
        con.execute("UPDATE runs SET finished=?, status=?, stats=? WHERE id=?",
                    (time.time(), status, json.dumps(brief["stats"], ensure_ascii=False), run_id))
        con.commit()
        log(f"run {day} {status} in {brief['stats']['secs']}s; cost {net.ledger.summary()}")
        return brief
    except Exception:
        con.execute("UPDATE runs SET finished=?, status=?, stats=? WHERE id=?",
                    (time.time(), "failed", traceback.format_exc()[-1500:], run_id))
        con.commit()
        raise
