"""HTML 列表页采集器 —— 给没有 RSS 的站点兜底。

配置驱动：在 sources.yaml 里给几个参数就能接一个站，不用为每个站点写代码。
国内游戏站基本都没有 RSS，靠这个补。

    type: html
    url: https://www.gamersky.com/news/
    params:
      base_url: https://www.gamersky.com
      link_pattern: '/news/\\d{6}/\\d+\\.shtml$'
      min_title: 8
      max_items: 60
      date_from_url: '/news/(\\d{4})(\\d{2})/'    # 可选：从 URL 里抠日期
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from ..config import Source
from ..models import RawItem
from ..pipeline.normalize import normalize_title, to_iso_utc, truncate
from .base import Http

try:
    from lxml import html as LH
except ImportError:  # 可选依赖：没装只是用不了 html 类型的源
    LH = None  # type: ignore[assignment]


def _published_from_url(url: str, pattern: re.Pattern[str] | None) -> str | None:
    if pattern is None:
        return None
    match = pattern.search(url)
    if not match:
        return None
    try:
        parts = [int(g) for g in match.groups() if g is not None]
    except (TypeError, ValueError):
        return None
    if not parts:
        return None

    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    try:
        return to_iso_utc(datetime(year, month, day, tzinfo=timezone.utc))
    except ValueError:
        return None


def collect(source: Source, http: Http) -> list[RawItem]:
    if LH is None:
        raise RuntimeError("html 类型的源需要 lxml：pip install lxml")

    params: dict[str, Any] = source.params or {}
    page = http.get_text(source.url)

    try:
        tree = LH.fromstring(page)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"HTML 解析失败：{exc}") from exc

    item_xpath = params.get("item_xpath") or "//a[@href]"
    link_re = re.compile(params["link_pattern"]) if params.get("link_pattern") else None
    date_re = re.compile(params["date_from_url"]) if params.get("date_from_url") else None
    base_url = params.get("base_url") or source.url
    title_attr = params.get("title_attr") or ""
    min_title = int(params.get("min_title", 6))
    max_items = int(params.get("max_items", 60))
    # URL 里只有年月（`/news/202609/`）时，别把整月的文章都算成 1 号那一批；
    # 列表页还会混进"相关阅读"的老链接，用这个天数把它们挡掉。
    max_age_days = int(params.get("max_age_days", 0))
    cutoff = (
        datetime.now(timezone.utc).timestamp() - max_age_days * 86400 if max_age_days else None
    )

    out: list[RawItem] = []
    seen: set[str] = set()

    for node in tree.xpath(item_xpath):
        href = (node.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        url = urljoin(base_url, href)
        if url.startswith("//"):
            url = "https:" + url
        if link_re and not link_re.search(url):
            continue
        if url in seen:
            continue

        title = normalize_title((node.get(title_attr) if title_attr else "") or node.text_content() or "")
        if len(title) < min_title:
            continue

        # date_from_url 只用来过滤老链接，**不用来当发布日期**：
        # 列表页 URL 通常只有年月（/news/202609/），拿它当日期会把整月的文章
        # 全挤到 1 号。发布时间留空，入库时回退成抓取时间 —— 列表页本来
        # 就是"当前最新"，这比一个假的月初日期接近事实。
        url_date = _published_from_url(url, date_re)
        if url_date and cutoff is not None:
            from ..pipeline.normalize import parse_iso

            dt = parse_iso(url_date)
            if dt is not None and dt.timestamp() < cutoff:
                continue

        seen.add(url)
        out.append(
            RawItem(
                source_id=source.id,
                domain=source.domain,
                url=url,
                title=truncate(title, 200),
                published_at=None,
                lang="zh",
            )
        )
        if len(out) >= max_items:
            break

    if not out:
        raise RuntimeError("列表页没匹配到任何条目（link_pattern 或 item_xpath 需要调整）")

    return out
