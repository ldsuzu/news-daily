"""HTML 列表页采集器的测试。

不需要联网：喂一段固定的 HTML，用桩对象冒充 Http。
"""

from __future__ import annotations

import re

from newspipe.collectors.html import _published_from_url, collect
from newspipe.config import Source


class _StubHttp:
    def __init__(self, page: str) -> None:
        self.page = page

    def get_text(self, url: str, **kwargs: object) -> str:
        return self.page


def _source(**params: object) -> Source:
    return Source(
        id="demo",
        name="Demo",
        domain="game",
        type="html",
        url="https://demo.com/news/",
        params=dict(params),
    )


# ───────────────────────────── 日期解析 ─────────────────────────────

def test_published_from_url_reads_year_and_month() -> None:
    pattern = re.compile(r"/news/(\d{4})(\d{2})/")
    assert _published_from_url("https://x.com/news/202609/1.shtml", pattern) == "2026-09-01T00:00:00Z"
    assert _published_from_url("https://x.com/about", pattern) is None
    assert _published_from_url("https://x.com/news/202609/1.shtml", None) is None


# ───────────────────────────── 采集与过滤 ─────────────────────────────

def test_collector_keeps_only_matching_long_titles() -> None:
    page = """
    <html><body><ul>
      <li><a href="/news/202609/111.shtml">一条足够长的新闻标题在这里</a></li>
      <li><a href="/news/202609/222.shtml">短</a></li>
      <li><a href="/about">关于我们关于我们关于我们</a></li>
    </ul></body></html>
    """

    items = collect(
        _source(base_url="https://demo.com", link_pattern=r"/news/\d{6}/\d+\.shtml$", min_title=8),
        _StubHttp(page),
    )

    assert len(items) == 1
    assert items[0].url == "https://demo.com/news/202609/111.shtml"
    assert items[0].title == "一条足够长的新闻标题在这里"
    assert items[0].domain == "game"


def test_collector_dedupes_repeated_links() -> None:
    page = """
    <a href="/news/202609/1.shtml">这条标题够长了可以进来</a>
    <a href="/news/202609/1.shtml">这条标题够长了可以进来</a>
    """

    items = collect(
        _source(base_url="https://demo.com", link_pattern=r"/news/\d{6}/\d+\.shtml$"),
        _StubHttp(page),
    )
    assert len(items) == 1


def test_collector_drops_stale_links_when_max_age_set() -> None:
    """列表页常常混着「相关阅读」的老链接，别把它们当成今天的新闻。"""
    page = """
    <a href="/news/202001/1.shtml">很久以前的一条新闻标题</a>
    <a href="/news/202609/2.shtml">最近的一条新闻标题在这里</a>
    """

    items = collect(
        _source(
            base_url="https://demo.com",
            link_pattern=r"/news/\d{6}/\d+\.shtml$",
            date_from_url=r"/news/(\d{4})(\d{2})/",
            max_age_days=30,
        ),
        _StubHttp(page),
    )

    assert len(items) == 1
    assert "202609" in items[0].url
    # 关键：URL 里的年月只用来过滤，不能拿来当发布日期
    # （列表页 URL 只有年月，标成 1 号会把整月文章挤到一起）
    assert items[0].published_at is None


def test_collector_raises_when_nothing_matches() -> None:
    """选择器写错时要明确报错，而不是安静地返回 0 条。"""
    try:
        collect(_source(link_pattern=r"/never/matches/\d+$"), _StubHttp("<a href='/x'>标题够长了</a>"))
    except RuntimeError as exc:
        assert "没匹配到" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当抛 RuntimeError")


def test_collector_respects_max_items() -> None:
    links = "\n".join(
        f'<a href="/news/202609/{i}.shtml">第 {i} 条足够长的新闻标题</a>' for i in range(30)
    )
    items = collect(
        _source(base_url="https://demo.com", link_pattern=r"/news/\d{6}/\d+\.shtml$", max_items=5),
        _StubHttp(links),
    )
    assert len(items) == 5
