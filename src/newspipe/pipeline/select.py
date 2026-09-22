"""从候选条目里挑出当天的「精选」。

单看分数会出问题，两个坑都踩过：

1. **一家垄断**：国内游戏站只有标题、没有 RSS 摘要，关键词加分天然吃亏，
   一个来源能把 10 个名额全占满。
2. **一件事刷屏**：微软重组 Xbox 那天，游民星空、3DM、PC Gamer、RPS、Eurogamer、
   Gematsu、机核、GameSpot 各报一遍 —— SimHash 抓不住中英文标题的差异，
   于是精选 10 条里 8 条是同一件事。现在靠 LLM 给的事件标签折叠。

精选要的是"今天值得看的几件事"，不是"某一家今天的全部输出"，也不是"同一件事的八种说法"。
"""

from __future__ import annotations

from typing import Any, Sequence


def _event_of(row: dict[str, Any]) -> str:
    return str(row.get("event_key") or "").strip()


def select_picks(
    rows: Sequence[dict[str, Any]],
    picks_n: int,
    *,
    max_per_source: int = 3,
    dedupe_by_event: bool = True,
) -> list[dict[str, Any]]:
    """按顺序取前 picks_n 条，同时限制单源占比、折叠同一事件。

    rows 必须已按重要度排好序。名额没被占满时，因来源配额被挡下的条目按原顺序回填 ——
    限制是"尽量均衡"，不是"宁可少给"；但同一事件被折叠的条目不回填，否则就白折了。
    """
    if picks_n <= 0:
        return []

    picked: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    seen_events: set[str] = set()

    for row in rows:
        source_id = str(row.get("source_id", ""))

        if max_per_source > 0 and per_source.get(source_id, 0) >= max_per_source:
            overflow.append(row)
            continue

        event = _event_of(row)
        if dedupe_by_event and event:
            if event in seen_events:
                continue
            seen_events.add(event)

        per_source[source_id] = per_source.get(source_id, 0) + 1
        picked.append(row)
        if len(picked) >= picks_n:
            return picked

    for row in overflow:
        if len(picked) >= picks_n:
            break
        event = _event_of(row)
        if dedupe_by_event and event and event in seen_events:
            continue
        if event:
            seen_events.add(event)
        picked.append(row)

    return picked[:picks_n]
