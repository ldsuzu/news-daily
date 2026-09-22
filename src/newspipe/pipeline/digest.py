"""生成每日 Markdown 日报 —— archive/YYYY-MM-DD-<domain>.md

界面是用来读的，归档是用来留的：纯文本、可 grep、可进 git，且不依赖这个软件还在不在。
版式对应界面上的报纸版式：报头 → 头条 → 精选 → 完整清单。
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Any

from ..config import Config
from ..pipeline.normalize import parse_iso
from ..storage.repo import Repo
from .select import select_picks

WEEKDAYS = "一二三四五六日"


def _hhmm(value: str | None) -> str:
    dt = parse_iso(value)
    return dt.astimezone().strftime("%H:%M") if dt else "--:--"


def _date_label(value: str) -> str:
    try:
        d = _date.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    return f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"


def _one_line(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _summary_of(item: dict[str, Any], limit: int = 160) -> str:
    return _one_line(item.get("summary_cn") or item.get("excerpt") or "", limit)


def _link(item: dict[str, Any]) -> str:
    return f"[{_one_line(item.get('title') or '', 90)}]({item.get('url')})"


def render_digest(
    *,
    date: str,
    domain_name: str,
    all_items: list[dict[str, Any]],
    picks: list[dict[str, Any]],
    picks_n: int,
    source_map: dict[str, dict[str, Any]],
    generated_at: str,
) -> str:
    src = lambda item: source_map.get(item.get("source_id", ""), {}).get("name", item.get("source_id", ""))
    domains_covered = len({i.get("source_id") for i in all_items})

    lines: list[str] = []
    lines.append(f"# 每日简报 · {domain_name} · {_date_label(date)}")
    lines.append("")
    lines.append(
        f"> 当天入库 **{len(all_items)}** 条 · 精选 **{len(picks)}** 条 · "
        f"覆盖 {domains_covered} 个来源 · 生成于 {_hhmm(generated_at)}"
    )
    lines.append("")
    lines.append(
        "> 正文已存本地库，界面里可离线精读；本文件是纯文本归档，不依赖那个软件还在不在。"
    )
    lines.append("")

    if not all_items:
        lines.append("这一天没有内容。")
        lines.append("")
        return "\n".join(lines)

    # ── 头条 ──
    lead = picks[0] if picks else all_items[0]
    lines.append("---")
    lines.append("")
    lines.append("## 头条")
    lines.append("")
    lines.append(f"### {_link(lead)}")
    lines.append("")
    lines.append(f"`{src(lead)}` · {_hhmm(lead.get('published_at'))} · 重要度 **{lead.get('score', 0):.1f}**")
    lines.append("")
    summary = _summary_of(lead, 400)
    if summary:
        lines.append(summary)
        lines.append("")
    if lead.get("title_en") and lead["title_en"] != lead.get("title"):
        lines.append(f"*{_one_line(lead['title_en'], 200)}*")
        lines.append("")

    # ── 精选 ──
    rest = picks[1:]
    if rest:
        lines.append("---")
        lines.append("")
        lines.append(f"## 精选（{len(picks)} 条）")
        lines.append("")
        lines.append("| # | 标题 | 来源 | 时间 | 分数 |")
        lines.append("|---:|---|---|---|---:|")
        for n, item in enumerate(rest, start=2):
            note = ""
            if item.get("cluster_size", 1) > 1:
                note = f"（同事件另有 {item['cluster_size'] - 1} 条）"
            lines.append(
                f"| {n} | {_link(item)}{note} | {src(item)} | {_hhmm(item.get('published_at'))} "
                f"| {item.get('score', 0):.1f} |"
            )
        lines.append("")

    # ── 完整清单 ──
    lines.append("---")
    lines.append("")
    lines.append(f"## 当天全部条目（{len(all_items)} 条，按时间倒序）")
    lines.append("")
    lines.append("| 时间 | 来源 | 标题 | 分数 |")
    lines.append("|---|---|---|---:|")
    for item in all_items:
        lines.append(
            f"| {_hhmm(item.get('published_at'))} | {src(item)} | {_link(item)} "
            f"| {item.get('score', 0):.1f} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"*当天共 {len(all_items)} 条入库，一条都没有丢；完整正文见本地库（`newspipe search <关键词>` 可检索全部历史）。*"
    )
    lines.append("")

    return "\n".join(lines)


def write_digests(config: Config, repo: Repo, date: str, *, generated_at: str) -> list[Path]:
    """每个有内容的领域写一份。返回写出的文件路径。"""
    picks_n = int(config.settings.get("selection.picks_per_domain", 10))
    source_map = repo.sources_map()
    archive_dir = config.settings.path("archive")
    written: list[Path] = []

    for domain in config.domains:
        all_items = repo.list_items(
            domain=domain.id, date=date, limit=2000, order="time", representatives_only=False
        )
        if not all_items:
            continue

        reps = repo.list_items(
            domain=domain.id, date=date, limit=200, order="score", representatives_only=True
        )
        picks = select_picks(
            reps,
            picks_n,
            max_per_source=int(config.settings.get("selection.max_per_source", 3)),
        )

        markdown = render_digest(
            date=date,
            domain_name=domain.name,
            all_items=all_items,
            picks=picks,
            picks_n=picks_n,
            source_map=source_map,
            generated_at=generated_at,
        )
        path = archive_dir / f"{date}-{domain.id}.md"
        path.write_text(markdown, encoding="utf-8")
        written.append(path)

    return written
