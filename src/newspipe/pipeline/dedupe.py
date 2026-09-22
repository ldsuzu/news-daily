"""去重三层漏斗（技术框架 §4 的 ③）。

    第一层  URL 指纹       —— 入库时的 url_hash 唯一约束，已经在做了
    第二层  标题 SimHash   —— 标题近似就直接算同一条
    第三层  跨源聚类       —— 同一条新闻被 N 家报道，折叠成 1 条 + "另有 N-1 家报道"

这一层是「精选每领域 10 条」能成立的前提：10 家报道同一件事只占 1 个名额。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import defaultdict
from typing import Any, Iterable

# 中文按 2-gram 切，英文数字按词切
_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[A-Za-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "with", "is", "are",
    "new", "how", "why", "what", "its", "it", "at", "by", "from", "as", "be",
}


def tokenize(text: str) -> list[str]:
    """标题 → 特征词。中文用 2-gram 是为了让 SimHash 对中文也有区分度。"""
    if not text:
        return []

    tokens: list[str] = []
    for chunk in _TOKEN_RE.findall(text.lower()):
        if chunk[0].isascii():
            if chunk not in _STOPWORDS and len(chunk) > 1:
                tokens.append(chunk)
            continue

        # 中文：单字 + 相邻 2-gram
        tokens.extend(chunk)
        tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


def simhash64(text: str) -> int:
    """64 位 SimHash。相近文本得到相近指纹，比较用汉明距离。"""
    tokens = tokenize(text)
    if not tokens:
        return 0

    vector = [0] * 64
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        h = int.from_bytes(digest[:8], "big")
        for i in range(64):
            vector[i] += 1 if (h >> i) & 1 else -1

    fingerprint = 0
    for i in range(64):
        if vector[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming64(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class _UnionFind:
    def __init__(self, ids: Iterable[int]) -> None:
        self.parent = {i: i for i in ids}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_items(
    conn: sqlite3.Connection,
    *,
    window_days: int = 14,
    max_distance: int = 3,
) -> dict[str, Any]:
    """对窗口内的条目做跨源聚类，结果写回 items.cluster_id / cluster_size。

    代表选取：簇内重要度最高者；同分则取更早发布的。
    """
    rows = conn.execute(
        """
        SELECT id, domain, title, title_key, score, source_id, published_at
        FROM items
        WHERE COALESCE(published_at, fetched_at) >= datetime('now', ?)
        ORDER BY domain
        """,
        (f"-{int(window_days)} days",),
    ).fetchall()

    if not rows:
        return {"clusters": 0, "grouped_items": 0, "scanned": 0}

    # 先按 domain 分桶：游戏和 AI 的标题再像也不该合并
    by_domain: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        by_domain[row["domain"]].append(row)

    uf = _UnionFind([r["id"] for r in rows])
    hashes = {r["id"]: simhash64(r["title"]) for r in rows}

    # 先用标题指纹精确撞一次（同一篇被两家转载，标题一字不差）
    exact: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["domain"], row["title_key"])
        if key in exact:
            uf.union(exact[key], row["id"])
        else:
            exact[key] = row["id"]

    # 再用 SimHash 近似，桶内两两比
    for domain, bucket in by_domain.items():
        for i, a in enumerate(bucket):
            ha = hashes[a["id"]]
            for b in bucket[i + 1 :]:
                if uf.find(a["id"]) == uf.find(b["id"]):
                    continue
                if hamming64(ha, hashes[b["id"]]) <= max_distance:
                    uf.union(a["id"], b["id"])

    # 归组并挑代表
    groups: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        groups[uf.find(row["id"])].append(row)

    conn.execute("UPDATE items SET cluster_id = id, cluster_size = 1 WHERE cluster_id IS NULL OR cluster_id <> id OR cluster_size <> 1")
    conn.execute("DELETE FROM clusters")

    cluster_count = 0
    grouped_items = 0
    from .normalize import now_utc_iso

    for members in groups.values():
        if len(members) < 2:
            continue

        representative = min(
            members,
            key=lambda r: (-(r["score"] or 0), r["published_at"] or "9999"),
        )
        ids = [r["id"] for r in members]

        conn.execute(
            "INSERT INTO clusters (cluster_key, representative_item_id, size, created_at) VALUES (?, ?, ?, ?)",
            (
                f"{representative['domain']}:{simhash64(representative['title']):016x}",
                representative["id"],
                len(members),
                now_utc_iso(),
            ),
        )
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE items SET cluster_id = ?, cluster_size = ? WHERE id IN ({placeholders})",
            (representative["id"], len(members), *ids),
        )

        cluster_count += 1
        grouped_items += len(members)

    conn.commit()
    return {"clusters": cluster_count, "grouped_items": grouped_items, "scanned": len(rows)}
