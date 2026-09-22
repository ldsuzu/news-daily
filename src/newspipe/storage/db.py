"""SQLite 连接与建表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SPLIT_MARK = "-- @@SPLIT@@"


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _trigram_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE temp.__probe")
        return True
    except sqlite3.OperationalError:
        return False


def _migrate(conn: sqlite3.Connection) -> None:
    """给老库补列 —— schema 用的是 CREATE TABLE IF NOT EXISTS，加列得显式做。"""
    source_cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
    if "extract" not in source_cols:
        conn.execute("ALTER TABLE sources ADD COLUMN extract INTEGER NOT NULL DEFAULT 1")

    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for name, ddl in (
        ("title_cn", "ALTER TABLE items ADD COLUMN title_cn TEXT NOT NULL DEFAULT ''"),
        ("llm_score", "ALTER TABLE items ADD COLUMN llm_score REAL"),
        ("llm_at", "ALTER TABLE items ADD COLUMN llm_at TEXT"),
    ):
        if name not in item_cols:
            conn.execute(ddl)

    conn.commit()


def init_db(conn: sqlite3.Connection) -> str:
    """建表并返回全文检索用的分词器名（doctor 会显示它）。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    core, _, fts = sql.partition(SPLIT_MARK)
    conn.executescript(core)
    _migrate(conn)

    tokenizer = "trigram" if _trigram_available(conn) else "unicode61"
    try:
        conn.executescript(fts.replace("__TOKENIZER__", tokenizer))
    except sqlite3.OperationalError as exc:
        # 搜不了历史，但别的功能照常 —— 不该因为一个可选特性把程序打死
        print(f"[warn] 全文检索表创建失败（{exc}）；历史搜索将退回 LIKE 匹配")
        conn.commit()
        return "none"

    conn.commit()
    _populate_fts_if_empty(conn)
    return tokenizer


def _populate_fts_if_empty(conn: sqlite3.Connection) -> int:
    """FTS 表建晚了（或刚建好）时，把已有条目补进索引。

    触发器只对之后的增删改生效，所以存量数据要显式回填一次。
    """
    try:
        n_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        n_fts = conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0

    if n_items and n_fts == 0:
        conn.execute(
            """
            INSERT INTO items_fts (rowid, title, title_en, summary_cn, content_text)
            SELECT id, title, title_en, summary_cn, content_text FROM items
            """
        )
        conn.commit()
        return int(n_items)
    return 0


def reindex(conn: sqlite3.Connection) -> None:
    """强制重建全文索引（external content 表自带 rebuild）。"""
    try:
        conn.execute("INSERT INTO items_fts (items_fts) VALUES ('rebuild')")
        conn.commit()
    except sqlite3.OperationalError as exc:
        print(f"[warn] 重建全文索引失败：{exc}")


def sqlite_version() -> str:
    return sqlite3.sqlite_version
