"""Settings, keyword sets and source budgets.

Keys, the model endpoint and the site's name come from environment variables. A ``.env`` file in the
repository root (``KEY=VALUE`` lines, see ``.env.example``) is read first, so a cron job needs no shell
profile; variables already set in the environment win.

Keyword lists are the lane definition: what counts as "AI / AI 视频" here.
Edit them freely; every collector reads from this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """``KEY=VALUE`` lines into os.environ; blank lines and ``#`` comments skipped, quotes stripped,
    variables that are already set are left alone."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(Path(os.environ.get("RADAR_ENV_FILE") or ROOT / ".env"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


DATA_DIR = Path(_env("RADAR_DATA_DIR") or HOME / ".local/share/intel-radar").expanduser()
DB_PATH = DATA_DIR / "radar.db"
SITE_DIR = DATA_DIR / "site"
LOG_DIR = DATA_DIR / "logs"

# TikHub (https://tikhub.io): YouTube, 抖音, TikTok, 小红书 and X search, billed per call.
TIKHUB_API_KEY = _env("TIKHUB_API_KEY")
TIKHUB_BASE_URL = _env("TIKHUB_BASE_URL", "https://api.tikhub.io")
# Any OpenAI-compatible chat completions endpoint (DeepSeek, Qwen, Kimi, GLM, a gateway, a local server…).
# LLM_EXTRA_BODY is merged into every request. DeepSeek's API thinks by default and long prompts then run
# for minutes, so against api.deepseek.com thinking is switched off unless LLM_EXTRA_BODY says otherwise;
# other endpoints get nothing extra (some reject fields they don't know).
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_API_KEY = _env("LLM_API_KEY")
LLM_MAIN = _env("LLM_MODEL", "deepseek-v4-pro")
LLM_EXTRA_BODY = json.loads(_env("LLM_EXTRA_BODY") or
                            ('{"thinking": {"type": "disabled"}}' if "api.deepseek.com" in LLM_BASE_URL else "{}"))
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "360"))
GH_TOKEN_ENV = "GITHUB_TOKEN"   # optional; without it the gh CLI's token is tried, then anonymous search

# What readers see. RADAR_SITE_BY is the publisher ("<SITE_BY> · 每日", "<SITE_BY>出品"), RADAR_EDITOR whose
# voice the headline comments are written in; both may stay empty.
SITE_NAME = _env("RADAR_SITE_NAME", "AI 情报雷达")
SITE_BY = _env("RADAR_SITE_BY")
EDITOR_NAME = _env("RADAR_EDITOR")
# Where a share card's QR code sends readers: the public address of this page, with {day} and {item}
# (head-1, video-<thread_id>, x-<thread_id>, repo-1). The page understands the hash after any path, so the
# local default works for testing; set your public address before sharing cards.
SHARE_URL = _env("RADAR_SHARE_URL", "http://127.0.0.1:8792/#intel-radar?day={day}&item={item}")

SERVE_HOST = _env("RADAR_HOST", "127.0.0.1")
SERVE_PORT = int(_env("RADAR_PORT", "8792"))

# Cost estimates for the run report (not billing truth). Prices from TikHub's free
# /api/v1/tikhub/user/get_all_endpoints_info, checked 2026-10-22; anything unlisted counts at the default.
TIKHUB_USD_PER_CALL = 0.001
TIKHUB_PRICES = {
    "/api/v1/youtube/web_v2/get_general_search_v2": 0.002,
    "/api/v1/youtube/web/get_channel_info": 0.001,
    "/api/v1/twitter/web/fetch_search_timeline": 0.001,
    "/api/v1/douyin/web/fetch_hot_search_result": 0.001,
    "/api/v1/douyin/search/fetch_video_search_v3": 0.01,
    "/api/v1/tiktok/app/v3/fetch_video_search_result": 0.001,
    "/api/v1/xiaohongshu/app_v2/search_notes": 0.01,
}
TIKHUB_MAX_CALLS = int(_env("TIKHUB_MAX_CALLS", "170"))       # hard cap per run (a normal run uses about 110)
TIKHUB_MAX_USD = float(_env("TIKHUB_MAX_USD", "0.40"))        # and a spend cap (a normal run is about $0.2)
# Model price per million tokens in CNY, "input,output", for the run report only.
LLM_PRICE_CNY_PER_M = tuple(float(x) for x in _env("LLM_PRICE_CNY_PER_M", "4,16").split(",")[:2])

# ------------------------------------------------------------------ lanes

# B站: guest search API, free. Searched with a 7-day pubtime window, order=click.
# Content forms first (these are where 题材 show up); talk-only keywords like 大模型 / AI副业 / AI工具
# never produced a topic.
BILI_KEYWORDS_DEEP = ["AI短片", "AI漫剧", "AI视频", "AI动画", "AI短剧", "AI MV"]   # two result pages each
BILI_KEYWORDS = BILI_KEYWORDS_DEEP + [
    "AI动漫", "AI特效", "AI萌宠", "AI解压", "AI翻唱", "AI古风", "AI历史", "AI恐怖", "AI故事", "AI怀旧",
    "AI电影", "AI广告", "AI鬼畜", "Seedance", "可灵", "即梦", "Sora", "Veo", "Vidu", "海螺AI",
    "AI数字人", "DeepSeek", "Claude Code", "Codex", "ComfyUI",
]

# B站 partition rankings (free) with the 2025 partition ids (the old 188/36/181 lists are frozen in 2025-04):
# 科技数码, 知识, 影视, 动画, 鬼畜, 音乐. Kept only when the title names an AI tool or says AI outright.
BILI_RANK_RIDS = [1012, 1010, 1001, 1005, 1007, 1003]

# YouTube via TikHub (billed). order_by=this_week.
YT_KEYWORDS = [
    "AI video", "AI animation", "AI short film", "AI cartoon story",
    "AI music video", "AI generated", "Seedance", "Kling AI", "Veo",
    "faceless channel AI", "make money with AI", "Claude Code", "AI agent", "vibe coding",
]

# Extra YouTube pass limited to videos under 4 minutes (where Shorts surface when sorted by views).
YT_SHORT_KEYWORDS = ["AI video", "AI animation", "AI cartoon story", "AI short film", "AI music video", "Seedance"]

# X via TikHub search (billed). {since} is filled with the date two days back.
X_QUERIES = [
    ("zh_ai", "(AI OR 大模型 OR 智能体 OR Agent OR AGI) lang:zh min_faves:300 since:{since}"),
    ("zh_video", "(AI视频 OR AI动画 OR AI短剧 OR AI漫剧 OR Seedance OR 可灵 OR 即梦 OR Sora OR Veo) lang:zh min_faves:80 since:{since}"),
    ("zh_models", "(Claude OR GPT OR Gemini OR DeepSeek OR Codex OR Cursor OR Grok) lang:zh min_faves:300 since:{since}"),
    ("zh_oss", "(开源 OR GitHub OR Skill OR 仓库) lang:zh min_faves:150 since:{since}"),
    ("zh_money", "(赚钱 OR 变现 OR 月入 OR 副业 OR 出海 OR 收益) AI lang:zh min_faves:150 since:{since}"),
    ("zh_creators", "(出海 OR 入驻 OR 博主 OR 创作者 OR 油管 OR YouTube) lang:zh min_faves:40 since:{since}"),
    ("zh_join", "(入驻 OR 第一条 OR 首条 OR 来X OR 来推特 OR 开推) lang:zh min_faves:20 since:{since}"),
    ("zh_crossover", "(B站 OR 抖音 OR 小红书 OR UP主 OR 百万粉) (X OR 推特 OR 出海) lang:zh min_faves:30 since:{since}"),
    ("en_video", "(\"AI video\" OR Sora OR Veo OR Kling OR Seedance OR Runway OR Higgsfield) min_faves:800 since:{since}"),
    ("en_oss", "(\"open source\" OR github OR \"open-source\") (AI OR LLM OR agent) min_faves:800 since:{since}"),
    ("en_labs", "(OpenAI OR Anthropic OR Claude OR Gemini OR DeepSeek OR xAI) min_faves:3000 since:{since}"),
    ("en_money", "(MRR OR ARR OR \"per month\" OR revenue) (AI OR app OR SaaS) min_faves:500 since:{since}"),
]

# Chinese AI-video creators on X, seeded in October 2026 from the accounts whose posts were mostly AI-video work
# (Seedance / 即梦 / 可灵 …), adult-promo accounts removed. Re-derive it from your own runs now and then.
X_ZH_CREATORS = ["johnAGI168", "yaohui12138", "eternityspring", "zmdsb259414", "Pluvio9yte", "FromSummerF", "PixelAigc",
                 "AndyL5cc", "aiwarts", "Saccc_c", "Super_Nius", "rngzhwn199456", "wanerfu", "AI_jacksaku", "isnail"]
X_QUERIES.append(("zh_creators_ai", "(" + " OR ".join("from:" + a for a in X_ZH_CREATORS) + ") since:{since}"))

X_PAGES = 2   # pages per query (20 tweets each)

# 抖音 via TikHub's v3 video search (POST, $0.01 a call, up to 1000 results by heat index, last 7 days).
# label 1 = 低粉爆款 (small accounts that blew up). No play counts on 抖音; likes and followers are there.
DOUYIN_QUERIES = [("AI短剧", 0), ("AI动画", 0), ("AI漫剧", 0), ("AI视频", 1)]
DOUYIN_PER_QUERY = 80

# TikTok via TikHub app search ($0.001), most liked in the last week; "ai video" alone returns almost nothing.
TIKTOK_KEYWORDS = ["AI animation", "AI short film", "AI story", "AI cartoon"]

# 小红书 video notes via TikHub ($0.01), most popular in the last week. No follower counts.
XHS_KEYWORDS = ["AI视频", "AI短剧"]

# Free news feeds (RSS/Atom). 机器之心 is left out: its feeds return an anti-scraping page.
NEWS_FEEDS = [
    ("aihot", "AI HOT", "https://aihot.news/feed.xml"),
    ("qbitai", "量子位", "https://www.qbitai.com/feed"),
    ("producthunt", "Product Hunt", "https://www.producthunt.com/feed?category=video"),
    ("producthunt", "Product Hunt", "https://www.producthunt.com/feed?category=artificial-intelligence"),
]
NEWS_HOURS = 48

# GitHub search (free with gh token). {d} is the creation cutoff.
GH_QUERIES = [
    "created:>{d} stars:>150",
    "created:>{d} stars:>40 video in:name,description,topics",
    "created:>{d} stars:>60 agent in:name,description,topics",
    "created:>{d} stars:>60 skill in:name,description,topics",
    "created:>{d} stars:>60 mcp in:name,description,topics",
]

# Hugging Face: models with these pipelines matter to creators whoever made them; other models (LLMs etc.)
# and every Space only when published by a known lab, which keeps out re-uploads, SEO "humanizer" Spaces and spam.
HF_CREATOR_PIPELINES = {
    "text-to-image", "image-to-image", "image-to-video", "text-to-video", "video-to-video", "text-to-speech",
    "text-to-audio", "audio-to-audio", "any-to-any", "image-to-3d", "text-to-3d", "automatic-speech-recognition",
}
HF_ORGS = {
    "black-forest-labs", "qwen", "openmoss-team", "microsoft", "fishaudio", "lightricks", "tencent", "tencent-hunyuan",
    "bytedance", "bytedance-seed", "bytedance-research", "deepseek-ai", "google", "meta-llama", "facebook",
    "meta-models", "mistralai", "stabilityai", "wan-ai", "alibaba-pai", "thudm", "zai-org", "minimaxai", "moonshotai",
    "nvidia", "apple", "openai", "kwai-kolors", "kwaivgi", "comfy-org", "ibm-granite", "liquidai", "baidu", "xai-org",
    "nari-labs", "sesame", "hexgrad", "resembleai", "kyutai", "lightonai", "stepfun-ai", "hunyuanvideo-community",
    "multimodalart", "huggingface", "hf-audio", "allenai", "salesforce", "runwayml", "genmo", "rhymes-ai",
}

# Off-lane for this audience (AI-video creators): security, finance and infra stories.
# X topics and repos matching these are dropped before they can take a slot.
OFF_LANE_ZH = r"漏洞|隐私泄露|数据泄露|泄露事件|安全审计|安全事件|供应链攻击|网络攻击|黑客|亏损|IPO|上市|融资|估值|裁员|诉讼|股价|财报|营收|币圈|加密货币|比特币|交易机器人|量化交易|自动化交易|炒股|股票|理财|政治|选举|关税"
OFF_LANE_EN = (r"security|vulnerab|pentest|exploit|malware|\bcve\b|red.?team|php-src|kernel|firmware|crypto|blockchain|"
               r"\btrading\b|wallet|defi|\bsoc\b|siem|forensic|compliance")

# Words that mark an item as inside the AI lane (used to filter general hot lists).
AI_TERMS = [
    "ai", "aigc", "人工智能", "大模型", "模型", "智能体", "agent", "gpt", "chatgpt", "openai", "claude",
    "anthropic", "gemini", "deepseek", "grok", "llm", "sora", "veo", "seedance", "可灵", "即梦",
    "豆包", "kimi", "通义", "千问", "qwen", "codex", "cursor", "机器人", "算力", "芯片", "数字人",
    "生成", "midjourney", "comfyui", "runway", "vidu", "海螺", "minimax", "智谱", "glm",
]

# Stricter than AI_TERMS: words that only appear when a video is about or made with AI
# (AI_TERMS also has 模型 / 生成 / 机器人, which hit film and game titles in general rankings).
STRONG_AI_TERMS = [
    "ai", "aigc", "人工智能", "大模型", "gpt", "chatgpt", "openai", "claude", "gemini", "deepseek", "sora", "veo",
    "seedance", "可灵", "kling", "即梦", "豆包", "kimi", "通义", "千问", "midjourney", "comfyui", "runway", "vidu",
    "海螺", "数字人", "智能体", "codex", "cursor",
]

# The daily run digs into these topics only (origin dates + more hits, radar.deep); about $0.015 TikHub a topic.
# Rising topics only, to keep the cost down. Empty = no deep dive in the daily run.
DEEP_DAILY_LABELS = ("新起", "升温")

# How far back counts as "now" vs "before" for novelty.
RECENT_DAYS = 7
BASELINE_DAYS = 60


def ensure_dirs() -> None:
    for d in (DATA_DIR, SITE_DIR, LOG_DIR, SITE_DIR / "d", SITE_DIR / "data"):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
