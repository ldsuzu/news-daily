"""重要度打分。

M0 先上规则版：源权重 + 时效 + 关键词命中。
LLM 兜底打分与摘要生成在 M2 接进来（见技术框架 §4），接口保持不变。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..config import Source
from ..models import RawItem
from .normalize import parse_iso

GAME_KEYWORDS: tuple[str, ...] = (
    "发售", "延期", "跳票", "实机", "演示", "预告", "评测", "重制", "复刻", "独占",
    "销量", "泄露", "爆料", "停服", "收购", "更新", "联机", "封测", "公测",
    "release", "delay", "trailer", "remake", "remaster", "launch", "gameplay",
    "review", "patch", "sold", "acquisition",
)

AI_KEYWORDS: tuple[str, ...] = (
    "开源", "模型", "论文", "基准", "融资", "推理", "训练", "微调", "对齐", "智能体",
    "多模态", "大模型", "算力", "数据集", "评测",
    "open-source", "open source", "model", "benchmark", "paper", "inference",
    "training", "agent", "multimodal", "sota", "rlhf", "distill", "scaling",
)

KEYWORDS: dict[str, tuple[str, ...]] = {"game": GAME_KEYWORDS, "ai": AI_KEYWORDS}


def _recency_bonus(published_at: str | None, now: datetime) -> float:
    dt = parse_iso(published_at)
    if dt is None:
        return 0.0
    age_hours = (now - dt).total_seconds() / 3600.0
    if age_hours < 0:            # 时间戳在未来，八成是源的时区问题
        return 0.5
    if age_hours < 6:
        return 1.5
    if age_hours < 24:
        return 1.0
    if age_hours < 72:
        return 0.4
    return -0.5


def _keyword_bonus(item: RawItem, keywords: Iterable[str]) -> float:
    haystack = f"{item.title} {item.excerpt} {item.content_text[:500]}".lower()
    hits = sum(1 for kw in keywords if kw in haystack)
    return min(hits * 0.6, 2.0)


def score_item(item: RawItem, source: Source, now: datetime | None = None) -> float:
    """0-10 的重要度。分数只用于排序与「精选」阈值，不做任何丢弃。"""
    now = now or datetime.now(timezone.utc)

    score = 5.0
    score += (source.weight - 1.0) * 3.0          # 源权重：1.0 为基准
    score += _recency_bonus(item.published_at, now)
    score += _keyword_bonus(item, KEYWORDS.get(item.domain, ()))

    return round(max(1.0, min(10.0, score)), 1)


def score_items(items: Iterable[RawItem], source: Source) -> None:
    """就地写入 item.extra['score']。"""
    now = datetime.now(timezone.utc)
    for it in items:
        it.extra["score"] = score_item(it, source, now)
