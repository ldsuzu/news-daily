"""命令行入口。

    newspipe fetch    抓本地可达源并入库
    newspipe collect  海外模式：只抓墙外源，产出 bundle JSON（给 GitHub Actions 用）
    newspipe catchup  开着 VPN 跑一次：抓墙外源并入库（不需要 GitHub）
    newspipe sync     拉取海外分身的 bundle 并合并入库（四层镜像回退）
    newspipe remote   海外分身链路诊断（配置 / 镜像可达性 / 下一步）
    newspipe run      跑完整流水线：sync → fetch → process → digest（定时任务用它）
    newspipe digest   生成 Markdown 日报到 archive/
    newspipe schedule 注册每日定时任务（Windows 任务计划 / crontab）
    newspipe process  补正文 + 去重聚类（加工都在这，抓取只负责拿回来）
    newspipe enrich   LLM 翻译标题 + 中文摘要 + 热度评分（要配 api_key）
    newspipe serve    打开本地阅读界面（今日 / 历史 / 信息源 / 设置）
    newspipe doctor   源健康检查
    newspipe stats    看看库里有什么
    newspipe search   全历史检索
    newspipe dates    有内容的日期列表（历史入口）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import load_config
from .collectors import collect_source
from .collectors.base import http_from_config
from .models import RawItem
from .pipeline.bundle import items_from_payload, write_bundle
from .pipeline.dedupe import cluster_items
from .pipeline.digest import write_digests
from .pipeline.extract import extract_for_url, trafilatura_available
from .pipeline.llm import LlmClient
from .pipeline.normalize import now_utc_iso, to_local_date, utc_today
from .pipeline.score import score_items
from .storage.db import connect, init_db, sqlite_version
from .storage.repo import Repo
from .sync import (
    MIRROR_LABELS,
    fallback_local,
    github_headers,
    mirror_urls,
    pull_recent,
    pull_remote,
    read_payload,
)


def _force_utf8_stdout() -> None:
    # Windows 控制台默认 code page 会把中文和符号打成乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _open(args: argparse.Namespace):
    root = Path(args.root).resolve() if getattr(args, "root", None) else None
    config = load_config(root)
    config.settings.ensure_dirs()
    conn = connect(config.settings.db_path)
    tokenizer = init_db(conn)
    repo = Repo(conn)
    repo.sync_sources(config.sources)
    return config, conn, repo, tokenizer


# ───────────────────────────── fetch / collect ─────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> int:
    config, conn, repo, _ = _open(args)
    mode = args.mode
    sources = config.enabled_sources(mode=mode, source_ids=args.source or None)

    if not sources:
        print(f"没有匹配的源（mode={mode}，检查 sources.yaml 的 enabled / needs_proxy）")
        return 1

    label = "墙外源 → bundle" if mode == "collector" else "本地可达源"
    print(f"抓取 {len(sources)} 个源（{label}）\n")

    results = []
    total_new = 0
    with http_from_config(config) as http:
        for src in sources:
            started = now_utc_iso()
            result = collect_source(src, http, config)
            if result.ok:
                score_items(result.items, src)      # 分数只用于排序和精选阈值

            stats = repo.upsert_items(
                result.items, origin="remote" if mode == "collector" else "local"
            )
            repo.log_fetch(src.id, started, result.ok, result.count, result.error)
            results.append(result)
            total_new += stats["new"]

            if result.ok:
                print(f"[ OK ] {src.id:<16} {result.count:>3} 条   新增 {stats['new']:>3} / 已见 {stats['seen']:>3}")
            else:
                print(f"[FAIL] {src.id:<16} {result.error}")

    if mode == "collector":
        out_dir = Path(args.out) if args.out else config.settings.path("bundles")
        # 用 UTC 日期命名 —— GitHub runner 就是 UTC，本地按北京时区找会差一天
        date = args.date or utc_today()
        path = write_bundle(out_dir, date, results)
        print(f"\nbundle 已写出：{path}")

    counts = repo.counts()
    print(f"\n合计新增 {total_new} 条 · 库中共 {counts['total']} 条（今日 {counts['today']} 条）")
    conn.close()
    return 0


# ───────────────────────────── catchup ─────────────────────────────

def cmd_catchup(args: argparse.Namespace) -> int:
    """开着 VPN 时跑一次：抓所有墙外源并入库。

    这是**不接 GitHub 也能让外网内容进本地库**的办法 —— 抓完就存下来了，
    之后读的时候不需要任何网络。代价是你得在某个时刻开着代理跑一次。

    想要"睡着的时候自动抓"，那就接 GitHub Actions（`newspipe remote` 有步骤）。
    """
    return cmd_fetch(
        argparse.Namespace(
            root=getattr(args, "root", None),
            source=args.source,
            mode="collector",
            out=args.out,
            date=args.date,
        )
    )


# ───────────────────────────── doctor ─────────────────────────────

def cmd_doctor(args: argparse.Namespace) -> int:
    config, conn, repo, tokenizer = _open(args)
    print(f"库：{config.settings.db_path}")
    print(f"SQLite {sqlite_version()} · 全文检索分词器：{tokenizer}")
    print(f"代理：{config.settings.proxy or '（直连）'}\n")

    sources = config.enabled_sources(mode=args.mode, source_ids=args.source or None)
    ok = 0
    with http_from_config(config) as http:
        for src in sources:
            url = src.url
            if src.type == "rsshub":
                base = config.settings.get("rsshub.base") or ""
                if not base:
                    print(f"[skip] {src.id:<16} 未配置 RSSHub，跳过")
                    continue
                url = base.rstrip("/") + "/" + src.url.lstrip("/")

            t0 = time.monotonic()

            # api / html 类型的源没法用一个裸 URL 探测：api 要带查询参数、html 要走解析。
            # 之前这里把 arXiv 报成 400、把 Hacker News 报成超时，全是误报。
            if src.type in ("api", "html"):
                result = collect_source(src, http, config)
                ms = int((time.monotonic() - t0) * 1000)
                if result.ok:
                    print(f"[ OK ] {src.id:<16} {result.count:>4} 条   {ms:>6}ms")
                    ok += 1
                else:
                    print(f"[FAIL] {src.id:<16} {ms:>6}ms   {result.error[:64]}")
                continue

            try:
                resp = http.get(url)
                ms = int((time.monotonic() - t0) * 1000)
                print(f"[ OK ] {src.id:<16} {resp.status_code}  {ms:>5}ms  {len(resp.content) // 1024:>5}KB")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                ms = int((time.monotonic() - t0) * 1000)
                print(f"[FAIL] {src.id:<16} {ms:>5}ms  {type(exc).__name__}: {str(exc)[:70]}")

    print(f"\n{ok}/{len(sources)} 个源可达")
    conn.close()
    return 0


# ───────────────────────────── process / sync ─────────────────────────────

def cmd_process(args: argparse.Namespace) -> int:
    """抓取只负责把条目拿回来；补正文和去重聚类都在这里做。"""
    config, conn, repo, _ = _open(args)

    if not args.no_extract:
        if not trafilatura_available():
            print("[warn] 未安装 trafilatura，跳过正文抽取（pip install trafilatura）")
        else:
            rows = conn.execute(
                """
                SELECT i.id, i.url, LENGTH(i.content_text) AS n
                FROM items i
                JOIN sources s ON s.id = i.source_id
                WHERE LENGTH(i.content_text) < ?
                  AND s.extract = 1
                ORDER BY COALESCE(i.published_at, i.fetched_at) DESC
                LIMIT ?
                """,
                (args.min_chars, args.limit),
            ).fetchall()
            print(f"正文不足 {args.min_chars} 字的条目：{len(rows)} 条（本轮上限 {args.limit}）")
            print("  （SPA 站点抽不出正文，已在 sources.yaml 里标 extract: false，不再浪费请求）")

            filled = 0
            shorter = 0
            nothing = 0
            samples: list[str] = []

            with http_from_config(config) as http:
                for i, row in enumerate(rows, 1):
                    text = extract_for_url(row["url"], http)
                    if len(text) > (row["n"] or 0):
                        conn.execute(
                            "UPDATE items SET content_text = ? WHERE id = ?", (text, row["id"])
                        )
                        filled += 1
                    elif text:
                        shorter += 1
                    else:
                        nothing += 1
                        if len(samples) < 3:
                            samples.append(row["url"])
                    if i % 25 == 0:
                        conn.commit()
                        print(f"  … {i}/{len(rows)}（已补 {filled}）")
            conn.commit()

            print(f"补到正文 {filled} 条")
            if nothing or shorter:
                print(f"  （抓不到内容 {nothing} 条 · 抽出的比现有更短 {shorter} 条）")
            for url in samples:
                print(f"  ✗ {url[:88]}")

    if not args.no_cluster:
        stats = cluster_items(conn, window_days=args.window_days)
        print(
            f"聚类：扫描 {stats['scanned']} 条 → {stats['clusters']} 个簇，"
            f"涉及 {stats['grouped_items']} 条重复报道"
        )

    counts = repo.counts()
    print(f"\n库中共 {counts['total']} 条（今日 {counts['today']}）")
    conn.close()
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """把海外分身的 bundle 拿回来合并入库：镜像 → 本地留存 → 最近一次。"""
    config, conn, repo, _ = _open(args)
    date = args.date or utc_today()
    bundles_dir = config.settings.path("bundles")

    payload = None
    if config.settings.get("bundle.repo"):
        with http_from_config(config) as http:
            # 从今天往前找 3 天：采集分身按 UTC 22:30 跑，当天大部分时间还没有"今天的"bundle
            result = pull_recent(config, http, date, days=3)
        for url, why in result["tried"]:
            print(f"[FAIL] {url[:72]}\n        {why[:80]}")
        if result["ok"]:
            payload = result["payload"]
            date = result.get("date", date)
            (bundles_dir / f"{date}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print(f"[ OK ] bundle 已取回（{date}）：{result['label']}")
    else:
        print("[skip] 未配置 bundle.repo，直接看本地留存")

    if payload is None:
        path, why = fallback_local(bundles_dir, date)
        if path is None:
            print("没有可用的 bundle。两条路：")
            print("  · 开着 VPN / 代理跑一次 newspipe catchup（立刻可用，不需要 GitHub）")
            print("  · 或者接 GitHub Actions：newspipe remote 看步骤")
            conn.close()
            return 1
        payload = read_payload(path)
        if payload is None:
            print(f"本地 bundle 读取失败：{path}")
            conn.close()
            return 1
        print(f"[warn] 走本地回退：{why}")

    items = items_from_payload(payload)
    stats = repo.upsert_items(items, origin="remote")
    print(
        f"\nbundle 日期 {payload.get('date', '?')} · 生成于 {payload.get('generated_at', '?')}\n"
        f"条目 {len(items)} 条 · 新增 {stats['new']} · 已见 {stats['seen']}"
    )
    conn.close()
    return 0


# ───────────────────────────── enrich（LLM 翻译 + 热度） ─────────────────────────────

def cmd_enrich(args: argparse.Namespace) -> int:
    """给还没处理过的新条目做：中文标题 + 一句话摘要 + 热度评分，并记账。"""
    config, conn, repo, _ = _open(args)
    client = LlmClient(config)

    if not client.available:
        reason = "llm.enabled 是 false" if not client.enabled else "没配 api_key"
        print(f"LLM 未启用（{reason}），跳过这步。")
        print("  在 config/settings.yaml 的 llm 段填 api_key、把 enabled 改成 true；")
        print("  或者设环境变量 NEWSPIPE_LLM_KEY。")
        conn.close()
        return 0

    if args.reset:
        n = repo.reset_llm_markers(days=args.days)
        print(f"已把最近 {args.days} 天的 {n} 条重新排进队列。")

    max_items = int(config.settings.get("llm.max_items_per_run", 300))
    pending = repo.pending_for_llm(limit=max_items, days=args.days)
    spend = repo.llm_spend()

    print(f"模型 {client.model} · 每批 {client.batch_size} 条 · 今日已花 ¥{spend['today']['cost_cny']:.4f}")
    if not pending:
        print("没有需要处理的新条目。")
        conn.close()
        return 0
    print(f"待处理 {len(pending)} 条\n")

    done = 0
    failed = 0
    for start in range(0, len(pending), client.batch_size):
        batch_rows = pending[start : start + client.batch_size]
        items = [
            RawItem(
                source_id=r["source_id"], domain=r["domain"], url=r["url"],
                title=r["title"], excerpt=r["excerpt"], content_text=r["content_text"],
            )
            for r in batch_rows
        ]

        results, usage = client.enrich_batch(items)
        repo.record_llm_usage(usage, now_utc_iso())

        if not usage.ok:
            failed += 1
            print(f"  [FAIL] 第 {start // client.batch_size + 1} 批：{usage.note}")
        else:
            ts = now_utc_iso()
            for idx, res in results.items():
                repo.apply_enrichment(
                    batch_rows[idx]["id"],
                    title_cn=res.title_cn,
                    summary_cn=res.summary_cn,
                    heat=res.heat,
                    ts=ts,
                )
            conn.commit()
            done += len(results)
            print(
                f"  [ OK ] 第 {start // client.batch_size + 1} 批 "
                f"{len(results)}/{len(batch_rows)} 条 · "
                f"in {usage.input_tokens}+{usage.cached_tokens}(缓存) out {usage.output_tokens} · "
                f"¥{usage.cost_cny:.4f}"
            )

        # 花超预算就停手 —— 宁可少翻几条，也不能一晚上烧穿
        if client.daily_budget:
            spent = repo.llm_spend()["today"]["cost_cny"]
            if spent >= client.daily_budget:
                print(f"\n今日累计 ¥{spent:.4f} 已达上限 ¥{client.daily_budget}，停手。")
                break

    spend = repo.llm_spend()
    t, tot = spend["today"], spend["total"]
    print(
        f"\n本轮处理 {done} 条，失败 {failed} 批\n"
        f"今日：{t['items']} 条 · in {t['input_tokens']}+{t['cached_tokens']}(缓存) "
        f"out {t['output_tokens']} · ¥{t['cost_cny']:.4f}\n"
        f"累计：{tot['items']} 条 · ¥{tot['cost_cny']:.4f}（{tot['calls']} 次调用）"
    )
    conn.close()
    return 0


# ───────────────────────────── digest / run / schedule ─────────────────────────────

def cmd_digest(args: argparse.Namespace) -> int:
    """把当天（或指定日期）的内容写成 Markdown 日报，放进 archive/。"""
    config, conn, repo, _ = _open(args)
    date = args.date or to_local_date(now_utc_iso()) or ""

    paths = write_digests(config, repo, date, generated_at=now_utc_iso())
    if not paths:
        print(f"{date} 没有内容，没生成日报。")
        conn.close()
        return 1

    print(f"日报已写出（{date}）：")
    for path in paths:
        print(f"  {path}  ({path.stat().st_size} B)")
    conn.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """一条命令跑完整流水线：sync → fetch → process → digest。

    定时任务就调它 —— 早上开机时，日报已经躺在 archive/ 里了。
    """
    root = getattr(args, "root", None)
    date = args.date or to_local_date(now_utc_iso()) or ""
    shared = {"root": root, "date": date}

    steps: list[tuple[str, object, argparse.Namespace]] = []

    if not args.no_sync:
        steps.append(("① 同步海外 bundle", cmd_sync, argparse.Namespace(**shared)))
    if not args.no_fetch:
        steps.append(
            ("② 抓取本地可达源", cmd_fetch,
             argparse.Namespace(**shared, source=None, mode="standalone", out=None))
        )
    if not args.no_process:
        steps.append(
            ("③ 补正文 + 去重聚类", cmd_process,
             argparse.Namespace(**shared, limit=args.limit, min_chars=400, window_days=14,
                                no_extract=False, no_cluster=False))
        )
    if not args.no_enrich:
        steps.append(
            ("④ LLM 翻译 + 热度评分", cmd_enrich,
             argparse.Namespace(**shared, days=3, reset=False))
        )
    steps.append(("⑤ 生成日报", cmd_digest, argparse.Namespace(**shared)))

    failed: list[str] = []
    for name, func, ns in steps:
        print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
        try:
            rc = int(func(ns) or 0)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 —— 一步失败不该带走后面几步
            print(f"[ERROR] {name} 出错：{type(exc).__name__}: {exc}")
            rc = 1
        if rc != 0:
            failed.append(name)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"跑完了，但有 {len(failed)} 步没成功：{'、'.join(failed)}")
        print("（其它步骤的内容照常入库，不影响你读。）")
        return 1
    print("全部完成。")
    return 0


TASK_NAME = "每日简报 DailyDigest"


def cmd_schedule(args: argparse.Namespace) -> int:
    """把每日抓取注册成系统定时任务。

    Windows 用任务计划程序；其它平台打印一行 crontab 让你贴。
    """
    config, conn, repo, _ = _open(args)
    root = config.root
    at = args.at or config.settings.get("schedule.daily_at", "07:00")
    python = sys.executable
    action = args.action

    if os.name != "nt":
        hh, _, mm = at.partition(":")
        line = (
            f"{int(hh)} {int(mm or 0)} * * * cd {root} && {python} -m newspipe run "
            f">> {root}/data/run.log 2>&1"
        )
        print("非 Windows 平台：把下面这行加进 `crontab -e` 即可")
        print()
        print(f"  {line}")
        conn.close()
        return 0

    # Windows：走任务计划程序。用批处理文件包一层，省掉引号地狱。
    script = config.settings.path("data_dir") / "daily-run.cmd"
    script.write_text(
        "@echo off\r\n"
        f'cd /d "{root}"\r\n'
        f'"{python}" -m newspipe run >> "{root}\\data\\run.log" 2>&1\r\n',
        encoding="utf-8",
    )

    if action == "install":
        # 用 PowerShell 的 ScheduledTasks 而不是 schtasks：只有它能开出 StartWhenAvailable
        # （错过计划时间就尽快补跑）—— 这条直接决定"某天没开机会不会漏新闻"。
        ps_file = config.settings.path("data_dir") / "install-task.ps1"
        ps_file.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f'$action = New-ScheduledTaskAction -Execute "{script}"\n'
            f'$trigger = New-ScheduledTaskTrigger -Daily -At "{at}"\n'
            "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
            "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit (New-TimeSpan -Hours 2)\n"
            f'Register-ScheduledTask -TaskName "{TASK_NAME}" -Action $action '
            "-Trigger $trigger -Settings $settings -Force | Out-Null\n"
            "Write-Output 'OK'\n",
            encoding="utf-8-sig",   # 带 BOM，否则 PowerShell 读中文任务名会乱码
        )

        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = ((result.stdout or "") + (result.stderr or "")).strip()

        if result.returncode == 0 and "OK" in out:
            print(f"已注册：每天 {at} 自动跑一次 `newspipe run`")
            print(f"  任务名  {TASK_NAME}")
            print(f"  执行    {script}")
            print(f"  日志    {config.settings.path('data_dir') / 'run.log'}")
            print("  错过补跑：已开启（关机错过时间点，开机后会尽快跑一次）")
            print("\n查看状态：newspipe schedule status")
            print("取消任务：newspipe schedule uninstall")
        else:
            print("注册失败：")
            print(out[:400])
            print(f"\n可以手动在「任务计划程序」里新建每日任务，执行：{script}")
        conn.close()
        return result.returncode

    if action == "uninstall":
        result = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        print(((result.stdout or "") + (result.stderr or "")).strip()[:300])
        conn.close()
        return result.returncode

    # status
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        print("还没有注册定时任务。")
        print(f"跑这个装上：newspipe schedule install --at {at}")
    else:
        for line in text.splitlines():
            if line.strip():
                print("  " + line.strip())
        print(f"\n手动跑一次看看：{script}")
    conn.close()
    return 0


# ───────────────────────────── remote（海外链路诊断） ─────────────────────────────

def cmd_remote(args: argparse.Namespace) -> int:
    """海外分身链路诊断：配置对不对、每层镜像通不通、下一步该做什么。"""
    config, conn, repo, _ = _open(args)
    s = config.settings

    repo_name = s.get("bundle.repo") or ""
    branch = s.get("bundle.branch", "main")
    path = s.get("bundle.path", "bundles")
    token = s.get("bundle.token") or os.environ.get("NEWSPIPE_GH_TOKEN") or ""
    date = args.date or utc_today()

    print("海外采集分身 · 链路诊断")
    print(f"  仓库       {repo_name or '（未配置 bundle.repo）'}")
    print(f"  分支 / 目录 {branch} / {path}")
    print(f"  token      {'已配置' if token else '未配置（公开仓库不需要）'}")
    print(f"  查询日期    {date}\n")

    if not repo_name:
        print("（还没配置 bundle.repo，先看 GitHub 通不通，再按下面三步接上它）\n")

    with http_from_config(config) as http:
        print("① GitHub 是否可达（只影响你 push 那一次；日常 sync 不需要）")
        for probe in ("https://github.com", "https://api.github.com"):
            t0 = time.monotonic()
            try:
                resp = http.get(probe)
                ms = int((time.monotonic() - t0) * 1000)
                print(f"   [ OK ] {probe:30s} {resp.status_code}  {ms:>5}ms")
            except Exception as exc:  # noqa: BLE001
                ms = int((time.monotonic() - t0) * 1000)
                print(f"   [FAIL] {probe:30s} {ms:>5}ms  {type(exc).__name__}: {str(exc)[:46]}")

        if not repo_name:
            print("\n让外网内容进本地库，有两条路：\n")
            print("  ① 立刻可用，不需要 GitHub —— 开着 VPN / 代理跑一次：")
            print("       newspipe catchup")
            print("     抓到的条目的正文直接入库，之后读的时候完全不需要网络。")
            print("     代理不是全局模式的话，先在 config/settings.yaml 里填 network.proxy。\n")
            print("  ② 全自动，你睡着的时候也在抓 —— 接 GitHub Actions：")
            print("       git remote add origin https://github.com/<你>/<仓库>.git")
            print("       git push -u origin main")
            print("     然后在 config/settings.yaml 填 bundle.repo，")
            print("     在仓库的 Actions 页手动触发一次 collect，本地再跑 newspipe sync。")
            print("     之后每天北京时间 06:30 自动抓。")
            conn.close()
            return 1

        print(f"\n② 镜像逐层测试（取 {date} 的 bundle）")
        urls = mirror_urls(config, date)
        if not urls:
            print("   没有配置任何镜像 URL")
        for i, url in enumerate(urls):
            label = MIRROR_LABELS[i] if i < len(MIRROR_LABELS) else f"镜像 {i + 1}"
            t0 = time.monotonic()
            try:
                text = http.get_text(url, headers=github_headers(config, url))
                ms = int((time.monotonic() - t0) * 1000)
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and "items" in payload:
                    print(f"   [ OK ] {label:22s} {ms:>5}ms  {len(payload.get('items', []))} 条")
                else:
                    print(f"   [BAD ] {label:22s} {ms:>5}ms  响应不是 bundle（这天可能还没产出）")
            except Exception as exc:  # noqa: BLE001
                ms = int((time.monotonic() - t0) * 1000)
                print(f"   [FAIL] {label:22s} {ms:>5}ms  {type(exc).__name__}: {str(exc)[:46]}")

    print("\n③ 本地留存")
    bundles_dir = s.path("bundles")
    kept = sorted(bundles_dir.glob("*.json"), reverse=True)
    if kept:
        print(f"   已有 {len(kept)} 份，最近：{kept[0].name}")
    else:
        print("   还没有任何 bundle —— 说明海外分身还没成功跑过一次")

    print("\n④ 最近的抓取记录")
    rows = conn.execute(
        "SELECT source_id, started_at, ok, count FROM fetch_log ORDER BY started_at DESC LIMIT 6"
    ).fetchall()
    if not rows:
        print("   （还没有任何抓取记录）")
    for r in rows:
        mark = "OK " if r["ok"] else "ERR"
        print(f"   [{mark}] {r['source_id']:16s} {r['started_at'][:16]}  {r['count']} 条")

    conn.close()
    return 0


# ───────────────────────────── serve ─────────────────────────────

def cmd_serve(args: argparse.Namespace) -> int:
    """起本地服务，浏览器打开就是阅读界面。没有网络也照常能读。"""
    try:
        import uvicorn
    except ImportError:
        print("需要 web 依赖：pip install -e .[web]")
        return 1

    from .web.app import create_app

    root = Path(args.root).resolve() if getattr(args, "root", None) else None
    config = load_config(root)
    config.settings.ensure_dirs()

    url = f"http://{args.host}:{args.port}"
    print(f"{config.settings.get('app.name', '每日简报')} 已启动：{url}")
    print("按 Ctrl+C 停止")

    if args.open:
        import webbrowser

        webbrowser.open(url)

    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="warning")
    return 0


# ───────────────────────────── stats / search / dates ─────────────────────────────

def cmd_stats(args: argparse.Namespace) -> int:
    config, conn, repo, _ = _open(args)
    counts = repo.counts()

    print(f"库：{config.settings.db_path}")
    print(f"总条目 {counts['total']} · 今日 {counts['today']}")
    print(f"按领域 {counts['by_domain']}")
    print(f"按来源归属 {counts['by_origin']}")

    body = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN LENGTH(content_text) >= 400 THEN 1 ELSE 0 END) AS full,
               CAST(AVG(LENGTH(content_text)) AS INT) AS avg_len
        FROM items
        """
    ).fetchone()
    clusters = conn.execute("SELECT COUNT(*) AS c FROM clusters").fetchone()["c"]
    print(
        f"正文：{body['full']}/{body['total']} 条够离线精读（平均 {body['avg_len'] or 0} 字）"
        f" · 聚类：{clusters} 个簇\n"
    )

    rows = repo.sources_overview()
    print(f"{'源':<18}{'领域':<6}{'归属':<8}{'条目':>6}{'7日抓取':>8}  状态")
    print("-" * 68)
    for r in rows:
        where = "海外" if r["needs_proxy"] else "本地"
        if r["fail_count"]:
            state = f"连续失败 {r['fail_count']} 次"
        elif r["last_ok_at"]:
            state = f"上次成功 {r['last_ok_at']}"
        else:
            state = "尚未抓取"
        print(f"{r['name'][:16]:<18}{r['domain']:<6}{where:<8}{r['item_count']:>6}{r['fetches_7d']:>8}  {state}")

    dates = repo.available_dates(limit=7)
    if dates:
        print("\n最近的日期：" + " · ".join(f"{d['date']}({d['n']})" for d in dates))

    conn.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config, conn, repo, _ = _open(args)
    hits = repo.search(args.query, limit=args.limit)
    if not hits:
        print("没有命中。")
        conn.close()
        return 0

    print(f"命中 {len(hits)} 条：\n")
    for h in hits:
        score = f"{h['score']:.1f}" if h["score"] else "-"
        print(f"[{score:>4}] {h['pub_date'] or '????-??-??'}  {h['title'][:70]}")
        print(f"        {h['source_id']} · {h['url'][:80]}")
    conn.close()
    return 0


def cmd_dates(args: argparse.Namespace) -> int:
    config, conn, repo, _ = _open(args)
    for d in repo.available_dates(limit=args.limit):
        print(f"{d['date']}  {d['n']:>5} 条")
    conn.close()
    return 0


# ───────────────────────────── 参数解析 ─────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="newspipe", description="每日简报 · 本地新闻聚合")
    parser.add_argument("--root", help="项目根目录（默认自动推断，可用 NEWSPIPE_ROOT 覆盖）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", action="append", help="只处理指定源 id，可重复")
        p.add_argument(
            "--mode",
            choices=["standalone", "collector"],
            default="standalone",
            help="standalone=抓本地可达源；collector=抓墙外源（海外分身用）",
        )

    p_fetch = sub.add_parser("fetch", help="抓取并入库")
    add_common(p_fetch)
    p_fetch.add_argument("--out", help="collector 模式的 bundle 输出目录")
    p_fetch.add_argument("--date", help="bundle 日期（默认今天）")
    p_fetch.set_defaults(func=cmd_fetch)

    p_collect = sub.add_parser("collect", help="海外模式：抓墙外源并产出 bundle")
    p_collect.set_defaults(mode="collector")
    p_collect.add_argument("--source", action="append", help="只处理指定源 id")
    p_collect.add_argument("--out", help="bundle 输出目录（默认 bundles/）")
    p_collect.add_argument("--date", help="bundle 日期（默认今天）")
    p_collect.set_defaults(func=cmd_fetch)

    p_catchup = sub.add_parser("catchup", help="开 VPN 时抓墙外源并入库（不需要 GitHub）")
    p_catchup.add_argument("--source", action="append", help="只处理指定源 id")
    p_catchup.add_argument("--out", help="bundle 输出目录")
    p_catchup.add_argument("--date", help="日期（默认今天）")
    p_catchup.set_defaults(func=cmd_catchup)

    p_doctor = sub.add_parser("doctor", help="源健康检查")
    add_common(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_process = sub.add_parser("process", help="补正文 + 去重聚类")
    p_process.add_argument("--limit", type=int, default=60, help="本轮最多补多少条正文")
    p_process.add_argument("--min-chars", type=int, default=400, help="正文少于此字数才补抓")
    p_process.add_argument("--window-days", type=int, default=14, help="聚类回看天数")
    p_process.add_argument("--no-extract", action="store_true", help="跳过正文抽取")
    p_process.add_argument("--no-cluster", action="store_true", help="跳过聚类")
    p_process.set_defaults(func=cmd_process)

    p_sync = sub.add_parser("sync", help="拉取海外 bundle 并合并入库")
    p_sync.add_argument("--date", help="要拉哪天的 bundle（默认今天）")
    p_sync.set_defaults(func=cmd_sync)

    p_enrich = sub.add_parser("enrich", help="LLM 翻译 + 热度评分（要配 api_key）")
    p_enrich.add_argument("--days", type=int, default=3, help="只处理最近几天的条目")
    p_enrich.add_argument("--reset", action="store_true", help="先把最近几天的 llm_at 清掉，重新排队")
    p_enrich.set_defaults(func=cmd_enrich)

    p_remote = sub.add_parser("remote", help="海外分身链路诊断")
    p_remote.add_argument("--date", help="要检查哪天的 bundle（默认今天）")
    p_remote.set_defaults(func=cmd_remote)

    p_serve = sub.add_parser("serve", help="打开本地阅读界面")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    p_serve.set_defaults(func=cmd_serve)

    p_digest = sub.add_parser("digest", help="生成 Markdown 日报（archive/）")
    p_digest.add_argument("--date", help="哪一天（默认今天）")
    p_digest.set_defaults(func=cmd_digest)

    p_run = sub.add_parser("run", help="跑完整流水线：sync → fetch → process → digest")
    p_run.add_argument("--date", help="哪一天（默认今天）")
    p_run.add_argument("--limit", type=int, default=40, help="本轮最多补多少条正文")
    p_run.add_argument("--no-sync", action="store_true", help="跳过海外 bundle 同步")
    p_run.add_argument("--no-fetch", action="store_true", help="跳过本地源抓取")
    p_run.add_argument("--no-process", action="store_true", help="跳过正文与聚类")
    p_run.add_argument("--no-enrich", action="store_true", help="跳过 LLM 翻译与评分")
    p_run.set_defaults(func=cmd_run)

    p_sched = sub.add_parser("schedule", help="注册每日定时任务")
    p_sched.add_argument(
        "action", nargs="?", default="status",
        choices=["status", "install", "uninstall"],
        help="默认 status",
    )
    p_sched.add_argument("--at", help="每天几点跑（默认取 settings.schedule.daily_at）")
    p_sched.set_defaults(func=cmd_schedule)

    p_stats = sub.add_parser("stats", help="库内容概览")
    p_stats.set_defaults(func=cmd_stats)

    p_search = sub.add_parser("search", help="全历史检索")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=30)
    p_search.set_defaults(func=cmd_search)

    p_dates = sub.add_parser("dates", help="有内容的日期")
    p_dates.add_argument("--limit", type=int, default=30)
    p_dates.set_defaults(func=cmd_dates)

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
