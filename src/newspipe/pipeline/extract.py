"""正文抽取 —— 「不开 VPN 也能读」的物理基础。

trafilatura 是可选依赖：装了就抽正文，没装就退回 RSS 摘要，程序绝不因此起不来。
"""

from __future__ import annotations

from typing import Any

from ..models import RawItem
from .normalize import clean_text, truncate

MAX_CONTENT_CHARS = 20000


def trafilatura_available() -> bool:
    try:
        import trafilatura  # noqa: F401
    except ImportError:
        return False
    return True


def extract_from_html(html: str, *, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """HTML → 正文纯文本。抽不出来就返回空串，由调用方决定怎么降级。"""
    if not html:
        return ""
    try:
        import trafilatura
    except ImportError:
        return ""

    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            url=None,
        )
    except Exception:  # noqa: BLE001 —— 抽取失败不该炸掉整轮抓取
        return ""

    return truncate(clean_text(text or ""), max_chars)


def extract_for_url(url: str, http: Any, *, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """抓页面并抽正文。网络或解析失败都返回空串。"""
    try:
        html = http.get_text(url)
    except Exception:  # noqa: BLE001
        return ""
    return extract_from_html(html, max_chars=max_chars)


def needs_extraction(item: RawItem, *, min_chars: int = 400) -> bool:
    """正文太薄就值得补抓一次。RSS 摘要通常只有百来字。"""
    return len(item.content_text or "") < min_chars


def upgrade_content(item: RawItem, html: str) -> bool:
    """用抽到的正文替换掉现有内容，仅在抽到的更长时才替换。"""
    text = extract_from_html(html)
    if len(text) > len(item.content_text or ""):
        item.content_text = text
        return True
    return False
