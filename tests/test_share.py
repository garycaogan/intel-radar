"""Offline checks for the share cards: specs, numbers, QR codes and the page hooks (no network, no browser)."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from radar import config, render, share
from radar.qrcodegen import QrCode

URL = "https://radar.example.com/#intel-radar?day={day}&item={item}"


def _video(tid=259, label="新起"):
    return {"thread_id": tid, "label": label, "name": "AI 水果宝宝动画", "kind": "内容题材", "summary": "水果拟人救援短剧。",
            "basis": ["6 个不同账号在做"], "days_seen": 1,
            "metrics": {"videos": 7, "authors": 6, "platforms": ["TikTok", "YouTube"], "views_total": 15217692,
                        "likes_total": 0, "max_vsr": 146.1, "vph_max": 107660.7,
                        "outlier": {"views": 13370000, "followers": 91530}},
            "samples": [{"title": "la reina de los mares", "url": "https://www.tiktok.com/@rayitoons/video/1",
                         "thumb": "img/97deca4d97dd7a05.jpg", "platform": "TikTok", "vertical": True, "views": 13370000},
                        {"title": "fruit rescue", "url": "https://www.youtube.com/watch?v=K-YoPrfh7rI",
                         "thumb": "https://i.ytimg.com/vi/K-YoPrfh7rI/hqdefault.jpg", "platform": "YouTube", "views": 9000}]}


def _x(tid=270, media=True):
    s = {"title": "We're opening a research preview of Claude Duet &amp; more https://t.co/abc", "author": "Claude",
         "handle": "claudeai", "url": "https://x.com/claudeai/status/1", "followers": 1941941, "views": 8527621,
         "likes": 52622, "bookmarks": 1, "published": "10-22 03:09",
         "media": {"type": "video", "thumb": "https://pbs.twimg.com/media/a.png"} if media else {}}
    return {"thread_id": tid, "label": "热议", "name": "Claude Duet 发布", "kind": "工具模型", "summary": "主动智能体。",
            "basis": [], "metrics": {"tweets": 3, "authors": 2, "views_total": 9706009, "likes_total": 57145},
            "breadth": {"tweets": 20, "authors": 19, "span_hours": 26.2}, "samples": [s]}


def _brief():
    repo = {"name": "alchaincyf/huashu-motion", "url": "https://github.com/alchaincyf/huashu-motion", "stars": 1432,
            "stars_today": None, "stars_per_day": 324.1, "created": "2026-10-18", "group": "AI 视频与图像",
            "zh": "一键生成产品宣传片", "use": "直接用于 AI 视频创作", "from_trending": False, "language": "JavaScript"}
    return {"day": "2026-10-23", "generated_bj": "2026-10-23 06:25", "history_days": 1, "summary": "今天的总结。",
            "headline": [{"title": "AI 水果宝宝动画在 TikTok 爆了", "verdict": "新题材，值得马上测", "why": "6 个账号在做。",
                          "grade": "S", "links": [{"section": "video", "name": "AI 水果宝宝动画", "thread_id": 259}]},
                         {"title": "Claude Duet 发布", "verdict": "先观察", "why": "主动智能体。", "grade": "B",
                          "links": [{"section": "x", "name": "Claude Duet 发布", "thread_id": 271}]}],
            "sections": {"video": [_video()], "x": [_x(), _x(271, media=False)], "news": [], "hotwords": [],
                         "tools": {"repos": [repo], "models": [], "stories": []}},
            "stats": {"items": 2312, "by_source": {"bili": 1}, "cost": {"est_usd_tikhub": 0.159, "est_cny_llm": 0.42},
                      "errors": ["TikHub HTTP 500: /api/v1/x"]}}


class Numbers(unittest.TestCase):
    def test_num_drops_point_zero(self):
        self.assertEqual(share.num(13370000), "1337万")
        self.assertEqual(share.num(15217692), "1521.8万")
        self.assertEqual(share.num(425), "425")

    def test_video_stats(self):
        self.assertEqual(share.video_stats(_video()["metrics"]),
                         [["6", "个账号在做"], ["1521.8万", "合计播放"], ["1337万", "9.2万粉的号单条播放"]])

    def test_one_video_is_the_total(self):
        m = {"authors": 2, "views_total": 453228, "likes_total": 96559, "max_vsr": 117,
             "outlier": {"views": 453228, "followers": 3868}}
        self.assertEqual(share.video_stats(m),
                         [["2", "个账号在做"], ["45.3万", "3868 粉的号单条播放"], ["9.7万", "合计点赞"]])

    def test_x_stats_use_breadth(self):
        self.assertEqual(share.x_stats(_x())[0], ["19", "个账号 26 小时内在聊"])


class QR(unittest.TestCase):
    def test_round_trip(self):
        text = URL.format(day="2026-10-23", item="video-259")
        q = share.qr(text)
        code = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
        n = q["n"]
        self.assertEqual(n, code.get_size())
        bits = "".join(f"{int(h, 16):04b}" for h in q["m"])
        for y in range(n):
            for x in range(n):
                self.assertEqual(bits[y * n + x] == "1", code.get_module(x, y))
        self.assertEqual(bits[:7], "1111111")      # top-left finder pattern


class Specs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        site = Path(self.tmp.name)
        (site / "img").mkdir()
        self.patches = [mock.patch.object(config, "SITE_DIR", site), mock.patch.object(config, "SHARE_URL", URL)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_one_spec_per_item(self):
        out = share.specs(_brief())
        ids = [s["id"] for s in out["items"]]
        self.assertEqual(ids, ["head-1", "head-2", "video-259", "x-270", "x-271", "repo-1"])
        head = out["items"][0]
        self.assertEqual((head["grade"], head["label"], head["title"]), ("必看", "新起", "AI 水果宝宝动画在 TikTok 爆了"))
        self.assertEqual(head["url"], "https://radar.example.com/#intel-radar?day=2026-10-23&item=head-1")
        self.assertEqual(out["meta"]["host"], "radar.example.com")
        self.assertEqual((out["meta"]["date"], out["meta"]["week"], out["meta"]["issue"]), ("10 月 23 日", "星期五", 2))

    def test_covers(self):
        out = {s["id"]: s for s in share.specs(_brief(), "../")["items"]}
        self.assertEqual(out["video-259"]["cover"], "../img/97deca4d97dd7a05.jpg")    # already local
        self.assertTrue(out["video-259"]["vertical"])
        self.assertEqual(out["x-270"]["cover"], "https://pbs.twimg.com/media/a.png")   # not cached: remote, CORS
        self.assertEqual(out["repo-1"]["pkey"], "gh")
        quote = out["x-271"]["quote"]
        self.assertEqual(quote["t"], "We're opening a research preview of Claude Duet & more")
        cached = share._name("https://pbs.twimg.com/media/a.png")
        (config.SITE_DIR / "img" / f"{cached}.png").write_bytes(b"\x89PNG")
        self.assertEqual(share.specs(_brief())["items"][3]["cover"], f"img/{cached}.png")

    def test_youtube_prefers_maxres(self):
        c = share._video_cover(_video()["samples"][1])
        self.assertEqual(c["cands"][0], "https://i.ytimg.com/vi/K-YoPrfh7rI/maxresdefault.jpg")

    def test_no_run_info(self):
        blob = json.dumps(share.specs(_brief()), ensure_ascii=False)
        for word in ("0.159", "0.42", "TikHub", "est_usd", "HTTP 500", "/api/"):
            self.assertNotIn(word, blob)

    def test_page_hooks(self):
        page = render.page(_brief(), ["2026-10-23"], at_root=True)
        data = json.loads(re.search(r'<script type="application/json" id="share-data">(.*?)</script>', page, re.S)[1])
        for item in data["items"]:
            self.assertIn(f'id="{item["id"]}"', page)
            self.assertIn(f'data-share="{item["id"]}"', page)
        self.assertIn('<dialog class="sd" id="share-dlg"', page)
        self.assertNotIn("0.159", page)


if __name__ == "__main__":
    unittest.main()
