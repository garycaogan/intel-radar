"""热点时间线: dates, the moving window, today's split, the data files in timeline/, and the page render writes."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from radar import config, render, timeline as tl
from tests.test_share import _brief

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 11, 4)


def ev(**kw):
    base = {"id": "x", "lane": "ai", "category": "模型", "date": "2026-11-19", "status": "scheduled", "heat": 2,
            "title": "t", "what": "w", "why": "y", "sources": [{"name": "s", "url": "https://example.com/"}]}
    base.update(kw)
    base["start"], base["end"], base["precision"] = tl.parse_date(base["date"])
    return base


def raw(**kw):
    base = {"id": "ok", "lane": "ai", "category": "模型", "date": "2026-11-19", "status": "done", "heat": 3,
            "title": "t", "what": "w", "why": "y", "sources": [{"name": "s", "url": "https://example.com/"}]}
    base.update(kw)
    return base


class Dates(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(tl.parse_date("2026-11-19"), (dt.date(2026, 11, 19), dt.date(2026, 11, 19), "day"))
        self.assertEqual(tl.parse_date("2027-02"), (dt.date(2027, 2, 1), dt.date(2027, 2, 28), "month"))
        self.assertEqual(tl.parse_date("2027-Q1"), (dt.date(2027, 1, 1), dt.date(2027, 3, 31), "quarter"))
        with self.assertRaises(ValueError):
            tl.parse_date("2026/11/19")

    def test_window_is_six_months_each_side(self):
        self.assertEqual(tl.window(TODAY), (dt.date(2026, 5, 1), dt.date(2027, 5, 31)))
        self.assertEqual(tl.window(dt.date(2027, 1, 31)), (dt.date(2026, 7, 1), dt.date(2027, 7, 31)))

    def test_labels(self):
        self.assertEqual(tl.date_label(ev(date="2026-11-19"), TODAY), "11 月 19 日 · 周四")
        self.assertEqual(tl.date_label(ev(date="2027-02"), TODAY), "2027 年 2 月内")
        self.assertEqual(tl.date_label(ev(date="2027-Q1"), TODAY), "2027 年一季度")
        self.assertEqual(tl.countdown(ev(date="2026-11-19"), TODAY), "还有 15 天")
        self.assertEqual(tl.countdown(ev(date="2026-12"), TODAY), "")


class Status(unittest.TestCase):
    def test_scheduled_day_that_passed_waits_for_confirmation(self):
        self.assertEqual(tl.status_of(ev(date="2026-11-01"), TODAY)[0], "due")
        self.assertEqual(tl.status_of(ev(date="2026-11-04"), TODAY)[0], "today")
        self.assertEqual(tl.status_of(ev(date="2026-11-05"), TODAY)[0], "sched")
        self.assertEqual(tl.status_of(ev(date="2026-11-01", status="done"), TODAY)[0], "done")

    def test_today_month_splits_around_the_line(self):
        events = [ev(id="a", date="2026-11-02", status="done"), ev(id="b", date="2026-11-19"),
                  ev(id="c", date="2026-11"), ev(id="d", date="2026-10-20", status="done")]
        bl = tl.blocks(events, TODAY)
        self.assertEqual([(b["month"].month, b["past"]) for b in bl], [(10, True), (11, True), (11, False)])
        self.assertEqual([e["id"] for e in bl[2]["items"]], ["b", "c"])
        html = tl.month_list(events, TODAY, "v")
        self.assertLess(html.index('id="v-m-2026-11"'), html.index('id="v-today"'))
        self.assertLess(html.index('id="v-today"'), html.index('id="v-m-2026-11-next"'))
        self.assertIn('id="v-past"', html)

    def test_strip_has_every_month_and_today(self):
        html = tl.strip([ev(id="a", date="2026-07-17", status="done", heat=3), ev(id="b", date="2026-11-19")], TODAY)
        self.assertEqual(html.count('class="hs-col'), 13)
        self.assertEqual(html.count("hs-now"), 1)
        self.assertIn('href="#ev-a"', html)


class DataFile(unittest.TestCase):
    def test_bad_events_are_left_out_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "events.json"
            nosrc = raw(id="nosrc")
            del nosrc["sources"]
            p.write_text(json.dumps({"events": [raw(), nosrc, raw(id="badlane", lane="film"), raw(id="ok"), "junk"]}),
                         encoding="utf-8")
            events, problems = tl.load(p)
        self.assertEqual([e["id"] for e in events], ["ok"])
        self.assertEqual(len(problems), 4)              # no source, bad lane, duplicate id, not an object

    def test_broken_json_names_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "events.json"
            p.write_text('{\n  "events": [\n    {"id": "a",}\n  ]\n}\n', encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                tl.load(p)
        self.assertIn("第 3 行", str(cm.exception))

    def test_pictures_come_from_images_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "images").mkdir()
            (folder / "images" / "a.jpg").write_bytes(b"\xff\xd8")
            (folder / "secret.jpg").write_bytes(b"\xff\xd8")
            (folder / "events.json").write_text(json.dumps({"events": [
                raw(id="a", image={"file": "a.jpg", "from": "https://www.example.com/p"}),
                raw(id="b", image={"file": "missing.jpg"}),
                raw(id="c", image={"file": "../secret.jpg"}),
                raw(id="d")]}), encoding="utf-8")
            events, problems = tl.load(folder / "events.json")
        by = {e["id"]: e for e in events}
        self.assertEqual(by["a"]["image"]["file"], "a.jpg")
        self.assertIsNone(by["b"]["image"])
        self.assertIsNone(by["c"]["image"])
        self.assertEqual(len(problems), 2)
        self.assertIn('src="img/a.jpg"', tl.picture(by["a"], "ev-img", 320, 180))
        self.assertIn('title="图：example.com"', tl.picture(by["a"], "ev-img", 320, 180))
        self.assertIn('class="ev-img ph lane-ai"', tl.picture(by["d"], "ev-img", 320, 180))   # text cover
        self.assertEqual(tl.public_json([by["a"]], TODAY)["events"][0]["image"], "timeline/img/a.jpg")

    def test_published_file_is_clean(self):
        events, problems = tl.load(ROOT / "timeline" / "events.json")
        self.assertEqual(problems, [])
        self.assertTrue(events)
        for e in events:
            self.assertTrue(all(s["url"].startswith("https://") for s in e["sources"]), e["id"])
            self.assertTrue(e.get("checked"), f"{e['id']} has no checked date")

    def test_sample_data_ships_without_pictures(self):
        for name in ("events.json", "youtube_hot.json"):
            data = json.loads((ROOT / "timeline" / name).read_text(encoding="utf-8"))
            for key in ("events", "parked", "topics"):
                for item in data.get(key) or []:
                    self.assertNotIn("image", item, f"{name} {item['id']}")
                    self.assertNotIn("note", item, f"{name} {item['id']}")
        self.assertFalse((ROOT / "timeline" / "images").exists())


class YouTubeWaves(unittest.TestCase):
    def test_wave_numbers(self):
        w = tl.wave([0, 2, 12, 40, 100, 70, 55, 30, 20])
        self.assertEqual((w["peak"], w["up"], w["half"], w["now"]), (4, 1, 3, 20))
        self.assertEqual(tl.wave([30, 32, 31, 35, 60, 100, 80])["up"], 1)    # an always-searched word: counted from its own floor
        self.assertIsNone(tl.wave([90, 100, 60, 30])["up"])                  # already up when the curve starts

    def test_published_topics_have_curves_and_sources(self):
        topics, trends, problems = tl.load_youtube(ROOT / "timeline" / "youtube_hot.json", ROOT / "timeline" / "youtube_trends.json")
        self.assertEqual([p for p in problems if "left out" in p], [])
        self.assertTrue(topics)
        self.assertTrue(tl.anchor_title(topics))
        for t in topics:
            self.assertEqual(len(t["series"]["values"]), len(next(iter(trends["series"].values()))["values"]), t["id"])
            self.assertTrue(all(s["url"].startswith("https://") for s in t["sources"]), t["id"])


class Page(unittest.TestCase):
    """What `python3 -m radar render` writes for the timeline, from the repository's sample files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(config, "DATA_DIR", root), mock.patch.object(config, "SITE_DIR", root / "site"),
                        mock.patch.object(config, "LOG_DIR", root / "logs"),
                        mock.patch.object(config, "TIMELINE_DIR", ROOT / "timeline")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_page_and_data(self):
        self.assertTrue(render.write_timeline(TODAY))
        page = (config.SITE_DIR / "timeline" / "index.html").read_text(encoding="utf-8")
        for bit in ('id="lane-video"', 'id="lane-ai"', 'id="yt-hot"', 'id="v-today"', 'id="a-today"', 'id="v-past"',
                    "接下来的大热点", "逐月看", "hs-now", 'href="../"', 'href="../data/timeline.json"', "ev-gta6-launch",
                    config.SITE_NAME):
            self.assertIn(bit, page)
        self.assertEqual(page.count('class="hs-wrap"'), 2)
        self.assertLess(page.index('id="lane-video"'), page.index('id="lane-ai"'))
        self.assertNotIn("<img", page)                          # the sample has no pictures: text covers only
        self.assertNotIn("配图", page)
        for word in ("Gary", "深海", "ytdeepsea", "公众号", "/Users/"):
            self.assertNotIn(word, page)
        data = json.loads((config.SITE_DIR / "data" / "timeline.json").read_text(encoding="utf-8"))
        self.assertEqual((data["today"], data["window"]), ("2026-11-04", ["2026-05-01", "2027-05-31"]))
        self.assertEqual(len(data["events"]), 76)
        self.assertTrue(all(e["image"] is None for e in data["events"]))
        yt = json.loads((config.SITE_DIR / "data" / "youtube-history.json").read_text(encoding="utf-8"))
        self.assertEqual(yt["anchor_title"], "KPop 猎魔女团")

    def test_own_picture_is_copied_next_to_the_page(self):
        folder = Path(self.tmp.name) / "tl"
        (folder / "images").mkdir(parents=True)
        (folder / "images" / "launch.jpg").write_bytes(b"\xff\xd8jpeg")
        (folder / "events.json").write_text(json.dumps({"events": [raw(id="launch", image={"file": "launch.jpg"})]}),
                                            encoding="utf-8")
        with mock.patch.object(config, "TIMELINE_DIR", folder):
            self.assertTrue(render.write_timeline(TODAY))
        self.assertEqual((config.SITE_DIR / "timeline" / "img" / "launch.jpg").read_bytes(), b"\xff\xd8jpeg")
        page = (config.SITE_DIR / "timeline" / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="img/launch.jpg"', page)
        self.assertNotIn('id="yt-hot"', page)                   # no YouTube files in this folder
        self.assertFalse((config.SITE_DIR / "data" / "youtube-history.json").exists())

    def test_broken_file_keeps_the_last_page(self):
        self.assertTrue(render.write_timeline(TODAY))
        folder = Path(self.tmp.name) / "tl"
        folder.mkdir()
        (folder / "events.json").write_text("{not json", encoding="utf-8")
        with mock.patch.object(config, "TIMELINE_DIR", folder), mock.patch("sys.stderr"):
            self.assertFalse(render.write_timeline(TODAY))
        self.assertIn("ev-gta6-launch", (config.SITE_DIR / "timeline" / "index.html").read_text(encoding="utf-8"))

    def test_daily_page_links_to_it(self):
        self.assertIn('href="./timeline/"', render.page(_brief(), ["2026-10-23"], at_root=True))
        self.assertIn('href="../timeline/"', render.page(_brief(), ["2026-10-23"]))
        with mock.patch.object(config, "TIMELINE_DIR", Path(self.tmp.name) / "none"):
            self.assertNotIn("timeline/", render.page(_brief(), ["2026-10-23"], at_root=True))
            self.assertFalse(render.write_timeline(TODAY))


if __name__ == "__main__":
    unittest.main()
