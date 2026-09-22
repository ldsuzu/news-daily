"""采集与入库之间流转的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawItem:
    """采集器抓回来的一条原始条目。

    这是本地与海外分身之间传递的同一套结构，也是 bundle JSON 里的 item。
    """

    source_id: str
    domain: str
    url: str
    title: str
    published_at: str | None = None      # ISO8601 UTC 字符串
    excerpt: str = ""                    # 源自带的短摘要
    content_text: str = ""               # 已抽取的正文（可能为空，M1 再补抽取）
    title_en: str = ""                   # 保留英文原标题
    lang: str = ""
    fetched_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "domain": self.domain,
            "url": self.url,
            "title": self.title,
            "published_at": self.published_at,
            "excerpt": self.excerpt,
            "content_text": self.content_text,
            "title_en": self.title_en,
            "lang": self.lang,
            "fetched_at": self.fetched_at,
            "extra": self.extra,
        }


@dataclass
class FetchResult:
    """单个源一次抓取的结果。"""

    source_id: str
    ok: bool
    count: int = 0
    error: str = ""
    items: list[RawItem] = field(default_factory=list)
