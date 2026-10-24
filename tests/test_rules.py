"""Offline checks for parsing, scoring and the label rules (no network, no model)."""

import json
import time
import unittest

from radar import analyze, score
from radar.sources import common


class Parsing(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(common.count("1,234 views"), 1234)
        self.assertEqual(common.count("1.3K"), 1300)
        self.assertEqual(common.count("12万次观看"), 120000)
        self.assertEqual(common.count("204K subscribers"), 204000)
        self.assertIsNone(common.count(""))

    def test_relative_time(self):
        now = 1_800_000_000
        self.assertEqual(common.relative_ts("3 days ago", now), now - 3 * 86400)
        self.assertEqual(common.relative_ts("Streamed 10 hours ago", now), now - 36000)
        self.assertEqual(common.relative_ts("2天前", now), now - 2 * 86400)
        self.assertIsNone(common.relative_ts("", now))

    def test_twitter_time(self):
        self.assertIsNotNone(common.iso_ts("Tue Oct 20 00:17:47 +0000 2026"))

    def test_blocked(self):
        self.assertTrue(common.blocked("Qwen-Image-Uncensored"))
        self.assertTrue(common.blocked("best-face-swap"))
        self.assertFalse(common.blocked("Lightricks/LTX-2.5"))

    def test_lane(self):
        self.assertTrue(common.in_lane("用 AI 做动画"))
        self.assertTrue(common.in_lane("Seedance 2.5 实测"))
        self.assertFalse(common.in_lane("he said it was fine"))   # "ai" inside a word
        self.assertFalse(common.in_lane("谭乔回应二仙桥大爷"))


class Scoring(unittest.TestCase):
    def test_video_signals(self):
        now = time.time()
        it = {"kind": "video", "source": "bili", "published_at": now - 10 * 3600,
              "metrics": {"views": 50000, "followers": 2000, "likes": 1000, "favorites": 500}}
        s = score.derive(it, now)
        self.assertEqual(s["vph"], 5000.0)
        self.assertEqual(s["vsr"], 25.0)

    def test_new_account_floor(self):
        now = time.time()
        it = {"kind": "video", "source": "youtube", "published_at": now - 2 * 3600, "metrics": {"views": 9000, "followers": 3}}
        s = score.derive(it, now)
        self.assertEqual(s["vsr"], 9.0)        # followers floored at 1000
        self.assertEqual(s["vph"], 1500.0)     # age floored at 6 h


def _topic(authors=1, max_vsr=None, probe=None, yt=None):
    return {"metrics": {"authors": authors, "max_vsr": max_vsr}, "probe_bili": probe, "probe_youtube": yt}


def _probe(recent, before):
    rpd, bpd = round(recent / 7, 2), round(before / 53, 2)
    return {"term": "x", "recent_n": recent, "before_n": before, "recent_per_day": rpd, "before_per_day": bpd,
            "capped": recent >= 1000 and before >= 1000, "ratio": round((rpd + 0.2) / (bpd + 0.2), 2)}


class Labels(unittest.TestCase):
    def test_new_needs_two_signals(self):
        label, basis = analyze._label_video(_topic(authors=4, probe=_probe(15, 6)), None, 0)
        self.assertEqual(label, "新起")
        self.assertTrue(any("53 天只有 6 条" in b for b in basis))

    def test_new_term_alone_is_watch(self):
        label, _ = analyze._label_video(_topic(authors=1, probe=_probe(15, 6)), None, 0)
        self.assertEqual(label, "观察")

    def test_burst_is_rising(self):
        label, _ = analyze._label_video(_topic(authors=3, probe=_probe(300, 700)), None, 0)
        self.assertEqual(label, "升温")

    def test_partly_old_youtube_format_is_rising(self):
        yt = {"term": "Hindi 3D animated story", "year_n": 20, "match": 7, "match_recent": 4, "oldest_match_days": 60}
        label, _ = analyze._label_video(_topic(authors=6, max_vsr=50, yt=yt), None, 0)
        self.assertEqual(label, "升温")

    def test_capped_before_is_not_burst(self):
        pb = {**_probe(444, 1000), "recent_match": 0, "before_match": 2, "before_page": 20}
        label, _ = analyze._label_video(_topic(authors=3, probe=pb), None, 0)
        self.assertNotEqual(label, "升温")

    def test_evergreen(self):
        label, _ = analyze._label_video(_topic(authors=5, probe=_probe(1000, 1000)), None, 0)
        self.assertEqual(label, "持续")

    def test_youtube_new_share(self):
        yt = {"term": "claude animation", "year_n": 20, "match": 6, "match_recent": 5, "oldest_match_days": 9}
        label, _ = analyze._label_video(_topic(authors=3, yt=yt), None, 0)
        self.assertEqual(label, "新起")

    def test_title_match_new(self):
        pb = {**_probe(1000, 1000), "recent_match": 12, "before_match": 1, "before_page": 20}
        label, basis = analyze._label_video(_topic(authors=3, probe=pb), None, 0)
        self.assertEqual(label, "新起")
        self.assertTrue(any("以前几乎没有" in b for b in basis))

    def test_title_match_evergreen(self):
        pb = {**_probe(1000, 1000), "recent_match": 15, "before_match": 14, "before_page": 20}
        label, _ = analyze._label_video(_topic(authors=6, max_vsr=30, probe=pb), None, 0)
        self.assertEqual(label, "持续")

    def test_youtube_title_match(self):
        from radar.sources import youtube
        self.assertTrue(youtube.title_matches("Claude Code TV", "Claude made my old TV like new"))
        self.assertFalse(youtube.title_matches("Claude Code TV", "Claude Code full course"))
        self.assertTrue(youtube.title_matches("AI food slide", "Ultimate Oddly Satisfying AI Food Slide"))

    def test_memory_and_cooling(self):
        hit = {"thread_id": 1, "first_day": "2026-10-10"}
        label, _ = analyze._label_video(_topic(authors=2, probe=_probe(20, 400)), hit, 5)
        self.assertEqual(label, "回落")


class XLabels(unittest.TestCase):
    def _t(self, breadth, tweets=2, authors=2, views=140000, likes=100):
        return {"breadth": breadth, "metrics": {"tweets": tweets, "authors": authors, "views_total": views, "likes_total": likes}}

    def test_creator_joins_x_is_burst(self):
        b = {"term": "影视飓风", "tweets": 17, "authors": 16, "span_hours": 15.4}
        self.assertEqual(analyze._x_label(self._t(b), None, 0)[0], "爆发")

    def test_slow_spread_is_hot(self):
        b = {"term": "PHP", "tweets": 18, "authors": 17, "span_hours": 27.6}
        self.assertEqual(analyze._x_label(self._t(b), None, 0)[0], "热议")

    def test_known_thread_never_bursts(self):
        b = {"term": "x", "tweets": 20, "authors": 20, "span_hours": 5}
        self.assertEqual(analyze._x_label(self._t(b), {"first_day": "2026-10-20"}, 5)[0], "热议")

    def test_single_source_money_claim(self):
        t = {**self._t({"term": "AI虚拟主播", "tweets": 9, "authors": 6, "span_hours": 30.7}, tweets=1, authors=1), "lane": "项目与变现",
             "name": "AI 虚拟主播直播打赏", "summary": "4 小时获得 3258 美元打赏"}
        self.assertTrue(analyze.single_source_claim(t))
        self.assertTrue(any("未核实" in b for b in analyze._x_label(t, None, 0)[1]))
        self.assertFalse(analyze.single_source_claim({**t, "metrics": {**t["metrics"], "authors": 3}}))
        self.assertTrue(analyze.single_source_claim({**t, "name": "AI 虚拟主播直播打赏", "summary": "4 小时获得 3258 美元打赏"}))
        self.assertFalse(analyze.single_source_claim({**t, "name": "AI 算命命理数据集开源", "summary": "有人开源了 57GB 的玄学数据集，67.5 万张紫微命盘"}))

    def test_small(self):
        self.assertEqual(analyze._x_label(self._t({"term": "x", "tweets": 4, "authors": 4, "span_hours": 30}), None, 0)[0], "关注")


class Relevance(unittest.TestCase):
    def test_generic_topic_names(self):
        self.assertTrue(analyze.generic_name("AI副业赚钱教程"))
        self.assertTrue(analyze.generic_name("AI 工具测评"))
        self.assertFalse(analyze.generic_name("Seedance 2.5写实教程"))
        self.assertFalse(analyze.generic_name("AI漫剧合集连载"))

    def test_off_lane(self):
        self.assertTrue(common.off_lane("OpenAI 发现 PHP 漏洞"))
        self.assertTrue(common.off_lane("Anthropic 80B 亏损与 IPO"))
        self.assertTrue(common.off_lane("cloudflare/security-audit-skill"))
        self.assertTrue(common.off_lane("AI炒股Agent开源"))
        self.assertFalse(common.off_lane("Kling 4.0 上线"))
        self.assertFalse(common.off_lane("影视飓风入驻 X"))

    def test_strong_ai(self):
        self.assertTrue(common.strong_ai("赵本山演唱金曲【AI MV大赛】"))
        self.assertFalse(common.strong_ai("影石超旗舰全景相机评测：芯片和机器人"))

    def test_model_family(self):
        self.assertEqual(analyze.model_family("OpenMOSS-Team/MOSS-Image-SFT"), analyze.model_family("Comfy-Org/MOSS-Image"))
        self.assertEqual(analyze.model_family("black-forest-labs/flux-3-image-klein-comfy"), "flux-3-image-klein")
        self.assertEqual(analyze.model_family("Lightricks/LTX-2.5"), "ltx-2.5")

    def test_hf_allowlist(self):
        space = lambda owner: {"kind": "space", "author": owner, "extra": {}}
        model = lambda owner, pipe: {"kind": "model", "author": owner, "extra": {"pipeline": pipe}}
        self.assertTrue(analyze._hf_keep(space("black-forest-labs")))
        self.assertFalse(analyze._hf_keep(space("hitman911")))
        self.assertTrue(analyze._hf_keep(model("someone", "image-to-video")))
        self.assertFalse(analyze._hf_keep(model("someone", "text-generation")))
        self.assertTrue(analyze._hf_keep(model("Qwen", "image-text-to-text")))


class Media(unittest.TestCase):
    def test_embeds(self):
        from radar import render
        m = render.media_of({"url": "https://www.bilibili.com/video/BV1sXJD6BELq"})
        self.assertEqual(m["kind"], "iframe")
        self.assertIn("player.bilibili.com/player.html?isOutside=true&bvid=BV1sXJD6BELq", m["src"])
        m = render.media_of({"url": "https://www.youtube.com/shorts/fP2SGGVsaB0"})
        self.assertTrue(m["src"].startswith("https://www.youtube-nocookie.com/embed/fP2SGGVsaB0"))
        m = render.media_of({"url": "https://x.com/a/status/1", "media": {"mp4": "https://video.twimg.com/x.mp4", "aspect": 0.56}})
        self.assertEqual((m["kind"], m["vertical"]), ("video", True))
        m = render.media_of({"url": "https://x.com/a/status/1", "thumb": "https://pbs.twimg.com/p.jpg"})
        self.assertEqual(m["kind"], "link")

    def test_x_media_rendition(self):
        from radar.sources import xtw
        t = {"tweet_id": "1", "screen_name": "a", "text": "hi", "created_at": "Tue Oct 20 00:17:47 +0000 2026",
             "media": {"video": [{"media_url_https": "https://pbs.twimg.com/t.jpg", "original_info": {"width": 1280, "height": 720},
                                  "variants": [{"content_type": "application/x-mpegURL", "url": "m3u8"},
                                               {"content_type": "video/mp4", "bitrate": 832000, "url": "low.mp4"},
                                               {"content_type": "video/mp4", "bitrate": 2176000, "url": "mid.mp4"},
                                               {"content_type": "video/mp4", "bitrate": 10368000, "url": "high.mp4"}]}]}}
        media = xtw._card(t, "x:test")["extra"]["media"]
        self.assertEqual((media["type"], media["mp4"], media["aspect"]), ("video", "mid.mp4", 1.778))

    def test_headline_why_fallback(self):
        from radar import render
        h = {"comment": "有手法，可以学一下。这个题材突然冒出来，四个账号在做。最大的坑是太技术化。"}
        self.assertEqual(render._why(h), "这个题材突然冒出来，四个账号在做。")
        self.assertEqual(render._why({"why": "一句话", "comment": "x"}), "一句话")


class Deep(unittest.TestCase):
    def test_bili_tag_match_and_ai_filter(self):
        from radar import deep
        rows = [{"bvid": "BV1", "title": "《墨染楠楠》第一集", "tag": "原创漫画,ai,AI漫剧", "pubdate": 1},
                {"bvid": "BV2", "title": "1分钟教会你AI漫剧完整流程", "tag": "", "pubdate": 2},
                {"bvid": "BV3", "title": "漫剧推荐", "tag": "动画", "pubdate": 3}]
        self.assertEqual([c["id"] for c in deep._matches_bili("AI漫剧", rows)], ["bili:BV1", "bili:BV2"])
        game = [{"bvid": "BV4", "title": "第一人称重制！高品质模型带来全新体验", "tag": "游戏,战斗", "pubdate": 4},
                {"bvid": "BV5", "title": "第一人称短片《告别》", "tag": "AI全民制作人,AI短片", "pubdate": 5}]
        self.assertEqual([c["id"] for c in deep._matches_bili("第一人称", game)], ["bili:BV5"])

    def test_bars_render(self):
        from radar import render
        qs = [{"start": 1_760_000_000 + i * 91 * 86400, "end": 0, "match": m} for i, m in enumerate([0, 0, 1, 0, 3, 2, 1, 5])]
        html = render._bars(qs, "第一人称")
        self.assertEqual(html.count('class="bar"') + html.count('class="bar zero"'), 8)
        self.assertEqual(html.count("<em>"), 1)          # peak and latest are the same bar here
        empty = render._bars([{**q, "match": 0} for q in qs], "水果宝宝")
        self.assertIn("chart-empty", empty)
        self.assertNotIn('class="bars"', empty)


class DailyDeep(unittest.TestCase):
    def test_cost_since_snapshot(self):
        from radar import net
        led = net.Ledger()
        led.hit("tikhub"); led.charge("/api/v1/youtube/web_v2/get_general_search_v2"); led.add_tokens("some-model", 1000, 100)
        mark = led.snapshot()
        led.hit("tikhub"); led.charge("/api/v1/douyin/search/fetch_video_search_v3"); led.add_tokens("some-model", 500, 50)
        since = led.summary(since=mark)
        self.assertEqual(since["calls"], {"tikhub": 1})
        self.assertEqual(since["est_usd_tikhub"], 0.01)
        self.assertEqual(since["tokens"], {"some-model": [500, 50]})
        self.assertEqual(led.summary()["est_usd_tikhub"], 0.012)

    def _run(self, deep_result):
        from unittest import mock
        from radar import __main__ as cli
        calls = []
        brief = {"day": "2026-10-23", "stats": {}}
        def fake_deep(day, labels):
            calls.append(("deep", labels))
            if isinstance(deep_result, Exception):
                raise deep_result
            return deep_result
        with mock.patch.object(cli.pipeline, "run", lambda *a: calls.append(("run",)) or brief), \
                mock.patch.object(cli.deep, "run", fake_deep), \
                mock.patch.object(cli.db, "connect", lambda: None), mock.patch.object(cli.db, "save_brief", lambda *a: None), \
                mock.patch.object(cli.render, "write_site", lambda *a: calls.append(("render",))):
            self.assertEqual(cli.main(["run"]), 0)
        return calls, brief

    def test_deep_runs_for_rising_only(self):
        calls, brief = self._run({"topics": [{}, {}], "cost": {"est_usd_tikhub": 0.03}})
        self.assertEqual([c[0] for c in calls], ["run", "deep", "render"])
        self.assertEqual(calls[1][1], ("新起", "升温"))
        self.assertEqual(brief["stats"]["deep"]["topics"], 2)

    def test_deep_failure_keeps_the_brief(self):
        calls, _ = self._run(RuntimeError("TikHub down"))
        self.assertEqual([c[0] for c in calls], ["run", "deep"])

    def test_no_rising_topics(self):
        calls, brief = self._run(None)
        self.assertEqual([c[0] for c in calls], ["run", "deep"])
        self.assertNotIn("deep", brief["stats"])


class Settings(unittest.TestCase):
    def test_env_file(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        from radar import config
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("# comment\n\nRADAR_T1=plain\nexport RADAR_T2 = 'quoted value'\nRADAR_T3=\"x=y\"\nRADAR_T4=from-file\nnot a pair\n",
                         encoding="utf-8")
            with mock.patch.dict(os.environ, {"RADAR_T4": "from-env"}):
                config.load_env_file(f)
                self.assertEqual(os.environ["RADAR_T1"], "plain")
                self.assertEqual(os.environ["RADAR_T2"], "quoted value")
                self.assertEqual(os.environ["RADAR_T3"], "x=y")
                self.assertEqual(os.environ["RADAR_T4"], "from-env")       # the real environment wins
            for k in ("RADAR_T1", "RADAR_T2", "RADAR_T3"):
                os.environ.pop(k, None)
        config.load_env_file(Path("/nonexistent/.env"))                     # a missing file is fine

    def test_headline_voice(self):
        from radar import analyze
        named = analyze.head_voice("某社群", "小王")
        self.assertEqual(named["role"], "你是某社群的情报主编，替小王写")
        self.assertIn("某社群社群里的", named["readers"])
        self.assertEqual(named["voice"], "小王的说话方式")
        plain = analyze.head_voice("", "")
        self.assertEqual(plain["readers"], "")
        prompt = analyze.HEAD_PROMPT.format(videos="", xs="", tools="", **plain)
        self.assertTrue(prompt.startswith("你是一份每日 AI 情报的主编，写今天的「AI 情报日报」头条。读者是AI 视频创作者"))
        self.assertNotIn("{", prompt.split("只输出")[0])

    def test_reader_json_has_no_run_info(self):
        from radar import render
        brief = {"day": "2026-10-23", "stats": {"items": 2312, "by_source": {"bili": 1}, "cost": {"est_usd_tikhub": 0.2},
                                                "errors": ["x"], "secs": 300, "collect": {}}}
        self.assertEqual(render.public_brief(brief)["stats"], {"items": 2312, "by_source": {"bili": 1}})
        self.assertIn("cost", brief["stats"])                                # the stored brief keeps it


if __name__ == "__main__":
    unittest.main()
