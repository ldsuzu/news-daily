"""bundle 拉取与回退的测试（技术框架 §3.4）。

GitHub 在国内时通时不通，所以「全都走不通」也是一条必须被覆盖的路径。
"""

from __future__ import annotations

import json
from pathlib import Path

from newspipe.config import Config, Domain, Settings
from newspipe.pipeline.bundle import items_from_payload
from newspipe.sync import fallback_local, mirror_urls, read_payload


def _config(tmp_path: Path, *, repo: str = "me/news-bundles") -> Config:
    data = {
        "paths": {"data_dir": "data", "bundles": "data/bundles"},
        "bundle": {
            "repo": repo,
            "branch": "main",
            "path": "bundles",
            "mirrors": [
                "https://cdn.jsdelivr.net/gh/{repo}@{branch}/{path}/{date}.json",
                "https://api.github.com/repos/{repo}/contents/{path}/{date}.json",
                "https://raw.githubusercontent.com/{repo}/{branch}/{path}/{date}.json",
            ],
        },
    }
    return Config(
        root=tmp_path,
        settings=Settings(tmp_path, data),
        domains=[Domain(id="game", name="游戏")],
        sources=[],
    )


def test_mirror_urls_expand_placeholders(tmp_path: Path) -> None:
    urls = mirror_urls(_config(tmp_path), "2026-09-22")

    assert len(urls) == 3
    assert urls[0] == "https://cdn.jsdelivr.net/gh/me/news-bundles@main/bundles/2026-09-22.json"
    assert "2026-09-22" in urls[1]
    assert urls[2].startswith("https://raw.githubusercontent.com/")


def test_mirror_urls_empty_without_repo(tmp_path: Path) -> None:
    assert mirror_urls(_config(tmp_path, repo=""), "2026-09-22") == []


def test_fallback_prefers_today_then_most_recent(tmp_path: Path) -> None:
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)

    (bundles / "2026-09-20.json").write_text("{}", encoding="utf-8")
    (bundles / "2026-09-22.json").write_text("{}", encoding="utf-8")

    path, why = fallback_local(bundles, "2026-09-22")
    assert path is not None and path.name == "2026-09-22.json"
    assert "当天" in why

    # 今天的没有 → 退回最近一天，并且要说清楚它不是当天的
    path, why = fallback_local(bundles, "2026-09-23")
    assert path is not None and path.name == "2026-09-22.json"
    assert "非当天" in why


def test_fallback_reports_nothing_when_empty(tmp_path: Path) -> None:
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    path, why = fallback_local(bundles, "2026-09-22")
    assert path is None and why == ""


def test_bundle_payload_roundtrips_to_items(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "date": "2026-09-22",
        "generated_at": "2026-09-22T22:35:12Z",
        "mode": "collector",
        "sources": [{"id": "ign", "ok": True, "count": 1, "error": None}],
        "items": [
            {
                "source_id": "ign",
                "domain": "game",
                "url": "https://www.ign.com/articles/x?utm_source=rss",
                "title": "Some English headline",
                "title_en": "Some English headline",
                "published_at": "2026-09-22T18:00:00Z",
                "lang": "en",
                "excerpt": "short",
                "content_text": "正文",
            }
        ],
    }

    path = tmp_path / "b.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = read_payload(path)
    assert loaded is not None

    items = items_from_payload(loaded)
    assert len(items) == 1
    assert items[0].source_id == "ign"
    assert items[0].content_text == "正文"
