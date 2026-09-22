"""采集层：把各家的原始条目统一成 RawItem。

加一种源类型 = 加一个适配器函数并在这里注册，别处不用改。
"""

from __future__ import annotations

from ..config import Config, Source
from ..models import FetchResult, RawItem
from . import api, rss
from .base import Http


def collect_source(source: Source, http: Http, config: Config | None = None) -> FetchResult:
    """抓一个源。任何异常都被收敛成 ok=False 的结果 —— 单源失败不能拖垮整轮。"""
    try:
        if source.type == "rss":
            items = rss.collect(source, http)
        elif source.type == "rsshub":
            base = (config.settings.get("rsshub.base") if config else "") or ""
            items = rss.collect_rsshub(source, http, base)
        elif source.type == "api":
            items = api.collect(source, http)
        else:
            raise ValueError(f"未知的源类型：{source.type}")
        return FetchResult(source_id=source.id, ok=True, count=len(items), items=items)
    except Exception as exc:  # noqa: BLE001 —— 故意兜住一切，错误信息原样带回去展示
        return FetchResult(
            source_id=source.id,
            ok=False,
            count=0,
            error=f"{type(exc).__name__}: {exc}",
        )


__all__ = ["collect_source", "Http", "RawItem", "FetchResult"]
