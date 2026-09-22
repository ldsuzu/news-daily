# 每日简报 · NewsPipe

一个跑在你自己机器上的新闻聚合与阅读软件。每天把**游戏**和 **AI** 两个领域的信息攒成一份中文日报，
**不挂 VPN 也能读** —— 外网新闻的正文在抓取那一刻就已经落到本地库里了。

设计依据：[`技术框架.md`](技术框架.md)（v0.4）。界面版式采用原型里的**变体 A（报纸版式）**。

## 它是怎么做到"不开 VPN 也能读"的

软件跑在你本机，出口就是你家的网络，翻不过去。所以：

| 通道 | 负责 | 跑在哪 |
|---|---|---|
| 本地直连 | **23 个源**：机核 / 游研社 / 游民星空 / 3DM / 量子位 / 雷峰网 / InfoQ … 以及 GameSpot / Eurogamer / PC Gamer / RPS / Gematsu / VGC / Nintendo Life / OpenAI Blog / The Verge AI / TechCrunch AI / arXiv / Hacker News | 你机器上的 `newspipe fetch` |
| 海外分身 | 只剩 7 个真需要代理的：IGN / Hugging Face / Google Research / Import AI / DeepMind / Anthropic | GitHub Actions（可选） |

> **注意**：最初我以为"外网源都得走代理"，逐个实测后发现有 13 个能直连 ——
> 之前那个结论是被真正被墙的站点拖累的误判。所以**不接 GitHub、不开 VPN，
> 也已经能读到当天的大部分外网新闻**（含正文）。海外分身现在是可选项，不是必需品。

两边抓到的条目在本地 SQLite 合并去重（靠 `url_hash`），正文一并落库 —— 之后读的时候
完全不需要网络。

## 桌面启动（点一下就开）

桌面上有一个**「每日简报」快捷方式**，双击它就会：启动本地服务 → 自动打开浏览器到
<http://127.0.0.1:8787> → 命令窗口保持打开。

**关掉那个命令窗口 = 停止服务。** 服务已经在跑时，双击只会帮你打开浏览器，不会重复启动。

想自己再建一个快捷方式的话：目标指向项目根目录的 `启动简报.cmd`，图标用 `assets/简报.ico`
（图标可以用 `.venv\Scripts\python.exe make_icon.py` 重新生成）。

> 注意 `启动简报.cmd` 是 **GBK 编码 + CRLF 换行**的 —— Windows 的 cmd.exe 按系统代码页读批处理
> 文件，存成 UTF-8 会把中文行首的命令撕碎（`echo` 变成 `cho`），存成 LF 换行会让多行 `if (`
> 块解析失败。改这个文件时留意这两点。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[web,extract]"     # Windows
# source .venv/bin/activate && pip install -e ".[web,extract]"  # macOS / Linux

.venv\Scripts\python.exe -m newspipe doctor        # 看看哪些源可达
.venv\Scripts\python.exe -m newspipe fetch         # 抓取并入库
.venv\Scripts\python.exe -m newspipe process       # 补正文 + 去重聚类
.venv\Scripts\python.exe -m newspipe serve --open  # 打开阅读界面
```

界面：<http://127.0.0.1:8787> —— 今日 / 历史 / 信息源 / 设置，四屏，离线可用。

## 命令

| 命令 | 作用 |
|---|---|
| `newspipe fetch` | 抓本地可达源并入库（`--source <id>` 可只跑某几个） |
| `newspipe collect` | 海外模式：只抓墙外源，产出 `bundles/<date>.json`（给 GitHub Actions 用） |
| `newspipe sync` | 拉取海外分身的 bundle 并合并入库（镜像 → 本地留存 → 最近一次） |
| `newspipe remote` | 海外链路诊断：配置、GitHub 可达性、每层镜像逐层测试 |
| `newspipe process` | 补正文（trafilatura）+ 去重聚类 |
| `newspipe serve` | 本地阅读界面 |
| `newspipe run` | **一条命令跑完整流水线**：sync → fetch → process → digest（定时任务用的就是它） |
| `newspipe digest` | 生成 Markdown 日报到 `archive/` |
| `newspipe schedule` | 注册每日定时任务（`install` / `status` / `uninstall`） |
| `newspipe doctor` | 源健康检查：可达性、耗时、体积 |
| `newspipe stats` | 库内容概览 + 每个源的状态 |
| `newspipe search <词>` | 全历史全文检索（SQLite FTS5） |
| `newspipe dates` | 有内容的日期列表（历史入口） |

`--mode collector` 与 `--mode standalone` 的分工是硬的：**两边零重叠**，所以合并时天然不冲突。

## 会不会漏新闻

**已经入库的永远不会丢** —— 历史永久保留，FTS5 全文可搜，不做滚动过期。

但**没跑的那几天确实会漏**。因为源的 feed 只保留最近几条，实测覆盖天数：

| 覆盖 | 源 |
|---|---|
| 1 天 | 机核、IGN、雷峰报、量子位、VGC |
| 2~4 天 | 游民星空、Gematsu、arXiv、InfoQ、Solidot、PC Gamer、GameSpot |
| 十几~几十天 | Steam、Google Research、DeepMind（更新慢，feed 里时间跨度大） |

也就是说：**电脑关机三天，那三天的机核新闻就永远抓不回来了** —— 源那边已经把它挤出 feed 了。
这不是存储问题，是抓取时机问题。

应对：

- 定时任务开了 **StartWhenAvailable**：错过 07:00 的话，开机后会尽快补跑一次
- 只要每天开一次机，就不会漏
- 连续多天不开机，回来只能抓到当时 feed 里的内容 —— 这部分是真丢了，补不回来

## 翻译与热度排序（要配 API key）

外网条目默认是英文标题 + 英文摘要。开了 LLM 之后每条会多出：

- **中文标题**（原文标题降到下面一行保留 —— 这就是"中文为主、保留原文"）
- **一句话中文摘要**
- **热度分 0-10**，排序改用它，不再只靠关键词匹配
- 界面上给外网条目加「外网」标记

### 开启

```yaml
# config/settings.yaml
llm:
  enabled: true
  api_key: "sk-..."        # 或者设环境变量 NEWSPIPE_LLM_KEY，免得写进文件
```

```powershell
.\.venv\Scripts\python.exe -m newspipe enrich            # 处理还没处理过的新条目
.\.venv\Scripts\python.exe -m newspipe enrich --reset    # 改完提示词后，重跑最近几天的
```

`newspipe run`（定时任务）里也包含这一步，会自动跳过已处理的。

### 花多少钱

用 `deepseek-flash`，**空闲时段**（非工作日 9-12、14-18 点；夜间、周末、节假日都算空闲）
单价是 ¥1/百万 输入、¥4/百万 输出，缓存命中的输入只要 ¥0.02/百万。

按每天 300 条、每条约 200 输入 + 120 输出估算：

| | 每天 | 每月 |
|---|---|---|
| tokens | 约 96k | 约 2.9M |
| **花费** | **约 ¥0.15** | **约 ¥4.5** |

**定时任务跑在早上 7:00，正好是空闲时段**，价格是高峰的一半。

设置页和今日屏页脚会实时显示今日 / 累计花费（换算成人民币，从 `llm_usage` 账本算出）。
`llm.daily_budget_cny` 是硬上限，当天花超了就停手，不会失控。

### 省 token 的四个手段

1. **批量送**：一次 `batch_size`（默认 20）条共享一份系统提示，还能吃上缓存折扣
2. **只给摘要不给全文**：`llm.summary_chars` 控制每条送进去多少字
3. **只处理新条目**：`llm_at` 为空才排队，重跑不重复花钱
4. **单轮上限**：`llm.max_items_per_run`，防止一口气处理几千条历史

### 不想花钱

`llm.enabled: false`（默认值）时其它功能完全不受影响 —— 只是没有翻译和热度分，
排序退回规则分（源权重 + 时效 + 关键词）。

## 接上你自己的海外分身（可选，非必需）

**这一步不是必需的。** 13 个外网源本地直连就能抓到，海外分身只对剩下 7 个源
（IGN / Hugging Face / Google Research / Import AI / DeepMind / Anthropic）有意义 ——
它的价值是"你睡觉的时候也在抓"。

完整步骤（含踩坑点）见 **[GitHub操作指南.md](GitHub操作指南.md)**，三步概览：

1. 在 GitHub 建一个**空仓库**（不要勾 README / .gitignore / license），然后 `git remote add` + `git push`；
2. 仓库 Settings → Actions → General → **Workflow permissions 改成 Read and write**
   —— 漏了这步，workflow 会表面成功、实际什么都不提交；
3. Actions 页手动跑一次 `collect`，然后在 `config/settings.yaml` 填 `bundle.repo`，本地 `newspipe sync`。

之后 `.github/workflows/collect.yml` 会在**北京时间每天 06:30**（UTC 22:30，给调度延迟留缓冲）
自动抓墙外源并提交 bundle —— 你早上开机时，内容已经躺在本地库里了。

## 目录

```
config/           settings.yaml（全部旋钮）· sources.yaml（信息源）· domains.yaml（领域）
src/newspipe/
  collectors/     rss / api / html(列表页) / base(HTTP: UA·限速·代理·浏览器头)
  pipeline/       normalize · extract · dedupe · score · select(精选) · bundle · digest
  storage/        schema.sql(含 FTS5) · db · repo
  web/            app.py(路由) · templates/ · static/
  sync.py         海外 bundle 的四层镜像回退
  cli.py          命令行入口
.github/workflows/collect.yml   海外采集分身
prototype/        界面原型（3 个版式变体，选完即弃）
tests/            23 项测试
data/             news.db · contents/ · bundles/
archive/          每日 Markdown 归档
```

## 几个已经定下来的取舍

- **条数不设硬上限**：分成「精选 / 全部」两层。精选 = **按重要度取每领域前 N 条**（默认 10），
  不是一个硬性的分数门槛；阈值 `picks_min_score` 默认 0（不设下界），需要收紧时再调。
  **没进精选 ≠ 被丢弃**，它照常入库、照常可搜。
- **精选限制单源占比**：`selection.max_per_source`（默认 3）。没有这一条，一家媒体就能把
  10 个名额全占满 —— 国内游戏站只有标题、没有 RSS 摘要，关键词加分天然吃亏，
  会被能提供摘要的源压死。名额没满时会回填，所以它只是"尽量均衡"，不是"宁可少给"。
- **国内游戏站用 HTML 列表页采集**：它们没有 RSS，`type: html` + 几个正则参数就能接一个站。
  注意这类页面给不出发布时间，`date_from_url` 只用于过滤老链接，日期回退为抓取时间。
- **历史永久保留**：`items_fts` 是 FTS5 虚拟表（中文用 trigram 分词），全历史可检索，
  不做滚动过期。
- **正文优先于链接**：正文在抓取时就落库，所以离线可读是默认状态，而不是缓存命中时的运气。
- **一个源失败不影响别人**：错误被收敛成 `ok=False` 记进 `fetch_log`，界面上标红而已。
- **配置文件是唯一事实来源**：界面只读，旋钮全在 `config/*.yaml`。

## 当前进度

- [x] **M0** 骨架：schema（含 FTS5）、采集器、CLI、幂等入库、规则打分、契约测试
- [x] **M1** 可用（代码层）：正文抽取、去重聚类、四屏界面（版式 A）、GitHub Actions 与 sync 回退
      —— 唯一待办是接上你自己的 GitHub 仓库（见上）
- [~] **M2** 好读：Markdown 日报归档 ✅、定时自动化 ✅（`newspipe run` + `schedule install`）；
      LLM 中文摘要与打分待做
- [ ] **M3** 打磨：聚类展示、收藏、关注/屏蔽、源权重自学习、桌面外壳

## 每天自动跑起来

```powershell
.\.venv\Scripts\python.exe -m newspipe schedule install --at 07:00
```

Windows 会在任务计划程序里注册一个每日任务，执行 `newspipe run`（sync → fetch → process → digest），
日志写到 `data/run.log`。跑完你会发现 `archive/2026-09-22-ai.md` 这样的日报已经躺在那儿了。

非 Windows 平台会打印一行 crontab 让你贴。
