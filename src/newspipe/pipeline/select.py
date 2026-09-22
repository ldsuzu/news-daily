"""从候选条目里挑出当天的「精选」。

单看分数会出问题：国内游戏站只有标题、没有 RSS 摘要，关键词加分天然吃亏，
于是一个来源就能把 10 个名额全占满。精选要的是"今天值得看的几件事"，
不是"某一家今天的全部输出"。
"""

from __future__ import annotations

from typing import Any, Sequence


def select_picks(
    rows: Sequence[dict[str, Any]],
    picks_n: int,
    *,
    max_per_source: int = 3,
) -> list[dict[str, Any]]:
    """按顺序取前 picks_n 条，同时限制单个来源的占比。

    rows 必须已按重要度排好序。名额没被占满时，溢出的条目按原顺序回填 ——
    限制是"尽量均衡"，不是"宁可少给"。
    """
    if picks_n <= 0:
        return []

    picked: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}

    for row in rows:
        source_id = row.get("source_id", "")
        used = per_source.get(source_id, 0)
        if max_per_source > 0 and used >= max_per_source:
            overflow.append(row)
            continue
        per_source[source_id] = used + 1
        picked.append(row)
        if len(picked) >= picks_n:
            return picked

    for row in overflow:
        if len(picked) >= picks_n:
            break
        picked.append(row)

    return picked[:picks_n]
