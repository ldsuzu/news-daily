"""界面路由。

版式按原型变体 A（报纸版式）：报头 → 头条 → 三栏次条 → 多栏列表。
四屏：今日 / 历史 / 信息源 / 设置。
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, load_config
from ..pipeline.normalize import now_utc_iso, parse_iso, to_local_date
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


templates.env.filters.update(
    hhmm=_hhmm,
    date_label=_date_label,
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
            "picks_min_score": float(cfg.settings.get("selection.picks_min_score", 8)),
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
                # 精选 = 按重要度取前 N 条（不是"够 N 分才进来"），阈值只是可选下界
                shown = repo.list_items(domain=dom, date=target, limit=picks_n, order="score")
                if picks_min > 0:
                    shown = [r for r in shown if (r["score"] or 0) >= picks_min]
                picks_count = len(shown)
            else:
                shown = repo.list_items(domain=dom, date=target, limit=500, order="time")
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
                lead=shown[0] if shown else None,
                secondary=shown[1:4],
                more=shown[4:],
                shown_count=len(shown),
                total_today=total_today,
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
                dates=repo.available_dates(limit=7),
                bundle_repo=cfg.settings.get("bundle.repo") or "",
                proxy=cfg.settings.proxy,
            )
            return templates.TemplateResponse(request, "settings.html", ctx)
        finally:
            conn.close()

    return app
