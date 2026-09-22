# GitHub 操作指南

> 这份是**可选步骤**的说明。先说结论：**不接 GitHub 也能用** —— 13 个外网源在本地直连就能抓到，
> 只有 7 个源（IGN / Hugging Face / Google Research / Import AI / DeepMind / Anthropic）
> 需要代理。接 GitHub 的意义是让这 7 个源在**你睡觉的时候**被自动抓下来。

---

## 第 0 步：想清楚要不要做

| | 不接 GitHub | 接 GitHub |
|---|---|---|
| 当天外网新闻 | ✅ 13 个源直连抓到了 | ✅ 再加上 7 个源 |
| 需要你做什么 | 什么都不用做 | 建仓库 + 推代码 + 触发一次 |
| 抓取时机 | 你开机 + 跑 `newspipe run` 时 | 每天北京 06:30 自动（云端） |
| 万一某天源连不上 | 那天就没有 | 云端照抓 |

如果你只是想"每天有份日报看"，第 0 步可以直接跳到最后的**替代方案**。

---

## 第 1 步：在 GitHub 建一个空仓库

打开 <https://github.com/new>：

- **Repository name**：`news-daily`（或任何你喜欢的名字）
- **Public / Private**：见下面的权衡表
- ⚠️ **不要**勾选 "Add a README file"、"Add .gitignore"、"Choose a license"
  —— 仓库必须是完全空的，否则 `git push` 会因为历史不一致被拒

点 **Create repository**。

### 公开还是私有？

| | Public（推荐） | Private |
|---|---|---|
| 本地拉取走哪条路 | jsDelivr CDN（国内最稳） | 只能用 GitHub API + token |
| 需要 token 吗 | 不需要 | 需要（见第 5 步） |
| Actions 免费额度 | 无限 | 2000 分钟/月（每天跑 2 分钟，够用） |
| 代价 | **你的代码会公开** | 代码私有，但拉取链路更脆 |

`bundles/` 里只有公开的新闻标题和正文，本身不含隐私 —— 公开的风险主要在**代码本身**。

如果你想代码私有、只有 bundle 公开：建两个仓库，把 collect 的产物推到那个公开仓库（需要改
`.github/workflows/collect.yml` 里的 checkout/push 目标，告诉我我来改）。

---

## 第 2 步：把本地代码推上去

```powershell
cd "D:\DSH工作区\news-daily"

git remote add origin https://github.com/<你的用户名>/news-daily.git
git push -u origin main
```

- 第一次 push 会**弹出 Git Credential Manager 窗口**，选 "Sign in with your browser"，
  在浏览器里登录一次 GitHub 即可（本机还没存过凭证）
- 实测 `github.com` 在国内直连可达（HTTP 200），**这一步不需要 VPN**
- 推送约 50 个文件，几百 KB

---

## 第 3 步：打开 Actions 的写权限

这一步很容易漏，漏了的话 workflow 表面成功、实际什么都没提交。

仓库页面 → **Settings** → **Actions** → **General** → 拉到底部的 **Workflow permissions**
→ 选 **Read and write permissions** → **Save**

---

## 第 4 步：手动触发一次

1. 仓库页面 → **Actions** 标签
2. 左侧列表里选 **collect**
3. 右边点 **Run workflow** → 绿色 **Run workflow** 按钮
4. 等 1~3 分钟，点进这次运行看日志

成功的标志：运行结束后，仓库根目录出现 `bundles/2026-09-22.json` 这样的文件。

日志里每个源都会有一行 `[ OK ]` 或 `[FAIL]` —— 有 FAIL 是正常的（7 个源里总有连不上的），
只要最后写出了 bundle 就算成功。

---

## 第 5 步：告诉本地软件去哪里拿

编辑 `config/settings.yaml`，把 `bundle.repo` 从空改成 `<你的用户名>/news-daily`：

```yaml
bundle:
  repo: "yourname/news-daily"     # ← 改这里，格式是 用户名/仓库名，不要带 https://
  branch: main
  path: bundles
  token: ""                       # 公开仓库留空；私有仓库见下
```

> 如果你选了**私有仓库**，还需要一个 token：GitHub → 头像 → Settings →
> Developer settings → **Personal access tokens** → **Fine-grained tokens** →
> 新建，权限只给 **Contents: Read-only**，把生成的 token 填到 `bundle.token`。

然后：

```powershell
# 先诊断：配置对不对、GitHub 通不通、三层镜像哪层能过
.\.venv\Scripts\python.exe -m newspipe remote

# 拉回来合并入库
.\.venv\Scripts\python.exe -m newspipe sync

# 补正文 + 生成日报
.\.venv\Scripts\python.exe -m newspipe process
.\.venv\Scripts\python.exe -m newspipe digest
```

以后这些都会由 `newspipe run` 一条命令串起来。

---

## 之后会发生什么

- **每天北京时间 06:30**（UTC 22:30）：GitHub 自动跑 `collect`，抓那 7 个源，提交新 bundle
- **你本地每天 07:00**（如果装了定时任务）：`newspipe run` 自动 `sync` → `fetch` → `process` → `digest`
- 结果：早上打开软件，`archive/` 里已经有当天日报

```powershell
# 装本地定时任务（如果还没装）
.\.venv\Scripts\python.exe -m newspipe schedule install --at 07:00
```

---

## 出问题怎么排查

```powershell
.\.venv\Scripts\python.exe -m newspipe remote
```

它会告诉你四件事：配置对不对、`github.com` / `api.github.com` 通不通、
三层镜像（jsDelivr → GitHub API → raw）逐层能不能取到、本地留存有几份。

| 现象 | 原因 | 怎么办 |
|---|---|---|
| workflow 日志说"没有新内容，跳过提交" | `.gitignore` 把 `bundles/` 忽略了 | 本仓库已修（见提交 `c57ff78` 之后），确认你的 `.gitignore` 里没有 `bundles/` |
| workflow 最后 push 失败 | 第 3 步的写权限没开 | 回去开 Read and write permissions |
| `git push` 报 `fetch first` / `rejected` | 建仓库时勾了 README | 删掉仓库重建，或先 `git pull --rebase origin main` |
| `sync` 说没有可用的 bundle | 第 4 步没触发，或跑失败了 | 去 Actions 页面看那次运行的日志 |
| jsDelivr 取不到但 GitHub API 能取到 | jsDelivr 缓存还没刷新（首次约 10 分钟） | 等一会儿，或把 `bundle.token` 配上走 API |
| 私有仓库 sync 报 404 | token 没配或权限不对 | 见第 5 步的 token 说明 |

---

## 替代方案：不接 GitHub

开着 VPN / 代理的时候跑一次：

```powershell
.\.venv\Scripts\python.exe -m newspipe catchup
```

它会把那 7 个墙外源抓下来**直接入库**（顺带写一份 bundle 到 `data/bundles/`），
之后读的时候完全不需要网络。如果代理不是全局模式，先在 `config/settings.yaml` 里填
`network.proxy`（例如 `http://127.0.0.1:7890`）。

代价是：你得记得偶尔开一次代理跑它，而不是让它自己发生。
