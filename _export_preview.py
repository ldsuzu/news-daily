"""把真实条目导出成 JS 变量，供布局原型使用（临时工具，用完即删）。"""

import json
import sqlite3

DB = r"D:\每日简报\data\news.db"
OUT = r"D:\DSH工作区\news-daily\prototype\_data.js"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# 选「最近一个条目足够多的一天」—— 当天可能才刚抓了几条，看不出布局效果
row = con.execute(
    """
    SELECT pub_date, COUNT(*) AS n FROM items
    WHERE (relevant IS NULL OR relevant = 1)
    GROUP BY pub_date HAVING n >= 12
    ORDER BY pub_date DESC LIMIT 1
    """
).fetchone()
day = row["pub_date"]

rows = con.execute(
    """
    SELECT title, title_cn, source_id, domain, llm_score, score,
           published_at, summary_cn, excerpt, content_text, url, lang
    FROM items
    WHERE pub_date = ? AND (relevant IS NULL OR relevant = 1)
    ORDER BY COALESCE(llm_score, score) DESC
    LIMIT 26
    """,
    (day,),
).fetchall()

sources = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM sources").fetchall()}


def hhmm(value):
    if not value:
        return ""
    try:
        from datetime import datetime, timedelta, timezone

        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(hours=8)).strftime("%H:%M")
    except Exception:
        return ""


items = []
for r in rows:
    title = r["title_cn"] or r["title"]
    original = r["title"] if r["title_cn"] and r["title_cn"] != r["title"] else ""
    heat = r["llm_score"] if r["llm_score"] is not None else (r["score"] or 0)
    body = (r["content_text"] or "").strip()
    items.append(
        {
            "src": sources.get(r["source_id"], r["source_id"]),
            "dom": r["domain"],
            "zh": title,
            "en": original,
            "sum": (r["summary_cn"] or r["excerpt"] or "").strip()[:150],
            "t": hhmm(r["published_at"]),
            "heat": round(float(heat), 1),
            "ext": bool(original),
            "chars": len(body),
            "preview": body[:160],
            "url": r["url"],
        }
    )

with open(OUT, "w", encoding="utf-8") as f:
    f.write("const DATA = ")
    json.dump(items, f, ensure_ascii=False, indent=1)
    f.write(";\n")

print(f"{day} · 导出 {len(items)} 条")
for it in items[:6]:
    print(f"  {it['heat']:>4}  [{it['src']}] {it['zh'][:44]}")
con.close()
