# AI 情报雷达（intel-radar）

每天自动扫一遍 B站、YouTube、抖音、TikTok、小红书、X 和 GitHub，再加上 Hugging Face、Hacker News、中文 AI 资讯和 Product Hunt，找出 AI 和 AI 视频圈刚冒头的题材、正在热议的事和值得一用的新工具，生成一页能直接播放视频的日报。

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
python3 -m radar render              # 用已存的数据重新生成页面（改了模板或站名时用）
python3 -m radar run --reuse 6       # 不重新采集，用最近 6 小时存下的内容重新分析
python3 -m radar run --only x        # 只重新采集某几个来源（名字见 radar/pipeline.py 的 COLLECTORS）
python3 -m radar deep                # 对最新一期的所有视频题材往回查起源（默认每天只查新起和升温的）
python3 -m radar status              # 最近几次运行
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
- `tests/`：离线测试，`python3 -m unittest discover -s tests`

改代码前后怎么自查见 [CONTRIBUTING.md](CONTRIBUTING.md)，密钥和数据怎么存见 [SECURITY.md](SECURITY.md)。

## 许可

MIT，见 [LICENSE](LICENSE)。二维码生成用的是 Project Nayuki 的 MIT 库，原样收录在 `radar/qrcodegen.py`，见 [THIRD_PARTY.md](THIRD_PARTY.md)。

作者 Gary（曹淦），做 AI 视频和内容出海。X：[@GaryAIytb](https://x.com/GaryAIytb)，更多开源项目见 [github.com/garycaogan](https://github.com/garycaogan)。
