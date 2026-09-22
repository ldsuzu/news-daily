"""配置加载：settings.yaml / domains.yaml / sources.yaml 一个入口读进来。

设计意图：所有可调数字都在 YAML 里，代码里不写死。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    """定位项目根目录：优先环境变量，其次按本文件位置推断（src/newspipe/config.py）。"""
    env = os.environ.get("NEWSPIPE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


@dataclass
class Source:
    id: str
    name: str
    domain: str
    type: str
    url: str
    needs_proxy: bool = False
    weight: float = 1.0
    enabled: bool = True
    adapter: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_remote(self) -> bool:
        """True = 由海外采集分身负责（见技术框架 §3.1）。"""
        return self.needs_proxy


@dataclass
class Domain:
    id: str
    name: str
    short: str = ""
    color: str = ""


class Settings:
    """点号路径读取 settings.yaml，例如 settings.get("network.timeout_seconds", 20)。"""

    def __init__(self, root: Path, data: dict[str, Any]) -> None:
        self.root = root
        self.data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, key: str) -> Path:
        """把 settings.paths.<key> 解析成绝对路径，并确保父目录存在。"""
        raw = self.get(f"paths.{key}")
        if raw is None:
            raise KeyError(f"settings.paths.{key} 未配置")
        p = Path(raw)
        if not p.is_absolute():
            p = self.root / p
        return p

    def ensure_dirs(self) -> None:
        for key in ("data_dir", "contents", "bundles", "archive"):
            try:
                self.path(key).mkdir(parents=True, exist_ok=True)
            except KeyError:
                continue

    @property
    def db_path(self) -> Path:
        return self.path("db")

    @property
    def proxy(self) -> str:
        return (self.get("network.proxy") or "").strip()

    @property
    def user_agent(self) -> str:
        return self.get("network.user_agent") or "NewsPipe/0.1"


@dataclass
class Config:
    root: Path
    settings: Settings
    domains: list[Domain]
    sources: list[Source]

    def domain(self, domain_id: str) -> Domain | None:
        for d in self.domains:
            if d.id == domain_id:
                return d
        return None

    def source(self, source_id: str) -> Source | None:
        for s in self.sources:
            if s.id == source_id:
                return s
        return None

    def enabled_sources(
        self,
        *,
        mode: str = "standalone",
        source_ids: list[str] | None = None,
    ) -> list[Source]:
        """按运行模式挑源：standalone 抓本地可达源，collector 抓墙外源。

        这样两边零重叠，本地合并时天然不冲突（见技术框架 §3.1）。
        """
        out = []
        for s in self.sources:
            if not s.enabled:
                continue
            if source_ids and s.id not in source_ids:
                continue
            if mode == "collector" and not s.is_remote:
                continue
            if mode == "standalone" and s.is_remote:
                continue
            out.append(s)
        return out


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(root: Path | None = None) -> Config:
    root = root or _project_root()
    cfg_dir = root / "config"

    settings_data = _read_yaml(cfg_dir / "settings.yaml")
    settings = Settings(root, settings_data)

    domains = [
        Domain(
            id=d["id"],
            name=d.get("name", d["id"]),
            short=d.get("short", d.get("name", d["id"])),
            color=d.get("color", ""),
        )
        for d in _read_yaml(cfg_dir / "domains.yaml").get("domains", [])
    ]

    sources = []
    for raw in _read_yaml(cfg_dir / "sources.yaml").get("sources", []):
        sources.append(
            Source(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                domain=raw.get("domain", ""),
                type=raw.get("type", "rss"),
                url=raw.get("url", ""),
                needs_proxy=bool(raw.get("needs_proxy", False)),
                weight=float(raw.get("weight", 1.0)),
                enabled=bool(raw.get("enabled", True)),
                adapter=raw.get("adapter", ""),
                params=raw.get("params", {}) or {},
            )
        )

    return Config(root=root, settings=settings, domains=domains, sources=sources)
