"""精选挑选逻辑的测试。

真实起因：国内游戏站只有标题、没有 RSS 摘要，关键词加分天然吃亏，
结果精选 10 条被机核一家占满 —— 精选要的是"今天值得看的几件事"。
"""

from __future__ import annotations

from newspipe.pipeline.select import select_picks


def _row(source_id: str, title: str, score: float) -> dict[str, object]:
    return {"source_id": source_id, "title": title, "score": score}


def test_caps_how_many_one_source_can_take() -> None:
    """候选充足时，单源占比受 max_per_source 约束。"""
    rows = []
    for i in range(4):
        rows.append(_row("gcores", f"机核第{i}条", 9.0 - i * 0.1))
        rows.append(_row("gamersky", f"游民第{i}条", 8.9 - i * 0.1))
        rows.append(_row("yystv", f"游研第{i}条", 8.8 - i * 0.1))
    rows.sort(key=lambda r: -float(r["score"]))  # type: ignore[arg-type]

    picks = select_picks(rows, 6, max_per_source=2)

    assert len(picks) == 6
    assert sum(1 for p in picks if p["source_id"] == "gcores") == 2
    assert sum(1 for p in picks if p["source_id"] == "gamersky") == 2
    assert sum(1 for p in picks if p["source_id"] == "yystv") == 2


def test_backfills_when_one_source_is_all_we_have() -> None:
    """限制是"尽量均衡"，不是"宁可少给" —— 只有一个来源时就该给满。"""
    rows = [_row("gcores", f"机核第{i}条", 9.0 - i * 0.1) for i in range(10)]

    picks = select_picks(rows, 8, max_per_source=3)

    assert len(picks) == 8
    assert all(p["source_id"] == "gcores" for p in picks)


def test_keeps_score_order_within_the_cap() -> None:
    rows = [
        _row("a", "a1", 9.0),
        _row("b", "b1", 8.9),
        _row("c", "c1", 8.8),
        _row("a", "a2", 8.7),
    ]

    picks = select_picks(rows, 4, max_per_source=2)

    assert [p["title"] for p in picks] == ["a1", "b1", "c1", "a2"]


def test_zero_cap_means_no_balancing() -> None:
    rows = [_row("gcores", f"机核第{i}条", 9.0 - i * 0.1) for i in range(6)]

    picks = select_picks(rows, 5, max_per_source=0)

    assert len(picks) == 5
    assert all(p["source_id"] == "gcores" for p in picks)


def test_handles_empty_and_zero_sized_requests() -> None:
    assert select_picks([], 5) == []
    assert select_picks([_row("a", "a1", 9.0)], 0) == []


# ───────────────────────────── 同一事件折叠 ─────────────────────────────

def _event_row(source_id: str, title: str, score: float, event: str) -> dict[str, object]:
    return {"source_id": source_id, "title": title, "score": score, "event_key": event}


def test_folds_same_event_reported_by_many_sources() -> None:
    """真实场景：微软重组 Xbox 那天，八家媒体各报一遍，精选 10 条里 8 条是同一件事。"""
    rows = [
        _event_row(f"src{i}", f"微软重组 #{i}", 9.0 - i * 0.1, "Xbox重组")
        for i in range(8)
    ]

    picks = select_picks(rows, 10)

    assert len(picks) == 1
    assert picks[0]["source_id"] == "src0"      # 留热度最高的那条


def test_keeps_different_events() -> None:
    rows = [
        _event_row("a", "A", 9.0, "事件甲"),
        _event_row("b", "B", 8.0, "事件乙"),
    ]
    assert len(select_picks(rows, 10)) == 2


def test_rows_without_event_key_are_never_folded_together() -> None:
    """没被 LLM 处理过的条目没有 event_key —— 不能因为都为空就当成同一件事。"""
    rows = [
        {"source_id": "a", "title": "A", "score": 9.0},
        {"source_id": "b", "title": "B", "score": 8.0},
    ]
    assert len(select_picks(rows, 10)) == 2


def test_folded_events_are_not_backfilled() -> None:
    """折叠掉的条目不能在"名额没满就回填"那一步又冒出来。"""
    rows = [
        _event_row("a", "A1", 9.0, "甲"),
        _event_row("a", "A2", 8.9, "甲"),
        _event_row("a", "A3", 8.8, "甲"),
        _event_row("a", "A4", 8.7, "甲"),
    ]

    picks = select_picks(rows, 4, max_per_source=1)

    assert len(picks) == 1
    assert picks[0]["title"] == "A1"
