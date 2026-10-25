"""python3 -m radar run | render [day] | serve | status | deep | mcp"""

from __future__ import annotations

import argparse
import http.server
import json
import sys

from . import config, db, deep, pipeline, render, share


def _serve(port: int = config.SERVE_PORT) -> None:
    config.ensure_dirs()
    from . import mcp
    store = mcp.DbStore()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(config.SITE_DIR), **kw)

        def log_message(self, *a):
            pass

        def _reply(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version")
            if code == 405:
                self.send_header("Allow", "POST, OPTIONS")
            if body:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):   # MCP, Streamable HTTP without sessions or streams: one JSON reply per request
            if self.path.split("?")[0] != "/mcp":
                self.send_error(405)
                return
            try:
                msg = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._reply(400, json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}).encode())
                return
            replies = [r for r in (mcp.handle(store, m) for m in (msg if isinstance(msg, list) else [msg])) if r]
            if not replies:
                self._reply(202)
                return
            self._reply(200, json.dumps(replies if isinstance(msg, list) else replies[0], ensure_ascii=False).encode("utf-8"))

        def do_OPTIONS(self):
            self._reply(204 if self.path.split("?")[0] == "/mcp" else 405)

        def do_GET(self):
            if self.path.split("?")[0] == "/mcp":   # no server-initiated stream
                self._reply(405)
                return
            super().do_GET()

    srv = http.server.ThreadingHTTPServer((config.SERVE_HOST, port), Handler)
    print(f"serving {config.SITE_DIR} on http://{config.SERVE_HOST}:{port} (MCP at /mcp)", flush=True)
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
    sub.add_parser("mcp", help="MCP over stdio for agents that start a local command (serve also answers at /mcp)")
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
    elif a.cmd == "mcp":
        from . import mcp
        mcp.serve_stdio(mcp.DbStore())
    elif a.cmd == "status":
        con = db.connect()
        for row in con.execute("SELECT id, day, started, finished, status FROM runs ORDER BY id DESC LIMIT 10"):
            print(dict(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
