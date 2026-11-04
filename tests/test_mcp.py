"""Offline checks for the MCP tools: protocol replies, tool answers from a stored brief and a timeline folder,
stdio transport."""

import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from radar import config, db, mcp, timeline
from tests.test_share import _brief


def _timeline_dir(folder: Path, today: dt.date) -> None:
    """A small timeline around today: one coming big one, one long past, one scheduled day that went by, one month."""
    def ev(eid, lane, date, status, heat, title, cat="模型"):
        return {"id": eid, "lane": lane, "category": cat, "date": date, "status": status, "heat": heat, "title": title,
                "what": f"{title} 是什么", "why": f"{title} 和做视频的关系",
                "sources": [{"name": "官方", "url": f"https://example.com/{eid}"}], "checked": today.isoformat()}
    soon, past, due = today + dt.timedelta(days=10), today - dt.timedelta(days=45), today - dt.timedelta(days=3)
    nxt = timeline.add_months(today.replace(day=1), 1)
    events = [ev("soon-launch", "video", soon.isoformat(), "scheduled", 3, "星河发售", "游戏"),
              ev("old-model", "ai", past.isoformat(), "done", 2, "旧模型发布"),
              ev("due-day", "ai", due.isoformat(), "scheduled", 2, "定档大会", "大会"),
              ev("next-month", "ai", f"{nxt:%Y-%m}", "expected", 1, "下月新品", "产品")]
    folder.mkdir(parents=True)
    (folder / "events.json").write_text(json.dumps({"events": events, "parked": [ev("parked-one", "ai", soon.isoformat(), "done", 1, "不上页面")]}, ensure_ascii=False),
                                        encoding="utf-8")
    topics = [{"id": "wave-a", "title": "甲热点", "kind": "动画", "start_label": "2025.6", "keyword": "wave a", "what": "甲是什么",
               "record": "官方说破了纪录", "why": "甲怎么跟", "sources": [{"name": "YouTube Blog", "url": "https://blog.youtube/a"}]},
              {"id": "wave-b", "title": "乙热点", "kind": "梗", "start_label": "2025.3", "keyword": "wave b", "what": "乙是什么",
               "why": "乙怎么跟", "sources": [{"name": "Wiki", "url": "https://en.wikipedia.org/wiki/B"}]}]
    (folder / "youtube_hot.json").write_text(json.dumps({"topics": topics}, ensure_ascii=False), encoding="utf-8")
    (folder / "youtube_trends.json").write_text(json.dumps({
        "fetched": "2026-11-04", "source": "Google Trends，YouTube 搜索类目", "time": "2025-01-01 2026-11-03", "anchor": "wave a",
        "start": 1735430400, "series": {"wave a": {"values": [0, 1, 5, 40, 100, 60, 30, 20], "ratio": 1},
                                        "wave b": {"values": [10, 12, 11, 30, 100, 50, 20, 15], "ratio": 2.5}}}), encoding="utf-8")


class Mcp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.today = timeline.today_bj()
        _timeline_dir(root / "timeline", self.today)
        self.patches = [mock.patch.object(config, "DATA_DIR", root), mock.patch.object(config, "DB_PATH", root / "radar.db"),
                        mock.patch.object(config, "SITE_DIR", root / "site"), mock.patch.object(config, "LOG_DIR", root / "logs"),
                        mock.patch.object(config, "TIMELINE_DIR", root / "timeline")]
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
        self.assertEqual(len(names), 9)
        self.assertIn("radar_timeline", names)
        self.assertIn("radar_youtube_history", names)
        self.assertEqual(init["serverInfo"]["version"], "1.1.0")
        self.assertIn("热点时间线", init["instructions"])
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

    def test_timeline(self):
        bad, text = self.call("radar_timeline")
        self.assertFalse(bad)
        self.assertTrue(text.startswith("# AI 热点时间线 · AI 视频线和 AI 线 · 接下来\n"))
        self.assertIn("页面：https://radar.example.com/timeline/（时间窗 ", text)
        self.assertIn("｜星河发售**（AI 视频线 · 游戏 · 已定档 · 还有 10 天 · 大热点）", text)
        self.assertIn("星河发售 是什么 跟做视频的关系：星河发售 和做视频的关系", text)
        self.assertIn("来源：[官方](https://example.com/soon-launch) · 详情：https://radar.example.com/timeline/#ev-soon-launch", text)
        self.assertIn("下月新品", text)
        for gone in ("旧模型发布", "定档大会", "不上页面"):     # past ones, and parked never
            self.assertNotIn(gone, text)
        self.assertLess(text.index("星河发售"), text.index("下月新品"))

    def test_timeline_filters(self):
        bad, text = self.call("radar_timeline", when="past")
        self.assertIn("已经发生（新的在前）", text)
        self.assertIn("日期已过，待确认", text)            # scheduled day that went by, not confirmed
        self.assertLess(text.index("定档大会"), text.index("旧模型发布"))
        self.assertNotIn("星河发售", text)
        bad, text = self.call("radar_timeline", lane="ai", when="all")
        self.assertIn("AI 线 · 前后各半年", text)
        self.assertNotIn("星河发售", text)
        past = self.today - dt.timedelta(days=45)
        bad, text = self.call("radar_timeline", when="all", month=f"{past:%Y-%m}")
        self.assertIn("旧模型发布", text)
        self.assertNotIn("星河发售", text)
        bad, text = self.call("radar_timeline", when="all", q="星河 游戏")
        self.assertIn("星河发售", text)
        self.assertNotIn("下月新品", text)
        bad, text = self.call("radar_timeline", q="没有这个词")
        self.assertIn("没有符合条件的事件", text)
        bad, text = self.call("radar_timeline", when="all", limit=1)
        self.assertEqual(text.count("\n- **"), 1)
        for args in ({"lane": "film"}, {"when": "later"}, {"month": "2026/12"}, {"limit": "many"}):
            bad, text = self.call("radar_timeline", **args)
            self.assertTrue(bad, args)

    def test_youtube_history(self):
        bad, text = self.call("radar_youtube_history")
        self.assertFalse(bad)
        self.assertTrue(text.startswith("# YouTube 历史热点\n"))
        self.assertIn("「高峰倍数」是和甲热点放在一起查出来的", text)
        self.assertIn("页面：https://radar.example.com/timeline/#yt-hot", text)
        self.assertLess(text.index("1. **乙热点**"), text.index("2. **甲热点**"))   # biggest peak first
        self.assertIn("高峰是基准的 2.5 倍", text)
        self.assertIn("硬数据：官方说破了纪录", text)
        self.assertIn("详情：https://radar.example.com/timeline/#yt-wave-a", text)
        bad, text = self.call("radar_youtube_history", topic="WAVE B")
        self.assertIn("1. **乙热点**", text)
        self.assertNotIn("甲热点**", text)
        bad, text = self.call("radar_youtube_history", topic="丙")
        self.assertTrue(bad)
        self.assertIn("现在收的是：乙热点、甲热点。", text)

    def test_timeline_without_files(self):
        with mock.patch.object(config, "TIMELINE_DIR", Path(self.tmp.name) / "none"):
            self.assertEqual(self.call("radar_timeline"), (True, "热点时间线还没有数据。"))
            self.assertEqual(self.call("radar_youtube_history"), (True, "YouTube 历史热点还没有数据。"))
        (Path(self.tmp.name) / "timeline" / "events.json").write_text("{oops", encoding="utf-8")
        bad, text = self.call("radar_timeline")
        self.assertTrue(bad)
        self.assertIn("第 1 行", text)

    def test_stdio(self):
        lines = "\n".join(json.dumps(m) for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "radar_timeline", "arguments": {"lane": "video"}}}]) + "\nnot json\n"
        out = io.StringIO()
        mcp.serve_stdio(self.store, io.StringIO(lines), out)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, 2, 3, None])
        self.assertEqual(len(replies[1]["result"]["tools"]), 9)
        self.assertIn("星河发售", replies[2]["result"]["content"][0]["text"])
        self.assertEqual(replies[3]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
