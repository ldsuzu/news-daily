# 每日简报 · NewsPipe

一个跑在你自己机器上的新闻聚合与阅读软件。每天把**游戏**和 **AI** 两个领域的信息攒成一份中文日报，
**不挂 VPN 也能读** —— 外网新闻的正文在抓取那一刻就已经落到本地库里了。

设计依据：[`技术框架.md`](技术框架.md)（v0.4）。界面版式采用原型里的**变体 A（报纸版式）**。

## 它是怎么做到"不开 VPN 也能读"的

软件跑在你本机，出口就是你家的网络，翻不过去。所以：

| 通道 | 负责 | 跑在哪 |
|---|---|---|
| 本地直连 | 机核 / 游研社 / 量子位 / Steam 公告… | 你机器上的 `newspipe fetch` |
| 海外分身 | IGN / PC Gamer / arXiv / OpenAI / HN… | GitHub Actions 定时任务，产出 bundle JSON |

两边抓到的条目在本地 SQLite 合并去重（靠 `url_hash`），正文一并落库 —— 之后读的时候
完全不需要网络。

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
| `newspipe doctor` | 源健康检查：可达性、耗时、体积 |
| `newspipe stats` | 库内容概览 + 每个源的状态 |
| `newspipe search <词>` | 全历史全文检索（SQLite FTS5） |
| `newspipe dates` | 有内容的日期列表（历史入口） |

`--mode collector` 与 `--mode standalone` 的分工是硬的：**两边零重叠**，所以合并时天然不冲突。

## 接上你自己的海外分身

代码、workflow、回退链路都已就位，只差你的仓库：

1. 建一个 GitHub 仓库（私有也行），把本项目推上去；
2. 在 `config/settings.yaml` 里填 `bundle.repo`（形如 `yourname/news-bundles`）；
3. 在 Actions 里手动触发一次 `collect`，然后本地跑 `newspipe sync`。

之后 `.github/workflows/collect.yml` 会在**北京时间每天 06:30**（UTC 22:30，给调度延迟留缓冲）
自动抓墙外源并提交 bundle —— 你早上开机时，内容已经躺在本地库里了。

## 目录

```
config/           settings.yaml（全部旋钮）· sources.yaml（信息源）· domains.yaml（领域）
src/newspipe/
  collectors/     rss / api / base(HTTP: UA·限速·代理)
  pipeline/       normalize · extract(正文抽取) · dedupe(去重聚类) · score · bundle
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
- **历史永久保留**：`items_fts` 是 FTS5 虚拟表（中文用 trigram 分词），全历史可检索，
  不做滚动过期。
- **正文优先于链接**：正文在抓取时就落库，所以离线可读是默认状态，而不是缓存命中时的运气。
- **一个源失败不影响别人**：错误被收敛成 `ok=False` 记进 `fetch_log`，界面上标红而已。
- **配置文件是唯一事实来源**：界面只读，旋钮全在 `config/*.yaml`。

## 当前进度

- [x] **M0** 骨架：schema（含 FTS5）、采集器、CLI、幂等入库、规则打分、契约测试
- [x] **M1** 可用（代码层）：正文抽取、去重聚类、四屏界面（版式 A）、GitHub Actions 与 sync 回退
      —— 唯一待办是接上你自己的 GitHub 仓库（见上）
- [ ] **M2** 好读：LLM 中文摘要/打分、Markdown 归档、定时自动化
- [ ] **M3** 打磨：聚类展示、收藏、关注/屏蔽、源权重自学习、桌面外壳
