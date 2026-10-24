"""From a day's items to sections: video topics, X topics, repos, tools/models, platform hot words.

The model groups and names things and writes the comments. Every number shown, and every
新起 / 升温 / 持续 label, is computed here from collected data and probes, and each label carries
the list of signals that fired (``basis``) so a pick can be checked afterwards.
"""

from __future__ import annotations

import re
import statistics
import time
from datetime import datetime, timedelta, timezone

from . import config, db, llm
from .score import fmt_num
from .sources import bilibili, xtw, youtube
from .sources.common import blocked, in_lane, log, off_lane

BJ = timezone(timedelta(hours=8))


def _bj(ts: float | None, fmt: str = "%m-%d") -> str:
    return datetime.fromtimestamp(ts, BJ).strftime(fmt) if ts else "?"


def _age(h) -> str:
    if h is None:
        return "?"
    return f"{int(h)}小时" if h < 48 else f"{int(h // 24)}天"


# ------------------------------------------------------------------ video

VIDEO_PROMPT = """你是 AI 视频赛道的情报编辑。下面是最近 7 天 B 站、YouTube、抖音、TikTok、小红书上和 AI 相关的热门视频。请把它们按「题材 / 玩法」归类，找出值得 AI 视频创作者注意的东西。

「题材」是观众买账的那个内容形式，粒度要细到别人能照着做，例如「印地语蔬菜家族 3D 动画」「AI 美食滑梯解压」「用 Claude 做 2D 动画教程」「AI 丧尸短剧连载」「DeepSeek 鲸鱼娘拟人」。同一个梗、同一种画面形式、同一套剧情结构、同一个新工具的同一种用法，归为一个题材；不同的就拆开。

禁止的大筐名字：「AI 工具教程」「AI 视频教程」「AI Agent 讨论」「AI 副业赚钱」「AI 科普」「AI 资讯」这类说不出具体形式的；名字里也不要用括号或斜杠列举几种类型（比如「AI 短剧（悬疑/恐怖/科幻）」），那是好几个题材，要拆开，或者只留证据最强的那一种。教程类必须写清用什么工具做出什么效果（比如「用 Seedance 做产品广告」）；只是在聊 AI 新闻、观点、测评的视频不属于这里，直接不放进任何题材。

之前几天识别过的题材（同一个题材请在 same_as 里写它的编号，比如 "T12"；不是同一个就写 null）：
{past}

视频（编号 | 平台 | 发布 | 播放 | 每小时播放 | 点赞 | 粉丝 | 播放÷粉丝 | 时长 | 账号 | 标题 | 搜索词；抖音和小红书不公开播放量，看点赞）：
{lines}

只输出一个 JSON 对象（不要 Markdown 代码块）：
{{"topics": [{{
  "name": "题材名，14 个字以内，说清楚是什么形式",
  "kind": "内容题材 | 制作玩法 | 平台活动 | 其他",
  "videos": [属于这个题材的视频编号],
  "summary": "观众在看什么、这个形式的看点是什么，两句话以内，只根据标题和数据",
  "bili_term": "在 B 站搜这个题材用的词：2 到 6 个字，必须原样出现在这个题材至少一条 B 站视频的标题里，选最能代表这个题材的那个词（比如「大肥鱼」「蔬菜家族」「第一人称」），不要用「AI视频」「AI」这种泛词；这个题材没有 B 站视频就写空字符串",
  "youtube_term": "在 YouTube 搜这个题材用的英文或原语言搜索词，2 到 5 个词；不适合就写空字符串",
  "ai_relevance": 0 到 100 的整数,
  "same_as": "T 编号或 null"
}}]}}

规则：
- 同一个题材在不同平台都出现时归在一起，这是很强的信号。
- 一个题材通常至少 2 条视频；只有 1 条的，必须是播放÷粉丝很高（小号爆了）才单独列。最多列 16 个，按值得关注的程度从高到低。
- ai_relevance：100 = 用 AI 做出来的内容或 AI 工具本身；60 = 和 AI 强相关的讨论；30 以下 = 只是标题里带了 AI，和 AI 视频创作关系不大。和 AI 无关的视频直接不放进任何题材。
- 同名不等于同一个东西，看标题判断。官方 MV、真人影视、游戏官方内容、动漫剧情解说、游戏实况这类看不出是 AI 做的，不要放进任何题材。
- 不要写任何数字，不要编造标题里看不出的事实。中文输出。"""


PLATFORM = {"bili": "B站", "youtube": "YouTube", "douyin": "抖音", "tiktok": "TikTok", "xhs": "小红书"}


def platform(v: dict) -> str:
    if v["source"] == "youtube" and v["extra"].get("short"):
        return "YouTube Shorts"
    return PLATFORM.get(v["source"], v["source"])


def _video_line(i: int, v: dict) -> str:
    s, m = v["signals"], v["metrics"]
    dur = v["extra"].get("duration")
    dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else "?"
    return " | ".join(str(x) for x in (
        i, platform(v).replace("YouTube Shorts", "YT短").replace("YouTube", "YT"), _bj(v.get("published_at")),
        fmt_num(m.get("views")), fmt_num(s.get("vph")), fmt_num(m.get("likes")), fmt_num(m.get("followers")),
        s.get("vsr") if s.get("vsr") is not None else "?", dur_s, (v.get("author") or "")[:16],
        (v.get("title") or "").replace("\n", " ")[:80], (v.get("query") or "").split(":", 1)[-1]))


def _past_lines(past: list[dict]) -> str:
    if not past:
        return "（还没有历史，这是第一次运行）"
    return "\n".join(f"T{p['thread_id']} | {p['name']} | 首次 {p['first_day']} | 最近 {p['day']}" for p in past[:60])


def _resolve_same(same, past: list[dict]):
    if not same or not isinstance(same, str):
        return None
    try:
        tid = int(same.strip().lstrip("Tt"))
    except ValueError:
        return None
    return next((p for p in past if p["thread_id"] == tid), None)


def _bili_probe(con, term: str, now: float) -> dict | None:
    key = f"bili2:{term}:{_bj(now, '%Y-%m-%d')}"
    cached = db.probe_get(con, key)
    if cached is not None:
        return cached
    try:
        recent = bilibili.window_count(term, now - config.RECENT_DAYS * 86400, now)
        time.sleep(0.6)
        before = bilibili.window_count(term, now - config.BASELINE_DAYS * 86400, now - config.RECENT_DAYS * 86400)
    except Exception as exc:     # a failed probe leaves the topic unlabelled, never guessed
        log(f"bili probe {term}: {exc}")
        return None
    days_before = config.BASELINE_DAYS - config.RECENT_DAYS
    out = {"term": term, "recent_n": recent["n"], "before_n": before["n"],
           "recent_match": recent["match"], "before_match": before["match"],
           "recent_page": recent["page"], "before_page": before["page"],
           "recent_per_day": round(recent["n"] / config.RECENT_DAYS, 2),
           "before_per_day": round(before["n"] / days_before, 2),
           "capped": recent["n"] >= 1000 and before["n"] >= 1000,
           "recent_top": recent["top"], "before_top": before["top"], "before_samples": before["samples"]}
    out["ratio"] = round((out["recent_per_day"] + 0.2) / (out["before_per_day"] + 0.2), 2)
    db.probe_put(con, key, out)
    return out


def _yt_probe(con, term: str, now: float) -> dict | None:
    key = f"youtube3:{term}:{_bj(now, '%Y-%m-%d')}"
    cached = db.probe_get(con, key)
    if cached is not None:
        return cached
    try:
        out = youtube.window_count(term)
    except Exception as exc:
        log(f"youtube probe {term}: {exc}")
        return None
    out["term"] = term
    db.probe_put(con, key, out)
    return out


def _label_video(t: dict, memory_hit: dict | None, history_days: int) -> tuple[str, list[str]]:
    """Label from independent signals; 新起 needs at least two of them."""
    basis, strong = [], set()
    pb, py = t.get("probe_bili"), t.get("probe_youtube")
    if pb:
        rm, bm = pb.get("recent_match", 0), pb.get("before_match", 0)
        bp = pb.get("before_page") or 20
        title_line = (f"B站搜「{pb['term']}」：之前 53 天播放最高的 {bp} 条里 {bm} 条标题带这个词，"
                      f"近 7 天是 {rm} 条")
        if not pb["capped"] and pb["before_n"] <= 15 and pb["recent_n"] >= 5:
            basis.append(f"B站搜「{pb['term']}」：近 7 天 {pb['recent_n']} 条，之前 53 天只有 {pb['before_n']} 条")
            strong.add("term_new")
        elif rm >= 8 and bm <= 2:
            basis.append(title_line + "，以前几乎没有")
            strong.add("term_new")
        elif pb["before_n"] < 1000 and pb["ratio"] >= 3:   # a capped "before" count would inflate the ratio
            basis.append(f"B站搜「{pb['term']}」：近 7 天日均 {pb['recent_per_day']} 条，是之前的 {pb['ratio']} 倍")
            strong.add("burst")
        elif rm >= 2 * bm + 4:
            basis.append(title_line)
            strong.add("burst")
        elif bm >= 8 or pb["capped"]:
            basis.append(title_line + "，以前就一直有")
            strong.add("evergreen")
        elif pb["recent_n"] < 1000 and pb["ratio"] < 0.6:
            basis.append(f"B站搜「{pb['term']}」：近 7 天日均条数只有之前的 {pb['ratio']} 倍")
            strong.add("cooling")
        else:
            basis.append(title_line)
    if py and py.get("match", 0) >= 3:
        mr, mt = py["match_recent"], py["match"]
        line = f"YouTube 搜「{py['term']}」：今年的相关视频里 {mt} 条标题对得上，其中 {mr} 条是最近 10 天发的"
        if mr / mt >= 0.6:
            basis.append(line + "，以前几乎没有")
            strong.add("term_new")
        elif mr / mt >= 0.3:
            basis.append(line)
            strong.add("burst")
        else:
            basis.append(line + f"，最早的是 {py.get('oldest_match_days')} 天前")
            strong.add("evergreen_yt")
    elif py:
        basis.append(f"YouTube 搜「{py['term']}」：今年的相关视频里标题对得上的不到 3 条，看不出新旧")
    m = t["metrics"]
    if m["authors"] >= 3:
        basis.append(f"{m['authors']} 个不同账号在做")
        strong.add("breadth")
    if (m.get("max_vsr") or 0) >= 5:
        o = m.get("outlier") or {}
        if o.get("followers") is not None:
            basis.append(f"有小号爆了：{fmt_num(o['followers'])} 粉的号单条 {fmt_num(o['views'])} 播放")
        else:
            basis.append(f"有小号爆了：播放是粉丝的 {m['max_vsr']} 倍")
        strong.add("outlier")
    if memory_hit:
        basis.append(f"雷达 {memory_hit['first_day']} 起就在记录")
    elif history_days >= 3:
        basis.append("雷达之前没记录过")
        strong.add("unseen")

    support = {"breadth", "outlier"} & strong
    # 新起 needs evidence the format barely existed before: the search probe, or (once the radar has
    # history) a burst the radar has never recorded. Breadth and a small-account hit only support it.
    if "term_new" in strong and support:
        return "新起", basis
    if {"unseen", "burst"} <= strong and support:
        return "新起", basis
    if "burst" in strong and support:
        return "升温", basis
    if "cooling" in strong and memory_hit:
        return "回落", basis
    if "evergreen" in strong or "evergreen_yt" in strong or memory_hit:
        return "持续", basis
    if support:
        return "观察", basis
    return "观察", basis


def _topic_metrics(members: list[dict]) -> dict:
    views = [v["metrics"].get("views") or 0 for v in members]
    vph = [v["signals"].get("vph") or 0 for v in members]
    vsr = [v["signals"].get("vsr") for v in members if v["signals"].get("vsr") is not None]
    best = max((v for v in members if v["signals"].get("vsr") is not None), key=lambda v: v["signals"]["vsr"], default=None)
    pubs = [v["published_at"] for v in members if v.get("published_at")]
    return {
        "videos": len(members), "authors": len({(v["source"], v.get("author_id") or v.get("author")) for v in members}),
        "platforms": sorted({PLATFORM.get(v["source"], v["source"]) for v in members}),
        "views_total": sum(views), "views_max": max(views or [0]), "vph_max": max(vph or [0]),
        # 抖音 / 小红书 hide play counts; their likes are reported separately, never mixed into plays
        "likes_total": sum(v["metrics"].get("likes") or 0 for v in members if v["metrics"].get("views") is None),
        "max_vsr": max(vsr) if vsr else None,
        "outlier": {"views": best["metrics"].get("views"), "followers": best["metrics"].get("followers"),
                    "title": best["title"]} if best else None,
        "earliest": min(pubs) if pubs else None,
        "median_age_h": round(statistics.median(v["signals"]["age_h"] for v in members if v["signals"].get("age_h") is not None), 1)
        if any(v["signals"].get("age_h") is not None for v in members) else None,
    }


def _sample(v: dict) -> dict:
    return {"title": v["title"], "url": v["url"], "thumb": v.get("thumb") or "", "author": v.get("author") or "",
            "platform": platform(v), "duration": v["extra"].get("duration"), "vertical": bool(v["extra"].get("vertical")),
            "views": v["metrics"].get("views"), "likes": v["metrics"].get("likes"), "followers": v["metrics"].get("followers"),
            "vsr": v["signals"].get("vsr"), "vph": v["signals"].get("vph"), "age_h": v["signals"].get("age_h"),
            "published": _bj(v.get("published_at"), "%m-%d %H:%M")}


# A topic name made only of these says no concrete format ("AI副业赚钱教程"), so it can't be copied.
_GENERIC = re.compile(r"AI|ai|视频|教程|副业|赚钱|变现|工具|科普|资讯|讨论|合集|分享|推荐|测评|评测|盘点|技巧|方法|攻略|入门|实战|\s")


def generic_name(name: str) -> bool:
    return len(_GENERIC.sub("", name or "")) < 2


POOL_SIZES = {"bili": 70, "youtube": 50, "douyin": 30, "tiktok": 25, "xhs": 20}   # candidates per platform for the model


def video_topics(con, day: str, videos: list[dict], report: dict, history_days: int) -> list[dict]:
    now = time.time()
    cand = [v for v in videos if v["signals"].get("age_h") is not None and v["signals"]["age_h"] <= 8 * 24
            and in_lane(" ".join(str(x) for x in (v.get("title"), v.get("text"), v["extra"].get("tag"), v.get("author"))))
            and not blocked(v.get("title"))]
    pool = []
    for src, n in POOL_SIZES.items():
        pool += sorted((v for v in cand if v["source"] == src), key=lambda v: -v["signals"]["heat"])[:n]
    if not pool:
        return []
    past = db.recent_topics(con, "video", day)
    lines = "\n".join(_video_line(i, v) for i, v in enumerate(pool))
    out = llm.ask_json(VIDEO_PROMPT.format(past=_past_lines(past), lines=lines), max_tokens=10000)
    topics = []
    dropped = report.setdefault("dropped", {})
    for raw in (out.get("topics") or [])[:16]:
        members = [pool[i] for i in raw.get("videos") or [] if isinstance(i, int) and 0 <= i < len(pool)]
        if not members or (raw.get("ai_relevance") or 0) < 55:
            continue
        if generic_name(raw.get("name")) or (raw.get("kind") == "其他" and (raw.get("ai_relevance") or 0) < 80):
            dropped["说不出具体形式的视频题材"] = dropped.get("说不出具体形式的视频题材", 0) + 1
            continue
        if len(members) == 1:   # a single video stands alone only as a clear small-account hit
            v = members[0]
            if (v["signals"].get("vsr") or 0) < 20 or (v["metrics"].get("views") or 0) < 200_000:
                continue
        members.sort(key=lambda v: -(v["metrics"].get("views") or 0))
        t = {"name": (raw.get("name") or "").strip()[:30], "kind": raw.get("kind") or "其他",
             "summary": raw.get("summary") or "", "ai_relevance": raw.get("ai_relevance"),
             "bili_term": (raw.get("bili_term") or "").strip(), "youtube_term": (raw.get("youtube_term") or "").strip(),
             "metrics": _topic_metrics(members), "samples": [_sample(v) for v in members[:6]],
             "member_ids": [v["id"] for v in members], "same_as": raw.get("same_as")}
        t["weight"] = round(sum(v["signals"]["heat"] for v in members[:5]) / 5 + 6 * min(t["metrics"]["authors"], 5), 1)
        topics.append(t)
    topics.sort(key=lambda t: -t["weight"])

    yt_budget = 6
    for t in topics[:12]:
        bili_titles = [s["title"] for s in t["samples"] if s["platform"] == "B站"]
        if t["bili_term"] and bili_titles and not any(bilibili._norm(t["bili_term"]) in bilibili._norm(x) for x in bili_titles):
            t["bili_term_note"] = "搜索词不在这些视频的标题里，跳过 B站探测"
            t["bili_term"] = ""
        if t["bili_term"]:
            t["probe_bili"] = _bili_probe(con, t["bili_term"], now)
            time.sleep(0.5)
        # overseas formats (YouTube, TikTok) get the YouTube novelty probe
        yt_share = sum(1 for s in t["samples"] if s["platform"].startswith(("YouTube", "TikTok"))) / max(len(t["samples"]), 1)
        if t["youtube_term"] and yt_share >= 0.5 and yt_budget > 0:
            t["probe_youtube"] = _yt_probe(con, t["youtube_term"], now)
            yt_budget -= 1
    kept = []
    for t in topics:
        hit = _resolve_same(t.pop("same_as", None), past)
        t["label"], t["basis"] = _label_video(t, hit, history_days)
        if not t["basis"]:
            continue
        t["first_day"] = hit["first_day"] if hit else day
        t["thread_id"] = db.save_topic(con, day, "video", t["name"], [t["bili_term"], t["youtube_term"]], t["label"],
                                       hit["thread_id"] if hit else None, t["first_day"],
                                       {"metrics": t["metrics"], "basis": t["basis"]})
        t["days_seen"] = len(db.thread_days(con, t["thread_id"]))
        kept.append(t)
    report["video_topics"] = len(kept)
    return kept


# ------------------------------------------------------------------ X

X_PROMPT = """你是 AI 圈的情报编辑，读者是做 AI 视频和 AI 产品的中文创作者。下面是最近两天 X 上互动最高的 AI 相关推文（中英文都有）。请把它们归成「话题」，挑出值得这群人知道的。

话题可以是：一件事（某个大博主入驻 X、某个模型发布、某个产品出事）、一种玩法或教程（AI 人偶穿搭带货、火柴人 Skill）、一个项目或赚钱机会（某个工具月入多少、某个出海打法）、一个开源仓库、一个观点争论。同一件事的不同推文归为一个话题。

之前几天识别过的话题（同一件事请在 same_as 写它的编号，比如 "T5"，否则写 null）：
{past}

推文（编号 | 语言 | 作者 @账号 粉丝 | 浏览 | 赞 | 收藏 | 发布 | 正文）：
{lines}

只输出一个 JSON 对象（不要 Markdown 代码块）：
{{"topics": [{{
  "name": "话题名，16 个字以内",
  "kind": "热点事件 | 玩法教程 | 项目机会 | 开源仓库 | 工具模型 | 观点讨论",
  "tweets": [推文编号],
  "summary": "发生了什么、为什么大家在聊，两三句话，只根据推文内容",
  "entity": "最能代表这件事的专有名词，用来在 X 上搜讨论量，比如 影视飓风、Seedance 2.5、Grok Bot；没有专有名词就写空字符串",
  "lane": "AI视频与创作 | 工具与模型 | 项目与变现 | 创作者生态 | 行业新闻 | 泛科技",
  "value": 0 到 100 的整数,
  "same_as": "T 编号或 null"
}}]}}

规则：
- value：100 = AI 视频创作者或 AI 产品人今天必须知道；60 = 有参考价值；30 以下 = 泛科技、币圈、政治八卦，和这群人关系不大。value 低于 40 的不要列。
- lane：AI视频与创作 = 用 AI 做视频、图片、音乐、短剧的作品和方法；工具与模型 = 创作者能用上的新模型、新工具、新功能；项目与变现 = 具体的赚钱打法和收入数据；创作者生态 = 博主入驻、平台规则和流量变化；行业新闻 = 公司财务、融资、人事、诉讼、安全漏洞、政策；泛科技 = 和 AI 创作关系不大的科技话题。安全漏洞、公司亏损和上市、交易机器人、币圈都算行业新闻或泛科技，value 不超过 45。
- 创作者生态的大事算高价值，即使单条推文互动不高：头部博主或大号入驻新平台、出海、换赛道，平台流量和收益规则变化，某种内容形式在平台上突然起量。几个不同账号在聊同一件这种事时，value 至少给 70。
- 色情、成人内容、灰产引流的推文不要列。
- 最多列 18 个话题，按 value 从高到低。只有一条推文的话题也可以列，前提是它本身足够重要。
- 营销号的夸张标题（"卧槽""炸裂""吓人"）要看实际内容判断，不要被语气带着走。
- 不要写任何数字，不要编造推文里没有的事实。中文输出。"""


def _x_line(i: int, t: dict) -> str:
    m = t["metrics"]
    text = (t.get("text") or "").replace("\n", " ")[:110]
    return " | ".join(str(x) for x in (
        i, t["extra"].get("lang") or "?", f"{(t.get('author') or '')[:12]} @{t.get('author_id')} {fmt_num(m.get('followers'))}",
        fmt_num(m.get("views")), fmt_num(m.get("likes")), fmt_num(m.get("bookmarks")), _bj(t.get("published_at"), "%m-%d %H:%M"), text))


_MONEY = re.compile(r"\d[\d.,]*\s*[万千]?\s*(刀|美元|美金|元|块|USD)|\$\s*\d|月入|日入|收入|营收|打赏|MRR|ARR", re.I)


def single_source_claim(t: dict) -> bool:
    """A money claim ("4 小时打赏 3258 刀") that one account makes and nobody else corroborates. One such
    claim traced back to an engagement template re-posted with the person and amount swapped, so it never
    leads the page."""
    return t.get("lane") == "项目与变现" and t["metrics"]["authors"] <= 1 \
        and bool(_MONEY.search(f"{t.get('name')} {t.get('summary')}"))


def _x_label(t: dict, hit: dict | None, history_days: int) -> tuple[str, list[str]]:
    basis = []
    b = t.get("breadth")
    m = t["metrics"]
    basis.append(f"{m['tweets']} 条高互动推文，{m['authors']} 个账号，合计浏览 {fmt_num(m['views_total'])}")
    if single_source_claim(t):
        basis.append("收入数字只有这一个账号在说，没有可查的账号、链接或直播间，按未核实处理，不进头条")
    hot = burst = False
    if b:
        if b["span_hours"] is not None and b["tweets"] >= 15:
            basis.append(f"X 上搜「{b['term']}」最新推文：{b['tweets']} 条集中在 {b['span_hours']} 小时内，{b['authors']} 个账号")
            hot = b["authors"] >= 10
            burst = hot and b["authors"] >= 12 and b["span_hours"] <= 16
        elif b["tweets"]:
            basis.append(f"X 上搜「{b['term']}」最新推文：36 小时内 {b['tweets']} 条，{b['authors']} 个账号")
    if hit:
        basis.append(f"雷达 {hit['first_day']} 起就在记录")
    elif history_days >= 3:
        basis.append("雷达之前没记录过")
    big = m["views_total"] >= 1_000_000 or m["likes_total"] >= 10_000
    if burst and not hit:
        return "爆发", basis
    if hot or big or m["authors"] >= 3:
        return "热议", basis
    return "关注", basis


X_NEWS_CAP = 1          # company / finance / policy news slots in the X section
X_MAX_TOPICS = 14


def x_topics(con, day: str, tweets: list[dict], report: dict, history_days: int) -> list[dict]:
    cand = sorted((t for t in tweets if t["signals"].get("age_h") is not None and t["signals"]["age_h"] <= 60),
                  key=lambda t: -t["signals"]["heat"])
    # The whole recent pool goes to the model (text trimmed): news that many accounts mention with modest
    # engagement each (a creator joining X) would be cut by any heat threshold.
    pool = cand[:320]
    if not pool:
        return []
    past = db.recent_topics(con, "x", day)
    lines = "\n".join(_x_line(i, t) for i, t in enumerate(pool))
    out = llm.ask_json(X_PROMPT.format(past=_past_lines(past), lines=lines), max_tokens=12000)
    topics = []
    dropped = report.setdefault("dropped", {})
    per_lane: dict = {}
    for raw in sorted(out.get("topics") or [], key=lambda r: -(r.get("value") or 0))[:18]:
        members = [pool[i] for i in raw.get("tweets") or [] if isinstance(i, int) and 0 <= i < len(pool)]
        if not members or (raw.get("value") or 0) < 40:
            continue
        lane = raw.get("lane") or "行业新闻"
        if off_lane(f"{raw.get('name')} {raw.get('summary')}") and lane not in ("AI视频与创作", "创作者生态"):
            lane = "泛科技"
        per_lane[lane] = per_lane.get(lane, 0) + 1
        if lane == "泛科技" or (lane == "行业新闻" and per_lane[lane] > X_NEWS_CAP) \
                or (raw.get("kind") == "观点讨论" and sum(t["kind"] == "观点讨论" for t in topics) >= 1):
            dropped["X 上的泛科技和行业新闻"] = dropped.get("X 上的泛科技和行业新闻", 0) + 1
            continue
        members.sort(key=lambda t: -(t["metrics"].get("views") or 0))
        t = {"name": (raw.get("name") or "").strip()[:30], "kind": raw.get("kind") or "观点讨论", "lane": lane,
             "summary": raw.get("summary") or "", "entity": (raw.get("entity") or "").strip(), "value": raw.get("value"),
             "metrics": {"tweets": len(members), "authors": len({m["author_id"] for m in members}),
                         "views_total": sum(m["metrics"].get("views") or 0 for m in members),
                         "likes_total": sum(m["metrics"].get("likes") or 0 for m in members)},
             "samples": [{"title": m["text"][:220], "url": m["url"], "thumb": m.get("thumb") or "",
                          "author": m.get("author"), "handle": m.get("author_id"), "followers": m["metrics"].get("followers"),
                          "views": m["metrics"].get("views"), "likes": m["metrics"].get("likes"),
                          "bookmarks": m["metrics"].get("bookmarks"), "published": _bj(m.get("published_at"), "%m-%d %H:%M"),
                          "lang": m["extra"].get("lang"), "links": m["extra"].get("links") or [],
                          "media": m["extra"].get("media") or {}} for m in members[:5]],
             "member_ids": [m["id"] for m in members], "same_as": raw.get("same_as")}
        topics.append(t)
    topics = topics[:X_MAX_TOPICS]
    for t in topics:
        if not t["entity"]:
            continue
        key = f"x:{t['entity']}:{day}"
        b = db.probe_get(con, key)
        if b is None:
            try:
                b = xtw.mention_count(t["entity"])
                b["term"] = t["entity"]
                db.probe_put(con, key, b)
            except Exception as exc:
                log(f"x breadth {t['entity']}: {exc}")
        t["breadth"] = b
    for t in topics:
        hit = _resolve_same(t.pop("same_as", None), past)
        t["label"], t["basis"] = _x_label(t, hit, history_days)
        t["first_day"] = hit["first_day"] if hit else day
        t["thread_id"] = db.save_topic(con, day, "x", t["name"], [t["entity"]], t["label"],
                                       hit["thread_id"] if hit else None, t["first_day"],
                                       {"metrics": t["metrics"], "basis": t["basis"]})
        t["days_seen"] = len(db.thread_days(con, t["thread_id"]))
    report["x_topics"] = len(topics)
    return topics


# ------------------------------------------------------------------ repos, models, stories

TOOLS_PROMPT = """你是 AI 工具情报编辑，读者是做 AI 视频、AI 内容和小产品的中文创作者（会用 Claude Code、Codex 这类工具，不一定是专业程序员）。下面是今天 GitHub 上涨星快的新仓库、Hugging Face 热门模型、Hacker News 上的 AI 讨论，以及中文 AI 资讯和 Product Hunt 新产品。

请给每条写一句中文说明，并判断对这群人有没有用。

GitHub 仓库（编号 | 仓库 | 星数 | 日均涨星 | 今日涨星 | 创建 | 语言 | 简介）：
{repos}

Hugging Face（编号 | 类型 | 名称 | 点赞 | 下载 | 创建 | 标签）：
{models}

Hacker News（编号 | 标题 | 分数 | 评论 | 链接）：
{stories}

资讯与新产品（编号 | 来源 | 标题 | 摘要）：
{news}

只输出一个 JSON 对象（不要 Markdown 代码块）：
{{"items": [{{
  "ref": "R3 / M2 / H5 / N1 这样的编号",
  "zh": "这是什么，一句话，30 个字以内，说人话（资讯就写发生了什么）",
  "use": "对 AI 视频创作者 / 内容创作者 / 小产品开发者有什么用，一句话；没用就写空字符串",
  "group": "AI 视频与图像 | 音频与配音 | 内容创作与运营 | Agent 与编程 | 模型 | 其他",
  "value": 0 到 100 的整数
}}]}}

规则：
- 每个编号都要给结果。和 AI 无关的（普通游戏、系统工具、灰产）value 给 0。
- 这群人关心：能做视频、图片、配音、音乐的新模型和工具；能帮他们写脚本、剪辑、运营账号、批量出片的工具；Claude Code / Codex 的好用技能和 Agent 工具；新的赚钱打法。
- 安全审计、漏洞扫描、渗透测试、数据库、编译器、运维监控、金融交易、区块链这类只对专业工程师或别的行业有用的，value 不超过 25。公司财报、融资、人事、诉讼类资讯 value 不超过 30。
- Hugging Face Spaces 里明显是刷榜、转载别人模型、赌博、色情的，value 给 0。
- 不要编造简介里看不出的功能。中文输出。"""

TOOL_CAPS = {"repos": 10, "models": 8, "stories": 6, "news": 8}
GROUP_CAPS = {"Agent 与编程": 5}      # coding/agent repos are plentiful; keep room for video/audio tools


_VARIANT = re.compile(r"[-_.](sft|base|instruct|comfy|diffusers|fp8|fp4|nvfp4|gguf|awq|gptq|turbo|preview|demo|\d+-?steps?.*)$", re.I)


def model_family(name: str) -> str:
    """'OpenMOSS-Team/MOSS-Image-SFT' and 'Comfy-Org/MOSS-Image' are one model for the reader."""
    base = name.split("/")[-1].lower()
    while _VARIANT.search(base):
        base = _VARIANT.sub("", base)
    return base


def _hf_keep(h: dict) -> bool:
    """Creator pipelines from anyone; other models and every Space only from known labs."""
    owner = (h.get("author") or "").lower()
    if h["kind"] == "space":
        return owner in config.HF_ORGS
    return h["extra"].get("pipeline") in config.HF_CREATOR_PIPELINES or owner in config.HF_ORGS


def tools(repos: list[dict], hf: list[dict], hn: list[dict], report: dict, news: list[dict] | None = None) -> dict:
    news = news or []
    dropped = report.setdefault("dropped", {})
    n_repos = len(repos)
    repos = [r for r in repos if not blocked(r["title"] + " " + (r.get("text") or ""))
             and not off_lane(r["title"] + " " + (r.get("text") or "") + " " + " ".join(r["extra"].get("topics") or []))]
    if n_repos - len(repos):
        dropped["安全、金融、运维类仓库"] = n_repos - len(repos)
    hf_all = [h for h in hf if not blocked(h["title"] + " " + " ".join(h["extra"].get("tags") or []))]
    hf = [h for h in hf_all if _hf_keep(h)]
    if len(hf_all) - len(hf):
        dropped["Hugging Face 个人转载和刷榜项目"] = len(hf_all) - len(hf)
    repos = sorted(repos, key=lambda r: -r["signals"]["heat"])[:28]
    hf = sorted(hf, key=lambda r: r["metrics"].get("rank") or 99)
    model_ids = {h["title"].lower() for h in hf if h["kind"] == "model"}
    hf = [h for h in hf if h["kind"] == "model"][:14] + \
         [h for h in hf if h["kind"] == "space" and h["title"].lower() not in model_ids][:6]
    hn = sorted(hn, key=lambda h: -(h["metrics"].get("points") or 0))[:14]
    news = [n for n in news if not off_lane(n["title"])][:40]
    if not (repos or hf or hn or news):
        return {"repos": [], "models": [], "stories": [], "news": []}
    rl = "\n".join(" | ".join(str(x) for x in (
        f"R{i}", r["title"], fmt_num(r["metrics"].get("stars")), r["signals"].get("stars_per_day") or "?",
        r["metrics"].get("stars_today") or "?", _bj(r.get("published_at"), "%Y-%m-%d"), r["extra"].get("language") or "?",
        (r.get("text") or "")[:160].replace("\n", " "))) for i, r in enumerate(repos))
    ml = "\n".join(" | ".join(str(x) for x in (
        f"M{i}", h["kind"], h["title"], h["metrics"].get("likes"), fmt_num(h["metrics"].get("downloads")),
        _bj(h.get("published_at"), "%Y-%m-%d"), ",".join(h["extra"].get("tags") or [])[:80] or h.get("text"))) for i, h in enumerate(hf))
    sl = "\n".join(" | ".join(str(x) for x in (
        f"H{i}", h["title"], h["metrics"].get("points"), h["metrics"].get("comments"), h["url"][:80])) for i, h in enumerate(hn))
    nl = "\n".join(" | ".join(str(x) for x in (
        f"N{i}", n["extra"].get("source_name") or n["source"], n["title"][:80], (n.get("text") or "")[:120].replace("\n", " ")))
        for i, n in enumerate(news))
    out = llm.ask_json(TOOLS_PROMPT.format(repos=rl or "（无）", models=ml or "（无）", stories=sl or "（无）",
                                           news=nl or "（无）"), max_tokens=9000)
    notes = {str(x.get("ref")).upper(): x for x in out.get("items") or []}

    def pack(prefix, rows, fn, cap, floor=45):
        res, names = [], set()
        for i, r in enumerate(rows):
            n = notes.get(f"{prefix}{i}") or {}
            key = model_family(r["title"]) if prefix == "M" else r["title"].lower()
            if (n.get("value") or 0) < floor or n.get("group") == "其他" or key in names:
                continue
            names.add(key)
            res.append({**fn(r), "zh": n.get("zh") or "", "use": n.get("use") or "", "group": n.get("group") or "其他",
                        "value": n.get("value")})
        res.sort(key=lambda x: -(x["value"] or 0))
        kept, per = [], {}
        for x in res:
            per[x["group"]] = per.get(x["group"], 0) + 1
            if per[x["group"]] <= GROUP_CAPS.get(x["group"], cap):
                kept.append(x)
        if len(res) - len(kept[:cap]):
            dropped["价值低或同类太多的工具条目"] = dropped.get("价值低或同类太多的工具条目", 0) + len(res) - len(kept[:cap])
        return kept[:cap]

    result = {
        "repos": pack("R", repos, lambda r: {
            "name": r["title"], "url": r["url"], "desc": r.get("text") or "", "stars": r["metrics"].get("stars"),
            "stars_today": r["metrics"].get("stars_today"), "stars_per_day": r["signals"].get("stars_per_day"),
            "stars_gain_per_day": r["signals"].get("stars_gain_per_day"), "created": _bj(r.get("published_at"), "%Y-%m-%d"),
            "language": r["extra"].get("language"), "from_trending": "github:trending" in (r.get("query") or "")},
            TOOL_CAPS["repos"], 60),
        "models": pack("M", hf, lambda h: {
            "name": h["title"], "url": h["url"], "kind": h["kind"], "likes": h["metrics"].get("likes"),
            "downloads": h["metrics"].get("downloads"), "created": _bj(h.get("published_at"), "%Y-%m-%d"),
            "tags": h["extra"].get("tags") or []}, TOOL_CAPS["models"]),
        "stories": pack("H", hn, lambda h: {
            "name": h["title"], "url": h["url"], "hn_url": h["extra"].get("hn_url"), "points": h["metrics"].get("points"),
            "comments": h["metrics"].get("comments"), "published": _bj(h.get("published_at"), "%m-%d %H:%M")},
            TOOL_CAPS["stories"], 50),
        "news": pack("N", news, lambda n: {
            "name": n["title"], "url": n["url"], "source_name": n["extra"].get("source_name") or n["source"],
            "votes": n["metrics"].get("votes"), "published": _bj(n.get("published_at"), "%m-%d %H:%M")},
            TOOL_CAPS["news"], 50),
    }
    report["repos"] = len(result["repos"])
    return result


def hot_words(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        if it["kind"] == "hotword" and (it["extra"].get("in_lane") or in_lane(it["title"])):
            out.append({"word": it["title"], "platform": "B站热搜" if it["source"] == "bili_hot" else "抖音热榜",
                        "rank": it["metrics"].get("rank"), "url": it["url"]})
    return sorted(out, key=lambda w: (w["platform"], w["rank"] or 99))


# ------------------------------------------------------------------ headline

HEAD_PROMPT = """{role}今天的「AI 情报日报」头条。读者是{readers}AI 视频创作者（做 YouTube、B 站、抖音、视频号，用 AI 批量出片）和想找新项目、新选题、新工具的人。

下面是系统今天整理出来的候选，数字和「新起 / 升温 / 持续」标签都是系统按数据算的，你不能改数字，也不能编造候选里没有的事实。

视频题材（编号 V）：
{videos}

X 话题（编号 X）：
{xs}

开源仓库、工具和资讯（编号 R / M / H / N）：
{tools}

从中挑 6 到 8 条今天最值得看的，写成头条。页面会把头条对应的视频直接放出来，所以有视频作品的题材优先。标「新起」「升温」「爆发」的要优先考虑，它们是数据确认的冒头信号。挑选标准按重要性：
1. 突然冒头、之前没有的题材或玩法，有多个账号在做或小号爆了的证据；
2. 创作者能复刻、AI 能明显降低成本、能做成系列的；
3. 对出海、变现、新项目有直接启发的大事（比如大博主入驻某个平台、某个工具改变了生产方式）；
4. 真正有用的新工具或开源仓库。
纯热闹、和这群人无关的不要选，公司财务、安全漏洞、融资上市这类行业新闻不进头条。收入、打赏、月入这类数字只有一个账号在说、又没有可查的账号或链接的，不进头条。一条头条只讲一件事；只有同一件事在视频和 X 都出现时才合成一条，不要把几件不相干的事拼成一条。
标题和点评的说法要和标签一致：标「持续」「观察」「关注」的不能写成“爆发”“突然”“新出现”；S 级只给标「新起」「升温」「爆发」的题材或真正的行业大事。
标题和点评里不要出现 V1、X6、R3 这种编号，也不要写“播放粉丝比多少倍”这种说法，要说小号就直接说“几百粉的号单条多少万播放”。

读者在手机上扫一眼就要懂，所以每条分开写：verdict 是判断短句（"有手法，可以学一下""值得马上测""先观察""上过了"）；why 一句话说清为什么值得看（观众买账的点、证据），不超过 40 个字；next_step 一个能马上做的小测试，不超过 40 个字；risk 最大的坑，不超过 30 个字；comment 再用两三句大白话补充能怎么改（换 IP、换语种、换场景、换平台形式、用 AI 降成本），它默认折叠。用{voice}，不要用"不是因为…而是因为…""真正值得学的是…"这类句式，不要冒号清单，不要"赋能""抓手"这种词。

只输出一个 JSON 对象（不要 Markdown 代码块）：
{{"summary": "今天整体在发生什么，两句话，像同事跟老板口头汇报",
  "headline": [{{
    "refs": ["V2", "X5"],
    "title": "头条标题，20 个字以内，说清楚是什么事",
    "verdict": "一句话判断，12 个字以内",
    "why": "为什么值得看，一句话，40 个字以内",
    "next_step": "一个具体可执行的小测试或动作，40 个字以内",
    "risk": "最大的风险或坑，30 个字以内；没有就写空字符串",
    "comment": "补充点评，两三句话",
    "grade": "S | A | B | C",
    "audience": "创作者 | 做产品的人 | 都适合"
  }}]}}

等级：S = 少见、正在起势、证据强、值得马上测；A = 强推荐，做一次小测试；B = 有参考价值；C = 先放观察。中文输出。"""


def _video_brief(i, t):
    m = t["metrics"]
    top = "; ".join(f"《{s['title'][:40]}》{s['platform']} "
                    + (f"{fmt_num(s['views'])}播放" if s.get('views') is not None else f"{fmt_num(s.get('likes'))}赞")
                    + f" 粉丝{fmt_num(s['followers'])} {_age(s['age_h'])}前"
                    for s in t["samples"][:3])
    return (f"V{i} | {t['name']} | {t['kind']} | 标签 {t['label']} | {m['videos']} 条视频 {m['authors']} 个账号 "
            f"{'/'.join(m['platforms'])} 合计 {fmt_num(m['views_total'])} 播放 | 依据：{'；'.join(t['basis'])} | "
            f"概述：{t['summary']} | 代表作：{top}")


def _x_brief(i, t):
    top = "; ".join(f"@{s['handle']}({fmt_num(s['followers'])}粉) 浏览{fmt_num(s['views'])} 赞{fmt_num(s['likes'])}：{s['title'][:90]}"
                    for s in t["samples"][:2])
    return (f"X{i} | {t['name']} | {t['kind']} | 标签 {t['label']} | 依据：{'；'.join(t['basis'])} | "
            f"概述：{t['summary']} | 代表推文：{top}")


def head_voice(org: str | None = None, editor: str | None = None) -> dict:
    """Who writes the headlines and for whom, from RADAR_SITE_BY / RADAR_EDITOR (either may be empty)."""
    org = config.SITE_BY if org is None else org
    editor = config.EDITOR_NAME if editor is None else editor
    role = (f"你是{org}的情报主编，" if org else "你是一份每日 AI 情报的主编，") + (f"替{editor}写" if editor else "写")
    return {"role": role, "readers": f"{org}社群里的 " if org else "",
            "voice": f"{editor}的说话方式" if editor else "大白话，像同事之间聊天"}


def headline(videos: list[dict], xs: list[dict], tl: dict, news: list[dict] | None = None) -> dict:
    vl = "\n".join(_video_brief(i, t) for i, t in enumerate(videos)) or "（无）"
    xl = "\n".join(_x_brief(i, t) for i, t in enumerate(xs)) or "（无）"
    tlines = []
    for i, r in enumerate(tl.get("repos") or []):
        tlines.append(f"R{i} | {r['name']} | {r['zh']} | 星 {fmt_num(r['stars'])} 今日 +{r['stars_today'] or '?'} 日均 {r['stars_per_day'] or '?'} | 用处：{r['use']}")
    for i, r in enumerate(tl.get("models") or []):
        tlines.append(f"M{i} | {r['name']} | {r['zh']} | 点赞 {r['likes']} | 用处：{r['use']}")
    for i, r in enumerate(tl.get("stories") or []):
        tlines.append(f"H{i} | {r['name']} | {r['zh']} | HN {r['points']} 分 | 用处：{r['use']}")
    for i, r in enumerate(news or []):
        tlines.append(f"N{i} | {r['source_name']} | {r['name']} | {r['zh']} | 用处：{r['use']}")
    out = llm.ask_json(HEAD_PROMPT.format(videos=vl, xs=xl, tools="\n".join(tlines) or "（无）", **head_voice()),
                       max_tokens=8000)
    items = []
    for h in out.get("headline") or []:
        refs = [str(r).upper() for r in h.get("refs") or []]
        if refs and all(r[:1] == "X" for r in refs) and all(
                single_source_claim(xs[int(r[1:])]) for r in refs if r[1:].isdigit() and int(r[1:]) < len(xs)):
            continue          # an uncorroborated money claim can be listed under X, never as a headline
        links = []
        for r in refs:
            try:
                idx = int(r[1:])
            except ValueError:
                continue
            pools = {"V": videos, "X": xs, "R": tl.get("repos") or [], "M": tl.get("models") or [], "H": tl.get("stories") or [],
                     "N": news or []}
            pool = pools.get(r[0]) or []
            if 0 <= idx < len(pool):
                obj = pool[idx]
                if r[0] in "VX":
                    links.append({"section": "video" if r[0] == "V" else "x", "name": obj["name"], "label": obj["label"],
                                  "thread_id": obj.get("thread_id")})
                else:
                    links.append({"section": "news" if r[0] == "N" else "tools", "name": obj["name"], "url": obj["url"]})
        item = {**{k: h.get(k) or "" for k in ("title", "verdict", "why", "comment", "next_step", "risk", "grade", "audience")},
                "refs": refs, "links": links}
        labels = {l.get("label") for l in links if l.get("label")}
        rising = {"新起", "升温", "爆发", "热议"}
        if item["grade"].strip().upper().startswith("S") and labels and not labels & rising:
            item["grade"] = "A"     # S needs a topic the data says is new or rising
        items.append(item)
    return {"summary": out.get("summary") or "", "headline": items}
