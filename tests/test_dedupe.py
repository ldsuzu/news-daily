"""去重聚类（第二、三层漏斗）的测试。

用内存库跑，不碰真实数据。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from newspipe.models import RawItem
from newspipe.pipeline.dedupe import (
    cluster_items,
    hamming64,
    simhash64,
    tokenize,
)
from newspipe.pipeline.normalize import to_iso_utc
from newspipe.storage.db import init_db
from newspipe.storage.repo import Repo


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _item(source_id: str, url: str, title: str, hours_ago: int = 1) -> RawItem:
    published = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return RawItem(
        source_id=source_id,
        domain="game",
        url=url,
        title=title,
        published_at=to_iso_utc(published),
    )


# ───────────────────────────── 特征与指纹 ─────────────────────────────

def test_tokenize_splits_cjk_into_bigrams():
    tokens = tokenize("空洞骑士")
    assert "空" in tokens
    assert "空洞" in tokens
    assert "骑士" in tokens


def test_tokenize_drops_english_stopwords():
    assert "the" not in tokenize("the witcher")
    assert "witcher" in tokenize("the witcher")


def test_simhash_similar_titles_are_closer_than_unrelated_ones():
    a = simhash64("Hollow Knight Silksong release window announced")
    b = simhash64("Hollow Knight: Silksong release window announced")
    c = simhash64("Steam Next Fest most played demos revealed")

    assert hamming64(a, b) < hamming64(a, c)


def test_simhash_handles_chinese():
    a = simhash64("空洞骑士丝之歌公布发售窗口")
    b = simhash64("空洞骑士：丝之歌公布发售窗口")
    assert hamming64(a, b) <= 8


# ───────────────────────────── 聚类 ─────────────────────────────

def test_same_event_across_sources_merges_into_one_cluster():
    conn = _conn()
    repo = Repo(conn)
    repo.upsert_items(
        [
            _item("ign", "https://ign.com/1", "Hollow Knight Silksong release window announced", hours_ago=1),
            _item("pcg", "https://pcgamer.com/1", "Hollow Knight: Silksong release window announced", hours_ago=2),
            _item("vgc", "https://vgc.com/1", "Silksong release window finally announced by Team Cherry", hours_ago=3),
            _item("gematsu", "https://gematsu.com/1", "日本一软件公布新作发售日期", hours_ago=4),
        ]
    )

    stats = cluster_items(conn)
    assert stats["clusters"] >= 1

    clustered = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE cluster_size > 1"
    ).fetchone()["c"]
    assert clustered >= 2, "同一事件的跨源报道应该被折叠到一起"


def test_different_domains_never_merge():
    """游戏和 AI 的标题再像也不该合并 —— 领域是硬边界。"""
    conn = _conn()
    repo = Repo(conn)

    a = _item("ign", "https://ign.com/x", "OpenAI announces new model")
    b = RawItem(
        source_id="openai",
        domain="ai",                       # 不同领域
        url="https://openai.com/x",
        title="OpenAI announces new model",
        published_at=to_iso_utc(datetime.now(timezone.utc)),
    )
    repo.upsert_items([a, b])

    cluster_items(conn)
    sizes = [r["cluster_size"] for r in conn.execute("SELECT cluster_size FROM items")]
    assert all(s == 1 for s in sizes)


def test_cluster_is_idempotent():
    conn = _conn()
    repo = Repo(conn)
    repo.upsert_items(
        [
            _item("ign", "https://ign.com/2", "Starfield expansion announced today", hours_ago=1),
            _item("pcg", "https://pcgamer.com/2", "Starfield expansion announced today", hours_ago=2),
        ]
    )

    first = cluster_items(conn)
    second = cluster_items(conn)

    assert first["clusters"] == second["clusters"]
    assert conn.execute("SELECT COUNT(*) AS c FROM clusters").fetchone()["c"] == first["clusters"]


def test_old_items_outside_window_are_ignored():
    conn = _conn()
    repo = Repo(conn)
    repo.upsert_items(
        [
            _item("ign", "https://ign.com/old", "Very old duplicated headline", hours_ago=24 * 30),
            _item("pcg", "https://pcgamer.com/old", "Very old duplicated headline", hours_ago=24 * 30),
        ]
    )

    stats = cluster_items(conn, window_days=14)
    assert stats["scanned"] == 0
