# AI 情报雷达（intel-radar）

每天自动扫一遍 B站、YouTube、抖音、TikTok、小红书、X 和 GitHub，再加上 Hugging Face、Hacker News、中文 AI 资讯和 Product Hunt，找出 AI 和 AI 视频圈刚冒头的题材、正在热议的事和值得一用的新工具，生成一页能直接播放视频的日报。另带一张热点时间线，把前后各半年 AI 视频圈和 AI 圈的大事排在一起。

**不想自己部署，可以直接看每天早上更新的公开版：https://lab.ytdeepsea.com/radar/**

![每天一期：今天的头条、视频新题材、X 热议、开源与工具](docs/page.jpg)

## 它和热榜有什么不一样

热榜谁都能看，难的是在一个题材刚冒头、还没被做烂的时候发现它。雷达做的是这件事：

- **「新起」要两条独立信号**。一个题材想标「新起」，得同时满足：这个词以前几乎搜不到（B站看之前 53 天和最近 7 天的播放前 20 里各有几条标题带这个词，YouTube 看今年的相关视频里有多少是最近 10 天发的），**并且**有 3 个以上账号在做或者有小号爆了。只有一条信号的标「观察」，以前就有、最近明显变多的标「升温」，一直有人做的标「持续」。每张卡片下面都写着触发了哪几条信号。
- **数字全部是代码算的**。模型只负责把视频归成题材、起名字、写点评；播放、粉丝、几个账号在做、小号单条多少播放、X 上多少账号几小时内在聊，都由程序从公开数据里算，模型改不了。
- **往回查起源**。当天标「新起」「升温」的题材会再往回翻两年：B站按季度画出这个词的作品数，YouTube、TikTok、抖音找最早能查到的那一条，最后写三句话：最早从哪来、怎么传开的、现在到哪一步。
- **视频就地播放**。头条和题材卡片先放作品本身，点封面就在页面里播（B站、YouTube、抖音、TikTok 用各自的官方播放器，X 视频直接播），一次只播一个，不用一个个点出去。
- **每条都能做成分享图**。头条、视频题材、X 热议和 GitHub 新仓库右上角有「分享」，生成一张 1080×1440 的竖图，带标题、一句判断、三个数字、代表作封面和二维码，扫码回到页面上的那一条。

## 降噪规则

- 视频题材：AI 相关度不够的不要；名字去掉「AI、教程、副业、赚钱」之后说不出具体形式的不要（比如「AI 副业赚钱教程」）；官方 MV、真人影视、游戏实况这类看不出是 AI 做的不要。
- X：安全漏洞、公司财报融资、币圈、交易机器人这类泛科技新闻不进页面；公司新闻和观点争论各最多留一个。
- **只有一个账号在说、又查不到来源的收入说法，不进头条。** 这条规则来自一次真实的翻车：某天头条差点放上「AI 虚拟主播 4 小时打赏 3258 刀」，往回一查，是一个国外账号的互动模板，隔几天换个人、换个金额再发一遍，配的视频也是换脸片。
- 开源与工具：安全、金融、运维类仓库在给模型之前就去掉；Hugging Face 的 Space 只收已知机构发的；成人、换脸、赌博类内容在采集时就挡掉。
- 每一期挡掉了多少条记在当天快照里，页面不显示。

## 跑起来

需要 Python 3.9 或更新版本，只用标准库，不用装任何依赖。

```bash
git clone https://github.com/garycaogan/intel-radar.git
cd intel-radar
cp .env.example .env        # 然后编辑 .env，填 TIKHUB_API_KEY 和 LLM_API_KEY
```

先不花钱试一下（只跑免费来源，不调模型，确认环境没问题）：

```bash
python3 -m radar run --only bili_search,bili_popular,bili_hot,github_trending,hn,hf,news --skip-llm
python3 -m radar serve      # 打开 http://127.0.0.1:8792
```

一分多钟就能跑完。这样出来的页面基本是空架子（最多有几条 B站热搜），题材、头条和工具点评都要靠模型来归类和写。配好密钥后跑完整一期：

```bash
python3 -m radar run        # 采集 + 分析 + 生成页面，约 6–10 分钟
python3 -m radar serve
```

其他命令：

```bash
python3 -m radar render              # 用已存的数据重新生成页面（改了模板、站名或时间线数据时用）
python3 -m radar run --reuse 6       # 不重新采集，用最近 6 小时存下的内容重新分析
python3 -m radar run --only x        # 只重新采集某几个来源（名字见 radar/pipeline.py 的 COLLECTORS）
python3 -m radar deep                # 对最新一期的所有视频题材往回查起源（默认每天只查新起和升温的）
python3 -m radar status              # 最近几次运行
python3 -m radar mcp                 # 用 stdio 跑 MCP（serve 也在 /mcp 上答，见下文）
```

### 需要哪些密钥

| 变量 | 用途 | 没有会怎样 |
|---|---|---|
| `TIKHUB_API_KEY` | [TikHub](https://tikhub.io) 的接口：YouTube、抖音、TikTok、小红书、X 的搜索和抖音热榜，按次计费 | 这几个平台跳过，只剩 B站、GitHub、Hugging Face、Hacker News 和资讯 |
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` | 任意 OpenAI 兼容的对话接口，默认 DeepSeek 官方（`https://api.deepseek.com`，`deepseek-v4-pro`） | 没有题材归类、X 话题和头条 |
| `GITHUB_TOKEN`（可选） | GitHub 搜索的限流更宽 | 先试 `gh auth token`，再不行就匿名搜，偶尔会少几个仓库 |

用 DeepSeek 官方接口时，程序会自动关掉思考模式（开着的话长提示词要跑好几分钟）。换成别的服务，只改 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`；如果那个模型默认也会长时间思考，用 `LLM_EXTRA_BODY` 带上关掉它的参数。其余可选项都在 [.env.example](.env.example) 里。

## 每期花多少钱

2026 年 10 月 24 日用这份代码实测跑了一期：扫了 2,255 条公开内容，TikHub 调用 103 次约 0.18 美元，模型调用 4 次（DeepSeek v4 pro，约 5.7 万输入、1 万输出 token）约 0.4 元人民币，两个新起/升温题材往回查起源另花 0.02 美元，总共用时 6 分钟出头。

一期完整运行大约 110 次 TikHub 调用，约 0.2 美元：YouTube 搜索约 20 次（每次 0.002 美元）、YouTube 频道粉丝最多 30 次（0.001）、X 搜索约 40 次（0.001）、抖音搜索 4 次（0.01）、小红书搜索 2 次（0.01）、TikTok 搜索 4 次（0.001）、抖音热榜 1 次。模型调用 4 次，用 DeepSeek v4 pro 大约几毛钱人民币。当天标「新起」「升温」的题材会自动往回查起源，每个题材再花约 0.015 美元，多数日子 2–5 个。

单次运行有硬上限（默认 170 次调用、0.4 美元，用 `TIKHUB_MAX_CALLS` 和 `TIKHUB_MAX_USD` 改），到了就停止调用，不会因为某个接口出错反复重试把钱花光。每期的实际花费按接口记在数据库的运行记录和日志里（`python3 -m radar status`），不会出现在页面上。

## 每天自动跑

用 cron 每天早上跑一次。日期按北京时间算，`--if-missing` 保证当天已经出过就跳过，所以可以设两个时间点，第一次失败了第二次补：

```cron
20 6,7 * * * cd /path/to/intel-radar && /usr/bin/python3 -m radar run --if-missing >> ~/.local/share/intel-radar/logs/run.log 2>&1
```

cron 的时间是服务器本地时间，服务器不在东八区的话自己换算一下。

## 公开给别人看

生成的页面在数据目录的 `site/` 里（默认 `~/.local/share/intel-radar/site/`），是纯静态文件，页面里的链接都是相对路径。把这个目录同步到任意静态托管（Cloudflare Pages、GitHub Pages、Nginx、对象存储）就能公开，不需要后端。

- 公开前把 `.env` 里的 `RADAR_SHARE_URL` 改成你的公开地址，再 `python3 -m radar render` 一次，分享卡片上的二维码才会指向你的站。
- 页面和 `data/<日期>.json` 里都没有花费、调用次数、失败来源和运行用时，但数据库和日志里有，所以只同步 `site/`，不要把整个数据目录放出去。
- 页面默认带 `noindex`，不想被搜索引擎收录就不用管；想被收录，改 `radar/render.py` 里的 robots 标签。
- `python3 -m radar serve` 只适合本机预览，公开请放在 Nginx、Caddy 或静态托管后面。

## 热点时间线

日报页面顶部有「热点时间线」的入口（生成在 `site/timeline/`）。它以生成页面那天为中心，往前往后各看半年，AI 视频线和 AI 线分开放：AI 视频线是视频生成模型和工具的大版本，加上做 AI 视频的人会追着二创的影视、游戏大作；AI 线是模型发布和开源、大公司的 AI 产品、大会和影响大的政策。每条线从上到下是接下来最近的几个大热点（带倒计时）、按月分卡的时间轴（左右滑，打开时对准今天这个月）、逐月列表（过去半年折成一条，点开才展开，中间一条线标着今天）。两条线下面是 YouTube 历史热点。每次 `run` 和 `render` 都会按当天日期重新生成，所以每天自动跑的话，时间窗也每天往后挪。还没跑过日报时也可以先 `python3 -m radar render`，它会提示没有日报，但时间线照样生成。

事件都在 [timeline/events.json](timeline/events.json)，一条一个，每个字段的意思写在文件开头的「说明」里。仓库里带的是一份示例：2026 年 5 月到 2027 年 5 月的 76 件 AI 和 AI 视频大事，都是公开报道，每件附来源，2026 年 11 月 4 日核实过。换成你自己的，就照着格式改或者加：

- 日期写到能确定的精度：`2026-11-19`、`2026-11`（只知道月份）或 `2027-Q1`（只知道季度），不知道就往粗里写。
- 状态按实际写：`done` 已发生、`scheduled` 官方定了日子、`expected` 有可靠报道但没定日子、`unconfirmed` 只有单一来源。定了档的日子过了还写着 `scheduled`，页面上会标「日期已过，待确认」，看到了就去核实，改成 `done` 或者改日期。
- 每件至少一个 https 来源，`checked` 写核实那天。时间窗外的事件留在文件里不碍事，不会显示；`parked` 里放核实过但还不想上页面的，要上就整条挪到 `events` 里。
- 配图可选：把图放进 `timeline/images/`，事件里写 `"image": {"file": "xxx.jpg", "from": "图的出处页"}`，生成时会复制到 `site/timeline/img/`，鼠标停在图上显示出处。只放你有权使用的图。示例数据不带图，全部用文字封面。
- 格式不对的事件会跳过，并在终端里用 `timeline:` 开头报出来，不影响其他事件；整个文件不是合法的 JSON 时，会报出第几行，并保留上一次生成的页面。`tests/test_timeline.py` 会检查仓库里的这份文件每一条都过得了。

数据用 JSON 而不是 YAML，是为了继续只用标准库。想把自己的事件放在仓库外面，把 `RADAR_TIMELINE_DIR` 指到另一个目录，里面放同名的几个文件。

YouTube 历史热点是过去一年多 YouTube 上最大的几波热点在 YouTube 里被搜了多少、火了多久。条目在 [timeline/youtube_hot.json](timeline/youtube_hot.json)，曲线在 `timeline/youtube_trends.json`，按 `keyword` 对上。曲线来自 Google Trends 的 YouTube 搜索类目（全球，按周），每个词自己的曲线以最高点为 100，另外和锚点（默认「kpop demon hunters」）一起查一次，算出高峰是锚点的几倍。卡片上的四个数由曲线算：高峰那周、冒头到见顶几周（从高峰前的最低点往上走了五分之一算冒头）、热度过半的周数、现在剩高峰的几成。

Google Trends 没有接口，刷新曲线用 [timeline/youtube_trends.js](timeline/youtube_trends.js)，在你自己的浏览器里跑：

1. 先打开一次 Google Trends 首页 `https://trends.google.com/trends/?hl=en`。这一步不能省，它会写入 Trends 要的 cookie，不然下一步的查询会直接回 429。
2. 再打开任意一个 explore 页，按 F12 打开开发者工具，把脚本整段贴进控制台，然后运行 `await refresh(["kpop demon hunters", "squid game", "ghibli"])`。数组里放 `youtube_hot.json` 里所有的 `keyword`，拼写一字不差，锚点也要在里面。每个词要查两次，中间隔 3 秒，十几个词大约要一两分钟。
3. 跑完结果已经复制到剪贴板，整个覆盖 `timeline/youtube_trends.json`，再 `python3 -m radar render`。

搜索热度看的是有多少人在 YouTube 里搜这个词，播放量要另看。「67」「world cup」这类泛词会把别的意思的搜索也算进去，曲线会偏高、拖得偏长，挑关键词时尽量用作品或梗的全名。加一条新热点：先把它的关键词放进上面的数组刷新一次曲线，再照着已有的格式在 `youtube_hot.json` 里抄一条。

给 Agent 读的数据在 `site/data/timeline.json`（时间窗里的事件）和 `site/data/youtube-history.json`（每波热点的那几个数，不带原始曲线），MCP 也有对应的两个工具，见下一节。

## 接到你的 Agent（MCP）

雷达自带一个只读的 MCP，Claude Code、Codex、Cursor 这类 Agent 接上以后，可以直接问「今天有什么新起的 AI 视频题材」「蔬菜家族这个玩法最早什么时候出现的」「接下来三个月 AI 视频圈有哪些大热点」，它会去查你自己跑出来的那几期，带着页面链接回答。不想自己部署，可以直接接公开版：`https://lab.ytdeepsea.com/mcp`，用法见 [lab.ytdeepsea.com/radar/agent/](https://lab.ytdeepsea.com/radar/agent/)。

9 个工具：`radar_today`（某一期的概览和头条）、`radar_video_topics`（视频新题材，可按标签、平台筛）、`radar_x_topics`（X 热议）、`radar_tools`（开源项目、模型和资讯）、`radar_topic_origin`（题材溯源）、`radar_search`（在最近几期里搜关键词）、`radar_list_issues`（往期列表）、`radar_timeline`（热点时间线，可按线 `lane`、接下来还是已经发生 `when`、月份 `month`、关键词 `q` 筛）、`radar_youtube_history`（YouTube 历史热点，可用 `topic` 只看一个）。只读数据库里已经存下的日报和溯源，以及 `timeline/` 里的时间线数据，不联网、不花钱，也不会带出运行花费。

两种接法：

```bash
# 1) HTTP：python3 -m radar serve 本来就在 /mcp 上答 MCP
python3 -m radar serve
claude mcp add --transport http intel-radar http://127.0.0.1:8792/mcp
codex mcp add intel-radar --url http://127.0.0.1:8792/mcp

# 2) stdio：给只支持「启动本地命令」的客户端
claude mcp add intel-radar -- sh -c 'cd /path/to/intel-radar && python3 -m radar mcp'
```

回答里的链接默认指向 `http://127.0.0.1:8792`，页面公开以后把 `RADAR_PUBLIC_URL` 设成你的公开地址。`serve` 是给本机用的简单服务，要把 MCP 开给别人，请放在 Nginx、Caddy 这类反向代理后面，并自己加上限流。

## 改成你自己的

- **站名和口吻**：`RADAR_SITE_NAME` 是站名；`RADAR_SITE_BY` 是出品方（页面上显示「xx · 每日」「xx出品」，头条的读者会写成「xx社群里的 AI 视频创作者」）；`RADAR_EDITOR` 是点评用谁的口吻写。后两个可以留空。
- **赛道**：所有关键词、搜索式、平台分区和预算都在 [radar/config.py](radar/config.py)。想盯别的领域（比如 AI 编程、跨境电商），改 B站和 YouTube 的关键词、X 的搜索式和 `AI_TERMS`，再改 `radar/analyze.py` 里提示词对读者的描述。先改采集和过滤，再改提示词，数字永远留给代码算。

## 目录

- `radar/sources/`：各平台采集器（bilibili、youtube、shortvideo（抖音/TikTok/小红书）、xtw（X）、github、feeds（资讯 RSS）、misc（Hugging Face、Hacker News、抖音热榜））
- `radar/score.py`：每小时播放、播放÷粉丝、涨星速度、来源内百分位热度
- `radar/analyze.py`：题材和话题归类、历史探测、标签规则、头条点评的提示词
- `radar/deep.py`：题材往回查起源
- `radar/pipeline.py`：一次完整运行
- `radar/render.py`：静态页面，分享弹层和画卡片的脚本也在这里
- `radar/share.py`：分享卡片的内容、数字、二维码和封面缓存
- `radar/config.py`：设置、关键词、搜索式、预算
- `radar/timeline.py`：热点时间线的数据校验、日期、时间窗和页面片段（整页和样式在 `radar/render.py`）
- `timeline/`：热点时间线的事件、YouTube 历史热点、Trends 曲线和刷新曲线的脚本（示例数据，改成你自己的）
- `tests/`：离线测试，`python3 -m unittest discover -s tests`

改代码前后怎么自查见 [CONTRIBUTING.md](CONTRIBUTING.md)，密钥和数据怎么存见 [SECURITY.md](SECURITY.md)。

## 许可

MIT，见 [LICENSE](LICENSE)。二维码生成用的是 Project Nayuki 的 MIT 库，原样收录在 `radar/qrcodegen.py`，见 [THIRD_PARTY.md](THIRD_PARTY.md)。

作者 Gary（曹淦），做 AI 视频和内容出海。X：[@GaryAIytb](https://x.com/GaryAIytb)，更多开源项目见 [github.com/garycaogan](https://github.com/garycaogan)。
