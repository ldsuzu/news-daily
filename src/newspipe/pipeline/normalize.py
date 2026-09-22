"""规范化：URL、标题、文本、时间。

这个模块是**本地与海外分身共用的纯函数集合** —— 两边用同一段代码算 url_hash，
合并去重才不会出现歧义（见技术框架 §3.3）。
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 营销/追踪参数，一律剥掉
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand",
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hsCtaTracking",
    "ref", "referrer", "source", "spm", "share_token", "share_source",
    "from", "from_source", "vd_source", "buvid", "wxfid",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# 中英文之间不需要的空格在标题里很常见，先不动，只做基础清理


def clean_text(raw: str | None) -> str:
    """去 HTML 标签、解实体、压空白。"""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    return _WS_RE.sub(" ", text).strip()


def normalize_title(title: str | None) -> str:
    """标题归一：全角空格、零宽字符、多余空白。"""
    if not title:
        return ""
    t = title.replace("\u3000", " ").replace("\u200b", "")
    t = clean_text(t)
    return _WS_RE.sub(" ", t).strip()


def canonical_url(url: str) -> str:
    """去掉追踪参数、fragment，统一大小写与尾部斜杠，得到稳定可比的 URL。"""
    if not url:
        return ""
    parts = urlsplit(url.strip())

    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query.sort()

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    """条目的唯一指纹。本地与海外用同一函数计算 → 合并去重零歧义。"""
    return "sha256:" + hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def title_key(title: str) -> str:
    """粗粒度标题指纹，用于近似重复的第一层判断（第二层 SimHash 在 M1）。"""
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalize_title(title).lower())
    return t[:120]


def to_iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_utc_iso() -> str:
    return to_iso_utc(datetime.now(timezone.utc)) or ""


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_local_date(value: str | None) -> str | None:
    """ISO8601 UTC → 本地时区的 YYYY-MM-DD，用来分日归档。"""
    dt = parse_iso(value)
    if dt is None:
        return None
    return dt.astimezone().strftime("%Y-%m-%d")


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
