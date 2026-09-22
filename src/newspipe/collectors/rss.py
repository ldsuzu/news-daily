"""RSS / Atom 采集器 —— 大多数源都走这条路。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import feedparser

from ..config import Source
from ..models import RawItem
from ..pipeline.normalize import clean_text, normalize_title, to_iso_utc, truncate
from .base import Http

MAX_ITEMS = 60
EXCERPT_CHARS = 600
CONTENT_CHARS = 20000


def looks_english(text: str) -> bool:
    """粗判：ASCII 字母占比高就是英文标题。"""
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_letters = [c for c in letters if c.isascii()]
    return len(ascii_letters) / len(letters) > 0.6


def entry_time(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def entries_to_items(feed: Any, source: Source, *, limit: int = MAX_ITEMS) -> list[RawItem]:
    """把 feedparser 的结果统一成 RawItem。arxiv 的 Atom 也走这里。"""
    lang = (feed.feed.get("language") or "").strip().lower()[:12]
    out: list[RawItem] = []

    for entry in feed.entries[:limit]:
        url = (entry.get("link") or "").strip()
        title = normalize_title(entry.get("title"))
        if not url or not title:
            continue

        excerpt = clean_text(entry.get("summary") or entry.get("description") or "")

        content = ""
        blocks = entry.get("content") or []
        if blocks:
            try:
                content = clean_text(blocks[0].get("value", ""))
            except (AttributeError, IndexError, TypeError):
                content = ""

        is_en = looks_english(title)
        out.append(
            RawItem(
                source_id=source.id,
                domain=source.domain,
                url=url,
                title=title,
                title_en=title if is_en else "",
                published_at=to_iso_utc(entry_time(entry)),
                excerpt=truncate(excerpt, EXCERPT_CHARS),
                content_text=truncate(content, CONTENT_CHARS),
                lang=lang or ("en" if is_en else "zh"),
            )
        )

    return out


def parse_feed(raw: bytes, source: Source) -> list[RawItem]:
    feed = feedparser.parse(raw)
    if not feed.entries and getattr(feed, "bozo", 0):
        reason = getattr(feed, "bozo_exception", "unknown")
        raise RuntimeError(f"RSS 解析失败：{reason}")
    return entries_to_items(feed, source)


def collect(source: Source, http: Http) -> list[RawItem]:
    return parse_feed(http.get_bytes(source.url), source)


def collect_rsshub(source: Source, http: Http, base: str) -> list[RawItem]:
    """没有官方 RSS 的源，经自建 RSSHub 转一手。"""
    if not base:
        raise RuntimeError("该源需要 RSSHub，但 settings.rsshub.base 未配置")
    url = base.rstrip("/") + "/" + source.url.lstrip("/")
    return parse_feed(http.get_bytes(url), source)
