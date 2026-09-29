"""界面路由。

版式按原型变体 A（报纸版式）：报头 → 头条 → 三栏次条 → 多栏列表。
四屏：今日 / 历史 / 信息源 / 设置。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, load_config, update_setting
from ..pipeline.normalize import now_utc_iso, parse_iso, to_local_date
from ..pipeline.select import select_picks
from ..secrets import delete_api_key, get_api_key, has_api_key, set_api_key, where_is_it
from ..storage.db import connect, init_db, sqlite_version
from ..storage.repo import Repo

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


# ───────────────────────────── 模板过滤器 ─────────────────────────────

def _hhmm(value: str | None) -> str:
    dt = parse_iso(value)
    return dt.astimezone().strftime("%H:%M") if dt else "--:--"


def _date_label(value: str | None) -> str:
    try:
        d = _date.fromisoformat(value or "")
    except (ValueError, TypeError):
        return value or ""
    return f"{d.year}年{d.month}月{d.day}日 星期{'一二三四五六日'[d.weekday()]}"


def _date_short(value: str | None, with_year: bool = False) -> str:
    """9月29日 周一 —— 顶栏用的紧凑写法，年份默认省掉。"""
    try:
        d = _date.fromisoformat(value or "")
    except (ValueError, TypeError):
        return value or ""
    prefix = f"{d.year}年" if with_year else ""
    return f"{prefix}{d.month}月{d.day}日 周{'一二三四五六日'[d.weekday()]}"


def _excerpt(value: Any, limit: int = 800) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _shift_date(value: str | None, days: int) -> str:
    try:
        d = _date.fromisoformat(value or "")
    except (ValueError, TypeError):
        return value or ""
    return (d + timedelta(days=days)).isoformat()


def _score_class(value: float | None) -> str:
    v = value or 0
    return "hi" if v >= 8 else "mid" if v >= 6 else "lo"


def _items_payload(
    rows: list[dict[str, Any]], source_map: dict[str, dict[str, Any]], body_limit: int = 4000
) -> str:
    """今日页右栏用的数据。

    整页嵌进 HTML 让点选不用再请求 —— 本地软件，省一次往返比省几十 KB 值。
    正文截到 4000 字，够读也够小；要看全文点标题去原文。
    """
    payload = []
    for it in rows:
        body = (it.get("content_text") or "").strip()
        lang = str(it.get("lang") or "")
        is_foreign = bool(lang and not lang.startswith("zh"))

        # 只有外网条目才谈得上「原文」—— 中文源的 title 本来就是中文，
        # 不能拿它当原文标题显示（之前就是这么错的）
        original = ""
        if is_foreign:
            if it.get("title_cn") and it["title_cn"] != it.get("title"):
                original = it.get("title") or ""
            elif it.get("title_en") and it["title_en"] != it.get("title"):
                original = it.get("title_en") or ""

        heat = it.get("llm_score")
        if heat is None:
            heat = it.get("score") or 0

        payload.append(
            {
                "zh": it.get("title_cn") or it.get("title") or "",
                "en": original,
                "sum": (it.get("summary_cn") or it.get("excerpt") or "")[:200],
                "body": body[:body_limit],
                "chars": len(body),
                "src": source_map.get(it.get("source_id", ""), {}).get(
                    "name", it.get("source_id", "")
                ),
                "dom": it.get("domain", ""),
                "t": _hhmm(it.get("published_at")),
                "heat": round(float(heat), 1),
                "url": it.get("url", ""),
                "ext": is_foreign,
                "same": int(it.get("cluster_size") or 1),
            }
        )
    return json.dumps(payload, ensure_ascii=False)


templates.env.filters.update(
    hhmm=_hhmm,
    date_label=_date_label,
    date_short=_date_short,
    excerpt=_excerpt,
    shift_date=_shift_date,
    score_class=_score_class,
)


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="每日简报", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def _open():
        cfg = config or load_config()
        cfg.settings.ensure_dirs()
        conn = connect(cfg.settings.db_path)
        init_db(conn)
        return cfg, conn, Repo(conn)

    def _ctx(request: Request, cfg: Config, repo: Repo, **extra: Any) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "request": request,
            "site_name": cfg.settings.get("app.name", "每日简报"),
            "domain_tabs": [{"id": "all", "name": "全部"}]
            + [{"id": d.id, "name": d.name} for d in cfg.domains],
            "source_map": repo.sources_map(),
            "picks_per_domain": int(cfg.settings.get("selection.picks_per_domain", 10)),
            "picks_min_score": float(cfg.settings.get("selection.picks_min_score", 0)),
            "llm_spend": repo.llm_spend(),
            "llm_on": bool(cfg.settings.get("llm.enabled", False)),
        }
        ctx.update(extra)
        return ctx

    # ───────────────────────────── 今日 ─────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def page_today(
        request: Request,
        domain: str = "all",
        view: str = "picks",
        date: str = "",
    ) -> HTMLResponse:
        cfg, conn, repo = _open()
        try:
            picks_min = float(cfg.settings.get("selection.picks_min_score", 0))
            picks_n = int(cfg.settings.get("selection.picks_per_domain", 10))
            dom = None if domain == "all" else domain
            target = date or (to_local_date(now_utc_iso()) or "")

            total_today = repo.count_items(domain=dom, date=target)

            if view == "picks":
                # 精选 = 按重要度取前 N 条（不是"够 N 分才进来"），阈值只是可选下界；
                # 同时限制单源占比，避免一家把名额占满
                candidates = repo.list_items(
                    domain=dom,
                    date=target,
                    limit=200,
                    order="score",
                    representatives_only=True,
                    relevant_only=True,
                )
                shown = select_picks(
                    candidates,
                    picks_n,
                    max_per_source=int(cfg.settings.get("selection.max_per_source", 3)),
                )
                if picks_min > 0:
                    shown = [r for r in shown if (r["score"] or 0) >= picks_min]
                picks_count = len(shown)
            else:
                shown = repo.list_items(
                    domain=dom, date=target, limit=500, order="time",
                    representatives_only=True, relevant_only=True,
                )
                picks_count = (
                    repo.count_items(domain=dom, date=target, min_score=picks_min)
                    if picks_min > 0
                    else min(picks_n, total_today)
                )

            dates = repo.available_dates(limit=21)
            ctx = _ctx(
                request, cfg, repo,
                nav="today",
                domain=domain,
                view=view,
                date=target,
                is_today=(target == (to_local_date(now_utc_iso()) or "")),
                rows=shown,
                shown_count=len(shown),
                items_json=_items_payload(shown, repo.sources_map()),
                total_today=total_today,
                is_empty=(repo.counts()["total"] == 0),
                picks_count=picks_count,
                dates=dates,
                has_later=any(d["date"] > target for d in dates),
            )
            return templates.TemplateResponse(request, "today.html", ctx)
        finally:
            conn.close()

    # ───────────────────────────── 历史 ─────────────────────────────

    @app.get("/history", response_class=HTMLResponse)
    def page_history(
        request: Request,
        q: str = "",
        domain: str = "all",
        source: str = "",
        date: str = "",
        min_score: float = 0,
        limit: int = 100,
    ) -> HTMLResponse:
        cfg, conn, repo = _open()
        try:
            dom = None if domain == "all" else domain
            query = q.strip()

            if query:
                rows: list[dict[str, Any]] = repo.search(query, limit=500)
                if dom:
                    rows = [r for r in rows if r["domain"] == dom]
                if source:
                    rows = [r for r in rows if r["source_id"] == source]
                if min_score:
                    rows = [r for r in rows if (r["score"] or 0) >= min_score]
                rows = rows[:limit]
            else:
                rows = repo.list_items(
                    domain=dom,
                    date=date or None,
                    source_id=source or None,
                    min_score=min_score or None,
                    limit=limit,
                    order="time",
                )

            ctx = _ctx(
                request, cfg, repo,
                nav="history",
                q=q,
                domain=domain,
                source=source,
                date=date,
                min_score=min_score,
                limit=limit,
                rows=rows,
                mode="search" if query else "browse",
                counts=repo.counts(),
                dates=repo.available_dates(limit=60),
            )
            return templates.TemplateResponse(request, "history.html", ctx)
        finally:
            conn.close()

    # ───────────────────────────── 信息源 ─────────────────────────────

    @app.get("/sources", response_class=HTMLResponse)
    def page_sources(request: Request) -> HTMLResponse:
        cfg, conn, repo = _open()
        try:
            ctx = _ctx(
                request, cfg, repo,
                nav="sources",
                rows=repo.sources_overview(),
                counts=repo.counts(),
            )
            return templates.TemplateResponse(request, "sources.html", ctx)
        finally:
            conn.close()

    # ───────────────────────────── 设置 ─────────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    def page_settings(request: Request) -> HTMLResponse:
        cfg, conn, repo = _open()
        try:
            ctx = _ctx(
                request, cfg, repo,
                nav="settings",
                settings=cfg.settings.data,
                sqlite=sqlite_version(),
                db_path=str(cfg.settings.db_path),
                root=str(cfg.root),
                counts=repo.counts(),
                dates=repo.available_dates(limit=30),
                bundle_repo=cfg.settings.get("bundle.repo") or "",
                proxy=cfg.settings.proxy,
                llm_auto=bool(cfg.settings.get("llm.auto_in_run", False)),
                has_key=has_api_key(),
                key_where=where_is_it(),
                llm_budget=float(cfg.settings.get("llm.daily_budget_cny", 0) or 0),
                budget_pct=min(100, round(
                    (repo.llm_spend()["today"]["cost_cny"]
                     / max(0.0001, float(cfg.settings.get("llm.daily_budget_cny", 0.5) or 0.5))) * 100
                )),
                daily_at=cfg.settings.get("schedule.daily_at", "07:00"),
                max_per_source=int(cfg.settings.get("selection.max_per_source", 3)),
            )
            return templates.TemplateResponse(request, "settings.html", ctx)
        finally:
            conn.close()

    # ───────────────────────────── 写配置（界面改设置） ─────────────────────────────

    @app.post("/api/setting")
    def api_setting(payload: dict[str, Any]) -> dict[str, Any]:
        """把界面上的改动写回 settings.yaml —— 只允许白名单里的键，且保留注释。"""
        cfg, conn, _ = _open()
        conn.close()
        try:
            written = update_setting(cfg.root, str(payload.get("key", "")), payload.get("value"))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "written": written}

    # ───────────────────────────── API Key（凭据管理器） ─────────────────────────────

    @app.post("/api/key")
    def api_save_key(payload: dict[str, Any]) -> dict[str, Any]:
        """把 Key 交给 Windows 凭据管理器。传空串等于删除。"""
        value = str(payload.get("key", "")).strip()
        if value and len(value) < 12:
            raise HTTPException(status_code=400, detail="这个 Key 看起来太短了")
        stored = set_api_key(value)
        return {"ok": True, "stored": stored, "where": where_is_it()}

    @app.delete("/api/key")
    def api_delete_key() -> dict[str, Any]:
        delete_api_key()
        return {"ok": True, "where": where_is_it()}

    @app.post("/api/key/test")
    def api_test_key() -> dict[str, Any]:
        """真发一次最小请求 —— 比只检查格式有意义得多。"""
        cfg, conn, _ = _open()
        conn.close()
        key = get_api_key()
        if not key:
            raise HTTPException(status_code=400, detail="还没配置 API Key")

        base = (cfg.settings.get("llm.base_url") or "https://api.deepseek.com").rstrip("/")
        model = cfg.settings.get("llm.model") or "deepseek-flash"
        try:
            resp = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "回答一个字：好"}],
                    "max_tokens": 8,
                    "thinking": {"type": "disabled"},
                },
                timeout=25.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"连不上 {base}：{type(exc).__name__}") from exc

        if resp.status_code != 200:
            detail = resp.text[:200]
            raise HTTPException(status_code=400, detail=f"API 返回 {resp.status_code}：{detail}")
        return {"ok": True, "model": model}

    # ───────────────────────────── 首次抓取（界面上的按钮） ─────────────────────────────

    run_state: dict[str, Any] = {"running": False, "started_at": "", "error": ""}

    def _run_pipeline(root: Any) -> None:
        """另起一个进程跑完整流水线。

        为什么用子进程而不是直接调函数：抓取会跑好几分钟，
        扔进服务进程里会把它拖住；而且分出去之后，界面进程崩了也不影响抓取。
        """
        import subprocess
        import sys as _sys

        try:
            if getattr(_sys, "frozen", False):
                cmd = [_sys.executable, "run"]          # 打包后：exe 自己带参数再跑一次
            else:
                cmd = [_sys.executable, "-m", "newspipe", "run"]
            subprocess.run(
                cmd,
                cwd=str(root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3600,
            )
        except Exception as exc:  # noqa: BLE001
            run_state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            run_state["running"] = False

    @app.post("/api/run")
    def api_run() -> dict[str, Any]:
        cfg, conn, _ = _open()
        conn.close()
        if run_state["running"]:
            return {"ok": False, "reason": "已经在抓了"}
        run_state.update(running=True, started_at=now_utc_iso(), error="")
        threading.Thread(target=_run_pipeline, args=(cfg.root,), daemon=True).start()
        return {"ok": True}

    @app.get("/api/run/status")
    def api_run_status() -> dict[str, Any]:
        cfg, conn, repo = _open()
        try:
            total = repo.counts()["total"]
        finally:
            conn.close()
        return {**run_state, "total": total}

    return app
