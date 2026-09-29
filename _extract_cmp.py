"""临时：逐条对比「现有 content_text 长度」与「抽取结果长度」。"""

import sqlite3
import sys

sys.path.insert(0, r"D:\DSH工作区\news-daily\src")
sys.stdout = open(r"D:\DSH工作区\news-daily\data\_extract_cmp.txt", "w", encoding="utf-8")

from pathlib import Path

from newspipe.collectors.base import Http
from newspipe.config import load_config
from newspipe.pipeline.extract import extract_for_url

cfg = load_config(Path(r"D:\DSH工作区\news-daily"))
con = sqlite3.connect(cfg.settings.db_path)
con.row_factory = sqlite3.Row

rows = con.execute(
    """
    SELECT i.id, i.url, i.source_id, i.content_text,
           LENGTH(i.content_text) AS n
    FROM items i JOIN sources s ON s.id = i.source_id
    WHERE LENGTH(i.content_text) < 400 AND s.extract = 1
    ORDER BY COALESCE(i.published_at, i.fetched_at) DESC
    LIMIT 8
    """
).fetchall()

print(f"{'来源':<14}{'库里 n':>8}{'实际 len':>10}{'抽出':>8}   结论")
with Http(timeout=20) as http:
    for r in rows:
        text = extract_for_url(r["url"], http)
        cur = r["content_text"] or ""
        verdict = "替换" if len(text) > (r["n"] or 0) else ("跳过" if text else "抓不到")
        print(f"{r['source_id']:<14}{r['n']:>8}{len(cur):>10}{len(text):>8}   {verdict}")
        print(f"    库里前 80 字：{cur[:80]!r}")
        print(f"    抽出前 80 字：{text[:80]!r}")

con.close()
