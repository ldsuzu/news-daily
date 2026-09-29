# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出一个便携目录，双击「每日简报.exe」就能用。

用 onedir 而不是 onefile：onefile 每次启动都要把上百 MB 解压到临时目录，
既慢又容易被杀毒软件盯上；而便携目录还有个好处 —— config/ 和 data/ 就在旁边，
看得见、摸得着、拷走就能带走全部历史。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / "src" / "newspipe" / "web" / "templates"), "newspipe/web/templates"),
    (str(ROOT / "src" / "newspipe" / "web" / "static"), "newspipe/web/static"),
    (str(ROOT / "src" / "newspipe" / "storage" / "schema.sql"), "newspipe/storage"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "README.md"), "."),
] + collect_data_files("trafilatura")        # 正文抽取的语言数据文件

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "webview.platforms.edgechromium",
] + collect_submodules("keyring.backends") + collect_submodules("trafilatura")

a = Analysis(
    [str(ROOT / "src" / "newspipe" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="每日简报",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="每日简报",
)
