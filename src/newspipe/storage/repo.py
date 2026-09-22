"""数据访问层：条目的写入与查询。

界面和 CLI 都只通过这里碰数据库 —— SQL 不散落在别处。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from ..config import Source
from ..models import RawItem
from ..pipeline.normalize import (
    now_utc_iso,
    title_key,
    to_local_date,
    url_hash,
)


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ───────────────────────────── 源 ─────────────────────────────

    def sync_sources(self, sources: Iterable[Source]) -> int:
        """把 sources.yaml 的状态同步进库，UI 的信息源页读的也是这张表。"""
        count = 0
        for s in sources:
            self.conn.execute(
                """
                INSERT INTO sources (id, name, domain, type, url, weight, enabled, needs_proxy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    domain = excluded.domain,
                    type = excluded.type,
                    url = excluded.url,
                    weight = excluded.weight,
                    enabled = excluded.enabled,
                    needs_proxy = excluded.needs_proxy
                """,
                (s.id, s.name, s.domain, s.type, s.url, s.weight,
                 int(s.enabled), int(s.needs_proxy)),
            )
            count += 1
        self.conn.commit()
        return count

    def log_fetch(self, source_id: str, started_at: str, ok: bool, count: int, error: str = "") -> None:
        self.conn.execute(
            "INSERT INTO fetch_log (source_id, started_at, ok, count, error) VALUES (?, ?, ?, ?, ?)",
            (source_id, started_at, int(ok), count, error[:500]),
        )
        if ok:
            self.conn.execute(
                "UPDATE sources SET last_ok_at = ?, last_error = '', fail_count = 0 WHERE id = ?",
                (started_at, source_id),
            )
        else:
            self.conn.execute(
                "UPDATE sources SET last_error = ?, fail_count = fail_count + 1 WHERE id = ?",
                (error[:500], source_id),
            )
        self.conn.commit()

    def sources_overview(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM items i WHERE i.source_id = s.id) AS item_count,
                   (SELECT COUNT(*) FROM fetch_log f
                     WHERE f.source_id = s.id AND f.started_at >= datetime('now', '-7 days')) AS fetches_7d
            FROM sources s
            ORDER BY s.domain, s.name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ───────────────────────────── 条目 ─────────────────────────────

    def upsert_items(self, items: Iterable[RawItem], origin: str = "local") -> dict[str, int]:
        """按 url_hash 幂等入库。已存在的条目只补空字段，不覆盖已有内容。"""
        new = seen = 0
        now = now_utc_iso()

        for it in items:
            h = url_hash(it.url)
            fetched = it.fetched_at or now
            pub_date = to_local_date(it.published_at) or to_local_date(fetched) or ""

            row = self.conn.execute("SELECT id FROM items WHERE url_hash = ?", (h,)).fetchone()
            if row:
                seen += 1
                self.conn.execute(
                    """
                    UPDATE items SET
                        content_text = CASE WHEN ? <> '' THEN ? ELSE content_text END,
                        excerpt      = CASE WHEN ? <> '' THEN ? ELSE excerpt END,
                        title_en     = CASE WHEN ? <> '' THEN ? ELSE title_en END,
                        published_at = COALESCE(published_at, ?),
                        pub_date     = CASE WHEN pub_date = '' THEN ? ELSE pub_date END,
                        fetched_at   = ?
                    WHERE id = ?
                    """,
                    (it.content_text, it.content_text,
                     it.excerpt, it.excerpt,
                     it.title_en, it.title_en,
                     it.published_at, pub_date, fetched, row["id"]),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO items
                        (url_hash, source_id, domain, url, title, title_en, title_key,
                         published_at, pub_date, fetched_at, lang, excerpt, content_text,
                         score, origin, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (h, it.source_id, it.domain, it.url, it.title, it.title_en,
                     title_key(it.title), it.published_at, pub_date, fetched,
                     it.lang, it.excerpt, it.content_text,
                     float(it.extra.get("score") or 0.0), origin, now),
                )
                new += 1

        self.conn.commit()
        return {"new": new, "seen": seen}

    def list_items(
        self,
        *,
        domain: str | None = None,
        date: str | None = None,
        source_id: str | None = None,
        min_score: float | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "time",
        representatives_only: bool = False,
    ) -> list[dict[str, Any]]:
        where, args = [], []
        if domain:
            where.append("domain = ?")
            args.append(domain)
        if date:
            where.append("pub_date = ?")
            args.append(date)
        if source_id:
            where.append("source_id = ?")
            args.append(source_id)
        if min_score is not None:
            where.append("score >= ?")
            args.append(min_score)
        if representatives_only:
            # 同一事件被多家（或同一家的两个入口）报道时，只出代表条目，
            # 否则重复条目会白占「精选每领域 N 条」的名额。
            where.append("(cluster_id IS NULL OR cluster_id = id)")

        order_sql = "score DESC, published_at DESC" if order == "score" else "COALESCE(published_at, fetched_at) DESC"
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM items {clause} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_items(
        self,
        *,
        domain: str | None = None,
        date: str | None = None,
        source_id: str | None = None,
        min_score: float | None = None,
    ) -> int:
        where, args = [], []
        if domain:
            where.append("domain = ?")
            args.append(domain)
        if date:
            where.append("pub_date = ?")
            args.append(date)
        if source_id:
            where.append("source_id = ?")
            args.append(source_id)
        if min_score is not None:
            where.append("score >= ?")
            args.append(min_score)

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return int(
            self.conn.execute(f"SELECT COUNT(*) FROM items {clause}", args).fetchone()[0]
        )

    def sources_map(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT id, name, domain, weight, needs_proxy FROM sources").fetchall()
        return {r["id"]: dict(r) for r in rows}

    def search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """全历史检索。优先 FTS5，建表失败时退回 LIKE。"""
        query = (query or "").strip()
        if not query:
            return []
        try:
            rows = self.conn.execute(
                """
                SELECT i.* FROM items_fts f
                JOIN items i ON i.id = f.rowid
                WHERE f MATCH ?
                ORDER BY f.rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            like = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT * FROM items
                WHERE title LIKE ? OR title_en LIKE ? OR summary_cn LIKE ? OR content_text LIKE ?
                ORDER BY COALESCE(published_at, fetched_at) DESC
                LIMIT ?
                """,
                (like, like, like, like, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def counts(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        today = self.conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE pub_date = date('now', 'localtime')"
        ).fetchone()["c"]
        by_domain = {
            r["domain"]: r["c"]
            for r in self.conn.execute("SELECT domain, COUNT(*) AS c FROM items GROUP BY domain")
        }
        by_origin = {
            r["origin"]: r["c"]
            for r in self.conn.execute("SELECT origin, COUNT(*) AS c FROM items GROUP BY origin")
        }
        return {"total": total, "today": today, "by_domain": by_domain, "by_origin": by_origin}

    def available_dates(self, limit: int = 60) -> list[dict[str, Any]]:
        """历史入口：有内容的日期列表（界面左栏/日历用）。"""
        rows = self.conn.execute(
            """
            SELECT pub_date AS date, COUNT(*) AS n
            FROM items
            WHERE pub_date <> ''
            GROUP BY pub_date
            ORDER BY pub_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ───────────────────────────── 阅读状态 ─────────────────────────────

    def set_read(self, item_id: int, read: bool = True) -> None:
        self.conn.execute(
            """
            INSERT INTO read_state (item_id, read_at, starred) VALUES (?, ?, 0)
            ON CONFLICT(item_id) DO UPDATE SET read_at = excluded.read_at
            """,
            (item_id, now_utc_iso() if read else None),
        )
        self.conn.commit()

    def set_starred(self, item_id: int, starred: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO read_state (item_id, read_at, starred) VALUES (?, NULL, ?)
            ON CONFLICT(item_id) DO UPDATE SET starred = excluded.starred
            """,
            (item_id, int(starred)),
        )
        self.conn.commit()


def dumps_points(points: list[str]) -> str:
    return json.dumps(points or [], ensure_ascii=False)
