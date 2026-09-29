"""配置加载：settings.yaml / domains.yaml / sources.yaml 一个入口读进来。

设计意图：所有可调数字都在 YAML 里，代码里不写死。
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    """定位「工作目录」—— config/ 和 data/ 都放在这里。

    三种情况：
    - 环境变量 NEWSPIPE_ROOT 显式指定（测试和脚本用）
    - 打包成 exe 后：**exe 所在目录**。这是便携模式 —— 拷走整个文件夹就能用，
      数据跟着一起走，不会散落到 AppData 里找不到。
    - 开发时：从 src/newspipe/config.py 往上两级。
    """
    env = os.environ.get("NEWSPIPE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _bundled_dir() -> Path:
    """只读资源目录：打包后是解压出来的 _MEIPASS，开发时就是源码根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parents[2]


def ensure_config(root: Path) -> None:
    """首次运行（或把便携文件夹拷到新机器）时，释放一份可编辑的默认配置。

    打包后的 config/ 藏在只读资源里，用户改不到；所以 exe 旁边没有 config/ 时，
    先复制一份出来。已有配置永远不覆盖。
    """
    target = root / "config"
    if target.exists():
        return
    source = _bundled_dir() / "config"
    if source.exists() and source.resolve() != target.resolve():
        try:
            shutil.copytree(source, target)
        except OSError:
            pass


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
    extract: bool = True          # 文章页能否抽出正文；SPA 站点设 false，别浪费请求
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
    ensure_config(root)          # 便携拷贝到新机器时，先把默认配置释放出来
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
                extract=bool(raw.get("extract", True)),
                params=raw.get("params", {}) or {},
            )
        )

    return Config(root=root, settings=settings, domains=domains, sources=sources)


# 允许界面写入的设置项白名单 —— 只有这些能通过 /api/setting 改。
# 其余（信息源、价格表等）仍然只认手改文件，免得界面误操作把配置搞乱。
WRITABLE_SETTINGS: dict[str, str] = {
    "llm.enabled": "bool",
    "llm.auto_in_run": "bool",
    "llm.model": "str",
    "llm.daily_budget_cny": "float",
    "llm.batch_size": "int",
    "llm.merge_events": "bool",
    "selection.picks_per_domain": "int",
    "selection.max_per_source": "int",
    "selection.picks_min_score": "float",
    "schedule.daily_at": "str",
    "network.proxy": "str",
    "bundle.repo": "str",
}


def _yaml_scalar(value: Any, kind: str) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    if kind == "float":
        return str(float(value))
    text = str(value)
    # 只有真会破坏 YAML 的才加引号。值中间出现的 '-'、'>'、'=' 都是安全的，
    # 一并加引号反而让配置文件看起来像是被人手改过
    risky = any(ch in text for ch in ':#{}[],&*?|>"\'')
    if text == "" or text != text.strip() or risky or text[:1] in ("-", "!", "%", "@", "`"):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def update_setting(root: Path, dotted: str, value: Any) -> str:
    """改 settings.yaml 里的一个点号键，**保留原有注释与排版**。

    用行级替换而不是 yaml.dump —— 后者会把文件里所有注释都吃掉，
    而这个文件的注释就是它的说明书。
    """
    if dotted not in WRITABLE_SETTINGS:
        raise KeyError(f"{dotted} 不在可写白名单里")

    section, _, field = dotted.partition(".")
    kind = WRITABLE_SETTINGS[dotted]
    path = root / "config" / "settings.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")

    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_section:
            if stripped.startswith(section + ":"):
                in_section = True
            continue
        # 走到下一个顶层键，说明这个 section 里没有该字段
        if stripped and not line.startswith((" ", "\t")):
            break
        if stripped.startswith(field + ":"):
            indent = line[: len(line) - len(line.lstrip())]
            comment = ""
            if "#" in line:
                comment = "  #" + line.split("#", 1)[1].rstrip()
            lines[i] = f"{indent}{field}: {_yaml_scalar(value, kind)}{comment}"
            path.write_text("\n".join(lines), encoding="utf-8")
            return lines[i].strip()

    raise KeyError(f"settings.yaml 里找不到 {dotted}")
