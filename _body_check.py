"""临时：看正文抓取覆盖率（双栏布局的右栏靠它）。"""

import sqlite3
import sys

sys.stdout = open(r"D:\DSH工作区\news-daily\data\_body.txt", "w", encoding="utf-8")

con = sqlite3.connect(r"D:\每日简报\data\news.db")
con.row_factory = sqlite3.Row

print("按天：")
for r in con.execute(
    """
    SELECT pub_date, COUNT(*) n,
           SUM(CASE WHEN LENGTH(content_text) > 400 THEN 1 ELSE 0 END) b
    FROM items WHERE pub_date >= '2026-09-26'
    GROUP BY pub_date ORDER BY pub_date DESC
    """
):
    pct = 100 * (r["b"] or 0) / max(1, r["n"])
    print(f"  {r['pub_date']}  {r['n']:>4} 条   有正文 {r['b'] or 0:>4} 条   {pct:.0f}%")

print()
print("整体：")
t = con.execute(
    "SELECT COUNT(*) n, SUM(CASE WHEN LENGTH(content_text) > 400 THEN 1 ELSE 0 END) b FROM items"
).fetchone()
print(f"  {t['n']} 条，有正文 {t['b'] or 0} 条（{100*(t['b'] or 0)/max(1,t['n']):.0f}%）")

print()
print("按来源（最近 3 天，条目 ≥ 8 的）：")
for r in con.execute(
    """
    SELECT source_id, COUNT(*) n,
           SUM(CASE WHEN LENGTH(content_text) > 400 THEN 1 ELSE 0 END) b
    FROM items
    WHERE pub_date >= '2026-09-27'
    GROUP BY source_id HAVING n >= 8
    ORDER BY n DESC
    """
):
    pct = 100 * (r["b"] or 0) / max(1, r["n"])
    flag = "  ← 抓不到正文" if pct < 20 else ""
    print(f"  {r['source_id']:<18} {r['n']:>4} 条   正文 {r['b'] or 0:>4} 条   {pct:>3.0f}%{flag}")

con.close()
