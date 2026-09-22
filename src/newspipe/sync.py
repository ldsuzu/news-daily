"""拉取海外分身的 bundle —— 四层镜像回退（技术框架 §3.4）。

GitHub 在国内时通时不通，所以一条路走不通就换下一条，全都走不通也不该给你一个空白页。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import Config
from .collectors.base import Http
from .pipeline.bundle import load_bundle

MIRROR_LABELS = ("jsDelivr", "GitHub API", "raw.githubusercontent")


def mirror_urls(config: Config, date: str) -> list[str]:
    repo = config.settings.get("bundle.repo") or ""
    if not repo:
        return []
    branch = config.settings.get("bundle.branch", "main")
    path = config.settings.get("bundle.path", "bundles")
    templates = config.settings.get("bundle.mirrors", []) or []
    return [
        str(t).format(repo=repo, branch=branch, path=path, date=date)
        for t in templates
    ]


def _headers(config: Config, url: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    if "api.github.com" in url:
        # 否则 GitHub 返回的是带 base64 的 JSON 信封，不是文件内容
        headers["Accept"] = "application/vnd.github.raw"
    token = config.settings.get("bundle.token") or os.environ.get("NEWSPIPE_GH_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def pull_remote(config: Config, http: Http, date: str) -> dict[str, Any]:
    """按镜像顺序尝试。返回 {ok, payload, label, tried}。"""
    tried: list[tuple[str, str]] = []

    for url in mirror_urls(config, date):
        try:
            text = http.get_text(url, headers=_headers(config, url))
            payload = json.loads(text)
        except Exception as exc:  # noqa: BLE001 —— 挨个试，失败就下一个
            tried.append((url, f"{type(exc).__name__}: {str(exc)[:80]}"))
            continue

        if isinstance(payload, dict) and "items" in payload:
            return {"ok": True, "payload": payload, "label": url, "tried": tried}

        tried.append((url, "响应不是 bundle（缺少 items 字段）"))

    return {"ok": False, "payload": None, "label": "", "tried": tried}


def fallback_local(bundles_dir: Path, date: str) -> tuple[Path | None, str]:
    """全部镜像都失败时，用本地留存的副本：先今天的，再最近一天的。"""
    exact = bundles_dir / f"{date}.json"
    if exact.exists():
        return exact, "本地留存（当天）"

    candidates = sorted(bundles_dir.glob("*.json"), reverse=True)
    if candidates:
        return candidates[0], f"本地留存（{candidates[0].stem}，非当天）"

    return None, ""


def read_payload(path: Path) -> dict[str, Any] | None:
    try:
        return load_bundle(path)
    except Exception:  # noqa: BLE001
        return None
