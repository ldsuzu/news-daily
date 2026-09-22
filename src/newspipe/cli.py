"""命令行入口。

    newspipe fetch    抓本地可达源并入库
    newspipe collect  海外模式：只抓墙外源，产出 bundle JSON（给 GitHub Actions 用）
    newspipe sync     拉取海外分身的 bundle 并合并入库（四层镜像回退）
    newspipe process  补正文 + 去重聚类（加工都在这，抓取只负责拿回来）
    newspipe serve    打开本地阅读界面（今日 / 历史 / 信息源 / 设置）
    newspipe doctor   源健康检查
    newspipe stats    看看库里有什么
    newspipe search   全历史检索
    newspipe dates    有内容的日期列表（历史入口）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import load_config
from .collectors import collect_source
from .collectors.base import http_from_config
from .pipeline.bundle import items_from_payload, write_bundle
from .pipeline.dedupe import cluster_items
from .pipeline.extract import extract_for_url, trafilatura_available
from .pipeline.normalize import now_utc_iso, to_local_date
from .pipeline.score import score_items
from .storage.db import connect, init_db, sqlite_version
from .storage.repo import Repo
from .sync import fallback_local, pull_remote, read_payload


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
        date = args.date or to_local_date(now_utc_iso()) or ""
        path = write_bundle(out_dir, date, results)
        print(f"\nbundle 已写出：{path}")

    counts = repo.counts()
    print(f"\n合计新增 {total_new} 条 · 库中共 {counts['total']} 条（今日 {counts['today']} 条）")
    conn.close()
    return 0


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
                SELECT id, url, LENGTH(content_text) AS n
                FROM items
                WHERE LENGTH(content_text) < ?
                ORDER BY COALESCE(published_at, fetched_at) DESC
                LIMIT ?
                """,
                (args.min_chars, args.limit),
            ).fetchall()
            print(f"正文不足 {args.min_chars} 字的条目：{len(rows)} 条（本轮上限 {args.limit}）")

            filled = 0
            with http_from_config(config) as http:
                for i, row in enumerate(rows, 1):
                    text = extract_for_url(row["url"], http)
                    if len(text) > (row["n"] or 0):
                        conn.execute(
                            "UPDATE items SET content_text = ? WHERE id = ?", (text, row["id"])
                        )
                        filled += 1
                    if i % 25 == 0:
                        conn.commit()
                        print(f"  … {i}/{len(rows)}（已补 {filled}）")
            conn.commit()
            print(f"补到正文 {filled} 条")

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
    date = args.date or to_local_date(now_utc_iso()) or ""
    bundles_dir = config.settings.path("bundles")

    payload = None
    if config.settings.get("bundle.repo"):
        with http_from_config(config) as http:
            result = pull_remote(config, http, date)
        for url, why in result["tried"]:
            print(f"[FAIL] {url[:72]}\n        {why[:80]}")
        if result["ok"]:
            payload = result["payload"]
            (bundles_dir / f"{date}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print(f"[ OK ] bundle 已取回：{result['label']}")
    else:
        print("[skip] 未配置 bundle.repo，直接看本地留存")

    if payload is None:
        path, why = fallback_local(bundles_dir, date)
        if path is None:
            print("没有可用的 bundle —— 海外分身还没跑过，或者仓库地址没配好。")
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

    p_serve = sub.add_parser("serve", help="打开本地阅读界面")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    p_serve.set_defaults(func=cmd_serve)

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
