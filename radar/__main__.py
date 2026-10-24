"""python3 -m radar run | render [day] | serve | status | deep"""

from __future__ import annotations

import argparse
import http.server
import json
import sys

from . import config, db, deep, pipeline, render, share


def _serve(port: int = config.SERVE_PORT) -> None:
    config.ensure_dirs()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(config.SITE_DIR), **kw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer((config.SERVE_HOST, port), Handler)
    print(f"serving {config.SITE_DIR} on http://{config.SERVE_HOST}:{port}", flush=True)
    srv.serve_forever()


def daily_deep(brief: dict) -> None:
    """Deep-dive the day's 新起 / 升温 topics after the brief; a failure leaves the brief as it is."""
    try:
        out = deep.run(brief["day"], config.DEEP_DAILY_LABELS)
        if not out:
            return
        con = db.connect()
        brief["stats"]["deep"] = {"topics": len(out["topics"]), "cost": out["cost"]}
        db.save_brief(con, brief["day"], brief)
        render.write_site(con, brief)
        print("deep dive:", len(out["topics"]), "topics,", out["cost"]["est_usd_tikhub"], "USD", flush=True)
    except Exception as exc:
        print("deep dive failed:", type(exc).__name__, exc, file=sys.stderr, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, analyze and render today's brief")
    r.add_argument("--day")
    r.add_argument("--only", help="comma-separated collector names")
    r.add_argument("--skip-llm", action="store_true")
    r.add_argument("--if-missing", action="store_true",
                   help="do nothing when today's (Beijing) brief already exists; lets cron fire twice a day")
    r.add_argument("--reuse", type=float, default=0, metavar="HOURS",
                   help="re-analyze items collected in the last HOURS instead of collecting again")
    rr = sub.add_parser("render", help="re-render a stored brief")
    rr.add_argument("day", nargs="?")
    sv = sub.add_parser("serve", help=f"serve the site on {config.SERVE_HOST}:{config.SERVE_PORT}")
    sv.add_argument("--port", type=int, default=config.SERVE_PORT)
    sub.add_parser("status", help="last runs")
    dp = sub.add_parser("deep", help="origin dates and more hits for the day's video topics (~4 min, ~$0.2 for all)")
    dp.add_argument("--day")
    dp.add_argument("--labels", help="only topics with these labels, comma-separated (e.g. 新起,升温)")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        if a.if_missing:
            from datetime import datetime
            from .analyze import BJ
            today = a.day or datetime.now(BJ).strftime("%Y-%m-%d")
            if today in db.brief_days(db.connect()):
                print("brief for", today, "already exists; skipping")
                return 0
        brief = pipeline.run(a.day, a.only.split(",") if a.only else None, a.skip_llm, a.reuse)
        print(json.dumps(brief["stats"], ensure_ascii=False, indent=1))
        if config.DEEP_DAILY_LABELS and not a.reuse and not a.skip_llm:
            daily_deep(brief)
    elif a.cmd == "render":
        con = db.connect()
        day = a.day or (db.brief_days(con) or [None])[0]
        row = con.execute("SELECT payload FROM briefs WHERE day=?", (day,)).fetchone()
        if not row:
            print("no brief for", day, file=sys.stderr)
            return 1
        brief = json.loads(row["payload"])
        try:        # covers for the share cards (only the ones not copied yet)
            print("card covers:", share.cache_covers(brief))
        except Exception as exc:
            print("card covers failed:", type(exc).__name__, exc, file=sys.stderr)
        render.write_site(con, brief)
        print("rendered", day)
    elif a.cmd == "serve":
        _serve(a.port)
    elif a.cmd == "deep":
        from datetime import datetime
        from .analyze import BJ
        con = db.connect()
        day = a.day or (db.brief_days(con) or [datetime.now(BJ).strftime("%Y-%m-%d")])[0]
        out = deep.run(day, tuple(a.labels.split(",")) if a.labels else None)
        if not out:
            return 0
        render.write_site(con, json.loads(con.execute("SELECT payload FROM briefs WHERE day=?", (day,)).fetchone()["payload"]))
        print(json.dumps({"day": day, "topics": len(out["topics"]), "cost": out["cost"], "thumbs": out["thumbs"]},
                         ensure_ascii=False, indent=1))
    elif a.cmd == "status":
        con = db.connect()
        for row in con.execute("SELECT id, day, started, finished, status FROM runs ORDER BY id DESC LIMIT 10"):
            print(dict(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
