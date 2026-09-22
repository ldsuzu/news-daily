"""采集器公共设施：HTTP 客户端（UA / 超时 / 代理 / 每主机限速）。"""

from __future__ import annotations

import time
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx


class Http:
    """所有出网请求都走这里，策略集中一处。

    - 每主机限速：默认 1 req/s，别把人家站点打疼了
    - 可选代理：settings.network.proxy，填了就走（你开 VPN 时可以临时打开）
    - 统一 UA / 超时 / 跟随跳转
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        proxy: str = "",
        user_agent: str = "NewsPipe/0.1",
        rate_limit_per_host: float = 1.0,
    ) -> None:
        self._interval = (1.0 / rate_limit_per_host) if rate_limit_per_host > 0 else 0.0
        self._last: dict[str, float] = {}

        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "headers": {
                "User-Agent": user_agent,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json, text/html;q=0.8, */*;q=0.5",
            },
        }
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.Client(**kwargs)

    # ───────────────────────── 内部 ─────────────────────────

    def _throttle(self, url: str) -> None:
        if not self._interval:
            return
        host = urlsplit(url).netloc
        last = self._last.get(host)
        now = time.monotonic()
        if last is not None:
            delta = now - last
            if delta < self._interval:
                time.sleep(self._interval - delta)
        self._last[host] = time.monotonic()

    # ───────────────────────── 公开 API ─────────────────────────

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self._throttle(url)
        resp = self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get(url, **kwargs).text

    def get_bytes(self, url: str, **kwargs: Any) -> bytes:
        return self.get(url, **kwargs).content

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def get_json_many(self, urls: Iterable[str], workers: int = 8) -> list[Any]:
        """同一站点批量取 JSON 时用（例如 Hacker News 逐条取 item）。

        这种场景限速会把它拖成几分钟，所以这里改由线程数控制并发；
        失败的单条返回 None，不炸整批。
        """
        from concurrent.futures import ThreadPoolExecutor

        def one(u: str) -> Any:
            try:
                resp = self._client.get(u)
                resp.raise_for_status()
                return resp.json()
            except Exception:  # noqa: BLE001
                return None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, list(urls)))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Http":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def http_from_config(config: Any) -> Http:
    s = config.settings
    return Http(
        timeout=float(s.get("network.timeout_seconds", 20)),
        proxy=s.proxy,
        user_agent=s.user_agent,
        rate_limit_per_host=float(s.get("network.rate_limit_per_host", 1.0)),
    )
