"""Offline checks for the MCP tools: protocol replies, tool answers from a stored brief, stdio transport."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from radar import config, db, mcp
from tests.test_share import _brief


class Mcp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(config, "DATA_DIR", root), mock.patch.object(config, "DB_PATH", root / "radar.db"),
                        mock.patch.object(config, "SITE_DIR", root / "site"), mock.patch.object(config, "LOG_DIR", root / "logs")]
        for p in self.patches:
            p.start()
        con = db.connect()
        brief = _brief()
        brief["sections"]["video"][0]["samples"][0]["title"] = "la reina &amp; the sea"
        db.save_brief(con, brief["day"], brief)
        db.save_deep(con, brief["day"], {"day": brief["day"], "cost": {"est_usd_tikhub": 0.03}, "topics": [{
            "name": "AI 水果宝宝动画", "label": "新起", "thread_id": 259,
            "note": {"origin": "最早能查到的是 2026-07 TikTok 上的一条。", "spread": "后来到了 YouTube。", "now": "正在起量。"},
            "earliest": {"title": "first one", "url": "https://www.tiktok.com/@a/video/1", "platform": "TikTok", "views": 1000,
                         "published": "2026-07-01"},
            "hits": [{"title": "big one", "url": "https://www.youtube.com/watch?v=abcdefghijk", "platform": "YouTube", "views": 2000000}]}]})
        con.close()
        self.store = mcp.DbStore("https://radar.example.com")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def rpc(self, method, params=None, mid=1):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if mid is not None:
            msg["id"] = mid
        return mcp.handle(self.store, msg)

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["isError"], res["content"][0]["text"]

    def test_protocol(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "intel-radar")
        self.assertIn("tools", init["capabilities"])
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(self.rpc("notifications/initialized", mid=None))
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(len(names), 7)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in mcp.TOOLS))
        self.assertEqual(self.rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(self.rpc("frobnicate")["error"]["code"], -32601)
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_today_links_and_no_run_info(self):
        bad, text = self.call("radar_today")
        self.assertFalse(bad)
        self.assertIn("10 月 23 日", text)
        self.assertIn("AI 水果宝宝动画在 TikTok 爆了", text)
        self.assertIn("https://radar.example.com/d/2026-10-23.html#head-1", text)
        self.assertIn("https://radar.example.com/d/2026-10-23.html#video-259", text)   # 新起 topic under 今天冒头
        for word in ("0.159", "0.42", "TikHub", "est_usd", "HTTP 500"):
            self.assertNotIn(word, text)

    def test_topics_filters_and_entities(self):
        bad, text = self.call("radar_video_topics", label="新起")
        self.assertFalse(bad)
        self.assertIn("la reina & the sea", text)                   # entities decoded
        self.assertIn("6 个账号在做", text)
        bad, text = self.call("radar_video_topics", label="回落")
        self.assertIn("没有符合条件", text)
        bad, text = self.call("radar_video_topics", label="爆款")
        self.assertTrue(bad)
        bad, text = self.call("radar_x_topics")
        self.assertIn("https://radar.example.com/d/2026-10-23.html#x-270", text)
        bad, text = self.call("radar_tools", kind="开源项目")
        self.assertIn("alchaincyf/huashu-motion", text)
        self.assertIn("#repo-1", text)

    def test_origin_search_days(self):
        bad, text = self.call("radar_topic_origin", topic="水果宝宝")
        self.assertFalse(bad)
        self.assertIn("最早能查到的是 2026-07", text)
        self.assertIn("https://radar.example.com/deep/2026-10-23.html", text)
        self.assertNotIn("0.03", text)
        bad, text = self.call("radar_search", q="duet")
        self.assertIn("Claude Duet", text)
        bad, text = self.call("radar_list_issues")
        self.assertIn("第 2 期", text)
        bad, text = self.call("radar_today", day="2026-01-01")
        self.assertTrue(bad)
        self.assertIn("2026-10-23", text)

    def test_stdio(self):
        lines = "\n".join(json.dumps(m) for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]) + "\nnot json\n"
        out = io.StringIO()
        mcp.serve_stdio(self.store, io.StringIO(lines), out)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, 2, None])
        self.assertEqual(replies[2]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
