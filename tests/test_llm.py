"""LLM 加工模块的测试。

不打真实 API：只测最容易出错的解析与计价 —— 模型返回的 JSON 千奇百怪，
而算钱的地方一旦错了，界面上显示的账就是错的。
"""

from __future__ import annotations

from pathlib import Path

from newspipe.config import Config, Settings
from newspipe.pipeline.llm import LlmClient, Usage


def _client(**llm: object) -> LlmClient:
    data = {"llm": {"enabled": True, "api_key": "test-key", **llm}}
    cfg = Config(
        root=Path("."),
        settings=Settings(Path("."), data),
        domains=[],
        sources=[],
    )
    return LlmClient(cfg)


# ───────────────────────────── 解析 ─────────────────────────────

def test_parses_plain_json_object() -> None:
    content = '{"items":[{"i":0,"cn":"中文标题","sum":"发生了什么","heat":8}]}'
    out = LlmClient._parse(content, 1)

    assert out[0].title_cn == "中文标题"
    assert out[0].summary_cn == "发生了什么"
    assert out[0].heat == 8.0


def test_parses_bare_array_too() -> None:
    """提示词要求返回对象，但模型偶尔直接给数组 —— 这两种都得认。"""
    content = '[{"i":0,"cn":"标题","sum":"摘要","heat":6}]'
    out = LlmClient._parse(content, 1)
    assert out[0].heat == 6.0


def test_strips_markdown_fence() -> None:
    content = '```json\n{"items":[{"i":0,"cn":"标题","sum":"摘要","heat":5}]}\n```'
    out = LlmClient._parse(content, 1)
    assert out[0].heat == 5.0


def test_clamps_heat_into_range() -> None:
    content = '{"items":[{"i":0,"heat":99},{"i":1,"heat":-3}]}'
    out = LlmClient._parse(content, 2)
    assert out[0].heat == 10.0
    assert out[1].heat == 0.0


def test_skips_out_of_range_and_malformed_rows() -> None:
    content = '{"items":[{"i":5,"heat":9},{"i":"abc"},{"cn":"没有编号"},"字符串"]}'
    assert LlmClient._parse(content, 2) == {}


def test_tolerates_missing_heat() -> None:
    content = '{"items":[{"i":0,"cn":"标题","sum":"摘要"}]}'
    out = LlmClient._parse(content, 1)
    assert out[0].heat is None
    assert out[0].title_cn == "标题"


def test_parses_event_label() -> None:
    content = '{"items":[{"i":0,"cn":"标题","sum":"摘要","heat":8,"ev":"Xbox重组"}]}'
    out = LlmClient._parse(content, 1)
    assert out[0].event == "Xbox重组"


def test_event_label_is_normalised() -> None:
    """标签要能当去重键用，所以去掉空白和标点，并限长。"""
    content = '{"items":[{"i":0,"ev":"  Xbox 重组，裁员！ "}]}'
    out = LlmClient._parse(content, 1)
    assert out[0].event == "Xbox重组裁员"


def test_missing_event_label_is_empty_string() -> None:
    content = '{"items":[{"i":0,"cn":"标题"}]}'
    out = LlmClient._parse(content, 1)
    assert out[0].event == ""


def test_returns_empty_on_garbage() -> None:
    assert LlmClient._parse("抱歉，我无法完成", 1) == {}
    assert LlmClient._parse("", 1) == {}


# ───────────────────────────── 计价 ─────────────────────────────

def test_cost_uses_configured_prices() -> None:
    client = _client(prices={"input_miss": 2.0, "input_hit": 0.1, "output": 8.0})
    usage = Usage(
        model="fake",
        items=10,
        input_tokens=1_000_000,   # 1M 未命中 × ¥2 = ¥2
        cached_tokens=1_000_000,  # 1M 命中   × ¥0.1 = ¥0.1
        output_tokens=1_000_000,  # 1M 输出   × ¥8 = ¥8
    )
    # 内部方法直接算
    assert client._cost(usage) == 10.1


def test_cost_defaults_are_deepseek_offpeak() -> None:
    client = _client()
    assert (client.price_in, client.price_hit, client.price_out) == (1.0, 0.02, 4.0)


def test_a_realistic_day_is_cheap() -> None:
    """300 条 × (200 输入 + 120 输出) 大概多少钱 —— 心里有个数。"""
    client = _client()
    usage = Usage(
        model="deepseek-flash",
        items=300,
        input_tokens=300 * 200,
        output_tokens=300 * 120,
    )
    cost = client._cost(usage)
    assert cost < 0.25, f"一天 300 条不该超过两毛五，实际 ¥{cost:.4f}"


def test_available_requires_both_switch_and_key(monkeypatch) -> None:
    # 这台机器上真的配了 NEWSPIPE_LLM_KEY，所以要先把环境变量摘掉再测
    monkeypatch.delenv("NEWSPIPE_LLM_KEY", raising=False)

    assert _client().available is True
    assert _client(enabled=False).available is False
    assert _client(api_key="").available is False


def test_api_key_can_come_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("NEWSPIPE_LLM_KEY", "from-env")
    assert _client(api_key="").available is True
