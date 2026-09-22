"""M0 的核心契约测试。

这里只钉最要紧的东西：url_hash 的稳定性（本地与海外分身靠它合并去重）、
bundle 的往返一致性（唯一的跨机器接口）、以及打分的基本序关系。
"""

from __future__ import annotations

from datetime import datetime, timezone

from newspipe.config import Source
from newspipe.models import FetchResult, RawItem
from newspipe.pipeline.bundle import build_bundle, items_from_payload
from newspipe.pipeline.normalize import (
    canonical_url,
    normalize_title,
    strip_tracking,
    title_key,
    to_local_date,
    url_hash,
)
from newspipe.pipeline.score import score_item


# ───────────────────────────── 规范化 ─────────────────────────────

def test_canonical_url_strips_tracking_and_www():
    a = canonical_url("https://www.IGN.com/articles/foo/?utm_source=x&id=1#frag")
    b = canonical_url("https://ign.com/articles/foo?id=1")
    assert a == b


def test_canonical_url_sorts_query():
    assert canonical_url("https://x.com/p?b=2&a=1") == canonical_url("https://x.com/p?a=1&b=2")


def test_dedup_url_and_display_url_are_different_jobs():
    """去重靠 canonical_url（去 www），展示靠 strip_tracking（保留 host）。

    真实教训：游研社的证书只对 www.yystv.cn 有效，把 www 去掉后链接直接证书错误。
    两者混用会把能点的链接弄坏。
    """
    raw = "https://www.yystv.cn/p/14411?utm_source=rss&utm_medium=feed"

    assert canonical_url(raw) == "https://yystv.cn/p/14411"          # 只给去重用
    assert strip_tracking(raw) == "https://www.yystv.cn/p/14411"     # 这个是给人点的

    # 指纹仍然是稳定的：带不带 www、带不带追踪参数，都是同一条
    assert url_hash(raw) == url_hash("https://yystv.cn/p/14411")


def test_url_hash_is_stable_across_sources():
    """本地与海外两边各算一次，必须得到同一个哈希 —— 否则合并去重就废了。"""
    assert url_hash("https://www.ign.com/articles/foo/?utm_source=x") == url_hash(
        "https://ign.com/articles/foo"
    )
    assert url_hash("https://ign.com/a").startswith("sha256:")


def test_normalize_title_handles_fullwidth_and_zero_width():
    assert normalize_title("  hello\u3000world\u200b  ") == "hello world"


def test_title_key_ignores_punctuation():
    assert title_key("Hello, World!") == title_key("hello world")


def test_to_local_date_accepts_iso_z():
    assert to_local_date("2026-09-22T10:00:00Z") is not None


# ───────────────────────────── bundle 契约 ─────────────────────────────

def _item(url: str = "https://ign.com/a") -> RawItem:
    return RawItem(
        source_id="ign",
        domain="game",
        url=url,
        title="Original English title",
        title_en="Original English title",
        published_at="2026-09-22T10:00:00Z",
        content_text="正文",
    )


def test_bundle_roundtrip_preserves_identity():
    result = FetchResult(source_id="ign", ok=True, count=1, items=[_item()])
    payload = build_bundle("2026-09-22", [result])

    assert payload["version"] == 1
    assert payload["sources"][0]["ok"] is True

    back = items_from_payload(payload)
    assert len(back) == 1
    # 跨机器传递后 url_hash 必须一致，否则本地会把它当成新条目再入一次
    assert url_hash(back[0].url) == url_hash(_item().url)
    assert back[0].content_text == "正文"


def test_bundle_records_failed_sources_without_items():
    result = FetchResult(source_id="ign", ok=False, count=0, error="TimeoutError")
    payload = build_bundle("2026-09-22", [result])
    assert payload["items"] == []
    assert payload["sources"][0]["error"] == "TimeoutError"


# ───────────────────────────── 打分 ─────────────────────────────

def _source(sid: str, weight: float) -> Source:
    return Source(id=sid, name=sid, domain="ai", type="rss", url="", weight=weight)


def test_score_rewards_fresh_and_weighted():
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)

    fresh = RawItem(
        source_id="a", domain="ai", url="https://a/1",
        title="新模型发布 open-source", published_at="2026-09-22T10:00:00Z",
    )
    stale = RawItem(
        source_id="b", domain="ai", url="https://b/1",
        title="旧闻一则", published_at="2026-09-01T10:00:00Z",
    )

    assert score_item(fresh, _source("a", 1.4), now) > score_item(stale, _source("b", 0.8), now)


def test_score_is_bounded():
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    item = RawItem(source_id="a", domain="ai", url="https://a/1", title="模型 开源 论文 基准 推理 训练")
    s = score_item(item, _source("a", 5.0), now)
    assert 1.0 <= s <= 10.0
