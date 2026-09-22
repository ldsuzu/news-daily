"""bundle JSON —— 本地与海外采集分身之间**唯一的接口**（见技术框架 §3.3）。

两侧都用这里的 build / load，字段和 url_hash 的算法因此不可能漂移。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import FetchResult, RawItem
from .normalize import now_utc_iso

BUNDLE_VERSION = 1


def build_bundle(date: str, results: list[FetchResult]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    for r in results:
        sources.append(
            {
                "id": r.source_id,
                "ok": r.ok,
                "count": r.count,
                "error": r.error or None,
            }
        )
        for it in r.items:
            items.append(it.to_dict())

    return {
        "version": BUNDLE_VERSION,
        "date": date,
        "generated_at": now_utc_iso(),
        "mode": "collector",
        "sources": sources,
        "items": items,
    }


def write_bundle(out_dir: Path, date: str, results: list[FetchResult]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date}.json"
    payload = build_bundle(date, results)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_bundle(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def items_from_payload(payload: dict[str, Any]) -> list[RawItem]:
    """bundle JSON → RawItem。本地 sync 时用。"""
    out: list[RawItem] = []
    for raw in payload.get("items", []):
        out.append(
            RawItem(
                source_id=raw.get("source_id", ""),
                domain=raw.get("domain", ""),
                url=raw.get("url", ""),
                title=raw.get("title", ""),
                title_en=raw.get("title_en", ""),
                published_at=raw.get("published_at"),
                fetched_at=raw.get("fetched_at"),
                lang=raw.get("lang", ""),
                excerpt=raw.get("excerpt", ""),
                content_text=raw.get("content_text", ""),
                extra=raw.get("extra") or {},
            )
        )
    return out


def bundle_date(payload: dict[str, Any]) -> str:
    value = payload.get("date")
    if value:
        return str(value)
    return datetime.now().strftime("%Y-%m-%d")
