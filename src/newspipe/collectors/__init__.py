"""采集层：把各家的原始条目统一成 RawItem。

加一种源类型 = 加一个适配器函数并在这里注册，别处不用改。
"""

from __future__ import annotations

import time

from ..config import Config, Source
from ..models import FetchResult, RawItem
from . import api, rss
from . import html as html_mod
from .base import Http

# 网站在限速、风控、偶发 5xx 上都是间歇性的（B站的风控尤其如此），
# 一次失败就记一笔太难看了，退避重试一轮。
RETRIES = 2
BACKOFF_SECONDS = 1.5


def _collect_once(source: Source, http: Http, config: Config | None) -> list[RawItem]:
    if source.type == "rss":
        return rss.collect(source, http)
    if source.type == "rsshub":
        base = (config.settings.get("rsshub.base") if config else "") or ""
        return rss.collect_rsshub(source, http, base)
    if source.type == "api":
        return api.collect(source, http)
    if source.type == "html":
        return html_mod.collect(source, http)
    raise ValueError(f"未知的源类型：{source.type}")


def collect_source(
    source: Source,
    http: Http,
    config: Config | None = None,
    *,
    retries: int = RETRIES,
) -> FetchResult:
    """抓一个源，失败自动退避重试。

    任何异常最终都被收敛成 ok=False 的结果 —— 单源失败不能拖垮整轮。
    """
    last_error = ""

    for attempt in range(retries + 1):
        try:
            items = _collect_once(source, http, config)
            return FetchResult(source_id=source.id, ok=True, count=len(items), items=items)
        except Exception as exc:  # noqa: BLE001 —— 故意兜住一切，错误信息原样带回去展示
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(BACKOFF_SECONDS * (attempt + 1))

    return FetchResult(source_id=source.id, ok=False, count=0, error=last_error)


__all__ = ["collect_source", "Http", "RawItem", "FetchResult"]
