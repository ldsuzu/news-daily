"""原生窗口：起一个本地服务线程，再用系统 WebView 把界面摆出来。

和 `newspipe serve` 的唯一区别是「谁负责显示」——
serve 让浏览器去开一个标签页，desktop 自己开一个没有地址栏、没有标签栏、
能做最小化和记忆尺寸的窗口。界面代码两边完全共用。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

TITLE = "每日简报"
DEFAULT_W, DEFAULT_H = 1180, 760
MIN_W, MIN_H = 900, 600


def _free_port() -> int:
    """让系统随便给一个没被占用的端口 —— 用户不该关心端口号。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _geometry_path(root: Path) -> Path:
    return root / "data" / "window.json"


def _load_geometry(root: Path) -> dict[str, int]:
    try:
        data = json.loads(_geometry_path(root).read_text(encoding="utf-8"))
        return {
            "width": max(MIN_W, int(data.get("width", DEFAULT_W))),
            "height": max(MIN_H, int(data.get("height", DEFAULT_H))),
        }
    except Exception:  # noqa: BLE001 —— 文件不存在或坏了都回到默认尺寸
        return {"width": DEFAULT_W, "height": DEFAULT_H}


def _save_geometry(root: Path, window: Any) -> None:
    try:
        path = _geometry_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"width": int(window.width), "height": int(window.height)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 —— 记不住尺寸不值得让程序崩
        pass


def _wait_for_port(port: int, timeout: float = 12.0) -> bool:
    """等端口真的能连上再开窗口，否则会先闪一下「无法访问」。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_desktop(config: Any, *, port: int | None = None) -> int:
    import uvicorn

    from .web.app import create_app

    try:
        import webview
    except ImportError:
        print(
            "没装 pywebview，开不了原生窗口。\n"
            "  装它：pip install pywebview\n"
            "  或者退回浏览器方式：newspipe serve --open"
        )
        return 1

    app = create_app(config)
    port = port or _free_port()

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait_for_port(port):
        print(f"服务没能在 127.0.0.1:{port} 上起来，窗口没有打开。")
        return 1

    geo = _load_geometry(config.root)
    window = webview.create_window(
        TITLE,
        f"http://127.0.0.1:{port}",
        width=geo["width"],
        height=geo["height"],
        min_size=(MIN_W, MIN_H),
    )
    window.events.closing += lambda: _save_geometry(config.root, window)

    print(f"窗口已打开：{TITLE}（按关闭按钮退出）")
    webview.start()

    server.should_exit = True
    return 0
