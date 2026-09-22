"""LLM 加工：翻译标题 + 生成中文摘要 + 判断热度，顺带记账。

两条设计原则：

1. **省 token**：批量送（一次 batch_size 条，共享一份系统提示，还能吃上缓存折扣）、
   只给标题和摘要（不给全文）、只处理**新增**条目、单轮与单日都有上限。
2. **不拖累主流程**：LLM 挂了只是少了翻译和热度分，抓取入库照常，界面照常能读。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Config
from ..models import RawItem

SYSTEM_PROMPT = """你是新闻编辑，为中文读者筛选和翻译资讯。

对每条资讯输出一个对象：
- i: 原样返回输入的编号
- cn: 中文标题。外文标题翻译成中文（不超过 25 字）；本来就是中文的原样返回，
      只在明显冗长时精简。不要加「重磅」「震惊」这类词。
- sum: 一句话中文摘要，不超过 60 字，说清"发生了什么"，不要评价、不要用"本文"。
- heat: 热度，0-10 的整数。判断依据是这件事本身有多值得知道 ——
      影响范围多大、是不是行业级的变化、时效性如何。
      不要因为标题里出现了大厂名字就给高分，也不要因为来源小众就给低分。
      10 = 当天最重要的行业事件；7-8 = 值得一读；5-6 = 常规资讯；3-4 = 边角消息。

只输出一个 JSON 对象：{"items": [ ... ]}，不要任何解释，不要 markdown 代码块。
"""


@dataclass
class EnrichResult:
    index: int
    title_cn: str = ""
    summary_cn: str = ""
    heat: float | None = None


@dataclass
class Usage:
    """一次调用的账。cost_cny 是按配置单价换算的，只用于展示。"""

    model: str
    items: int = 0
    input_tokens: int = 0     # 未命中缓存的输入
    cached_tokens: int = 0    # 命中缓存的输入
    output_tokens: int = 0
    cost_cny: float = 0.0
    ok: bool = True
    note: str = ""
    detail: list[dict[str, Any]] = field(default_factory=list)


class LlmClient:
    def __init__(self, config: Config) -> None:
        s = config.settings
        self.enabled = bool(s.get("llm.enabled", False))
        self.model = s.get("llm.model") or "deepseek-flash"
        self.base_url = (s.get("llm.base_url") or "https://api.deepseek.com").rstrip("/")
        self.api_key = s.get("llm.api_key") or os.environ.get("NEWSPIPE_LLM_KEY") or ""
        self.batch_size = max(1, int(s.get("llm.batch_size", 20)))
        self.summary_chars = int(s.get("llm.summary_chars", 400))
        self.daily_budget = float(s.get("llm.daily_budget_cny", 0) or 0)

        prices = s.get("llm.prices", {}) or {}
        self.price_in = float(prices.get("input_miss", 1.0))
        self.price_hit = float(prices.get("input_hit", 0.02))
        self.price_out = float(prices.get("output", 4.0))

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def _cost(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.price_in
            + usage.cached_tokens * self.price_hit
            + usage.output_tokens * self.price_out
        ) / 1_000_000

    # ───────────────────────── 调用 ─────────────────────────

    def enrich_batch(self, items: list[RawItem]) -> tuple[dict[int, EnrichResult], Usage]:
        """一次处理一批。返回 {序号: 结果} 与这次调用的账。"""
        usage = Usage(model=self.model, items=len(items))

        if not items:
            return {}, usage

        payload = [
            {
                "i": idx,
                "title": it.title,
                "source": it.source_id,
                "excerpt": (it.excerpt or it.content_text or "")[: self.summary_chars],
            }
            for idx, it in enumerate(items)
        ]

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"items": payload}, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "max_tokens": max(800, len(items) * 220),
        }

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=180.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 —— LLM 失败不该带走整条流水线
            usage.ok = False
            usage.note = f"{type(exc).__name__}: {str(exc)[:160]}"
            return {}, usage

        raw = data.get("usage") or {}
        usage.cached_tokens = int(raw.get("prompt_cache_hit_tokens") or 0)
        miss = raw.get("prompt_cache_miss_tokens")
        usage.input_tokens = int(
            miss if miss is not None else (raw.get("prompt_tokens") or 0)
        ) - (0 if miss is not None else usage.cached_tokens)
        usage.input_tokens = max(0, usage.input_tokens)
        usage.output_tokens = int(raw.get("completion_tokens") or 0)
        usage.cost_cny = self._cost(usage)

        choices = data.get("choices") or []
        content = ""
        if choices:
            content = ((choices[0].get("message") or {}).get("content") or "").strip()

        return self._parse(content, len(items)), usage

    @staticmethod
    def _parse(content: str, expected: int) -> dict[int, EnrichResult]:
        text = (content or "").strip()
        if text.startswith("```"):  # 万一模型还是包了代码块
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}

        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return {}

        out: dict[int, EnrichResult] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < expected:
                continue

            heat_raw = row.get("heat")
            try:
                heat = float(heat_raw) if heat_raw is not None else None
            except (TypeError, ValueError):
                heat = None
            if heat is not None:
                heat = max(0.0, min(10.0, heat))

            out[idx] = EnrichResult(
                index=idx,
                title_cn=str(row.get("cn") or "").strip(),
                summary_cn=str(row.get("sum") or "").strip(),
                heat=heat,
            )
        return out
