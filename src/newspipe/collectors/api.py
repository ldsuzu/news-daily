"""官方 API 采集器：arXiv / Hacker News / Steam。

每个适配器只做一件事：把该家的 JSON/XML 翻译成 RawItem 列表。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import feedparser

from ..config import Source
from ..models import RawItem
from ..pipeline.normalize import clean_text, normalize_title, to_iso_utc, truncate
from .base import Http
from .rss import looks_english


def collect(source: Source, http: Http) -> list[RawItem]:
    adapter = source.adapter or source.type
    handler = ADAPTERS.get(adapter)
    if handler is None:
        raise RuntimeError(f"未实现的 API 适配器：{adapter}")
    return handler(source, http)


# ─────────────────────────── arXiv ───────────────────────────

def _arxiv(source: Source, http: Http) -> list[RawItem]:
    """arXiv 返回 Atom，所以复用 feedparser，只是 URL 需要带查询参数。"""
    params = {k: v for k, v in source.params.items() if v not in (None, "")}
    raw = http.get_bytes(source.url, params=params)
    from .rss import entries_to_items

    feed = feedparser.parse(raw)
    if not feed.entries and getattr(feed, "bozo", 0):
        raise RuntimeError(f"arXiv 解析失败：{getattr(feed, 'bozo_exception', 'unknown')}")
    return entries_to_items(feed, source)


# ─────────────────────── Hacker News ───────────────────────

def _hackernews(source: Source, http: Http) -> list[RawItem]:
    base = source.url.rstrip("/")
    top_n = int(source.params.get("top", 60))
    keywords = [str(k).lower() for k in source.params.get("keywords", [])]

    story_ids = (http.get_json(f"{base}/topstories.json") or [])[:top_n]
    if not story_ids:
        return []

    payloads = http.get_json_many([f"{base}/item/{sid}.json" for sid in story_ids])

    out: list[RawItem] = []
    for sid, data in zip(story_ids, payloads):
        if not isinstance(data, dict):
            continue
        title = normalize_title(data.get("title"))
        if not title:
            continue
        if keywords and not any(k in title.lower() for k in keywords):
            continue

        # 讨论帖本身没有外链，用 HN 的讨论页当链接
        url = data.get("url") or f"https://news.ycombinator.com/item?id={sid}"
        ts = data.get("time")
        published = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc) if isinstance(ts, (int, float)) else None
        )
        text = clean_text(data.get("text") or "")

        out.append(
            RawItem(
                source_id=source.id,
                domain=source.domain,
                url=url,
                title=title,
                title_en=title if looks_english(title) else "",
                published_at=to_iso_utc(published),
                excerpt=truncate(text, 600),
                lang="en" if looks_english(title) else "zh",
                extra={"hn_id": sid, "points": data.get("score"), "comments": data.get("descendants")},
            )
        )
    return out


# ─────────────────────────── Steam ───────────────────────────

def _steam(source: Source, http: Http) -> list[RawItem]:
    appids = source.params.get("appids") or []
    count = int(source.params.get("count", 20))
    out: list[RawItem] = []

    for appid in appids:
        data = http.get_json(
            # 注意：这个接口要的是单数 appid（复数会直接 400）
            source.url,
            params={"appid": appid, "count": count, "format": "json", "maxlength": 0},
        )
        appnews = (data or {}).get("appnews") or {}
        for news in appnews.get("newsitems", [])[:count]:
            title = normalize_title(news.get("title"))
            if not title:
                continue
            gid = news.get("gid")
            url = news.get("url") or f"https://store.steampowered.com/news/app/{appid}/view/{gid}"
            ts = news.get("date")
            published = (
                datetime.fromtimestamp(int(ts), tz=timezone.utc) if isinstance(ts, (int, float)) else None
            )
            body = clean_text(news.get("contents") or "")

            out.append(
                RawItem(
                    source_id=source.id,
                    domain=source.domain,
                    url=url,
                    title=title,
                    title_en=title if looks_english(title) else "",
                    published_at=to_iso_utc(published),
                    excerpt=truncate(body, 600),
                    content_text=truncate(body, 20000),
                    lang="en" if looks_english(title) else "zh",
                    extra={"appid": appid, "feedlabel": news.get("feedlabel", "")},
                )
            )
    return out


# ─────────────────────────── B站 ───────────────────────────

def _bilibili(source: Source, http: Http) -> list[RawItem]:
    """B站排行榜（rid=4 是游戏区）。用来补游戏侧——国内游戏媒体基本没有 RSS。"""
    params = {
        "rid": source.params.get("rid", 4),
        "type": source.params.get("type", "all"),
    }
    data = http.get_json(source.url, params=params)
    # 注意：这里**不要**加 Referer —— 实测带上它会触发 B站风控返回 -352，
    # 而不带任何自定义头反而稳定返回 code=0。

    code = (data or {}).get("code")
    if code != 0:
        raise RuntimeError(f"B站接口返回 code={code}：{(data or {}).get('message', '')}")

    out: list[RawItem] = []
    for entry in ((data.get("data") or {}).get("list") or [])[:50]:
        title = normalize_title(entry.get("title"))
        bvid = entry.get("bvid")
        if not title or not bvid:
            continue

        ts = entry.get("pubdate")
        published = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc) if isinstance(ts, (int, float)) else None
        )
        stat = entry.get("stat") or {}

        out.append(
            RawItem(
                source_id=source.id,
                domain=source.domain,
                url=f"https://www.bilibili.com/video/{bvid}",
                title=title,
                published_at=to_iso_utc(published),
                excerpt=truncate(clean_text(entry.get("desc") or ""), 600),
                lang="zh",
                extra={
                    "bvid": bvid,
                    "up": (entry.get("owner") or {}).get("name", ""),
                    "views": stat.get("view"),
                },
            )
        )
    return out


ADAPTERS = {
    "arxiv": _arxiv,
    "hackernews": _hackernews,
    "steam": _steam,
    "bilibili": _bilibili,
}
