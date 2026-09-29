"""让 `python -m newspipe` 可用；打包成 exe 后双击它就是打开原生窗口。

这里必须用**绝对导入**：PyInstaller 把本文件当作顶层脚本执行，
相对导入（from .cli import ...）会报 "attempted relative import with no known parent package"。
"""

import sys

from newspipe.cli import main

if __name__ == "__main__":
    # 双击 exe 时命令行是空的。这时默认走桌面窗口 —— 用户双击图标期待的是
    # "软件打开了"，而不是一份 argparse 帮助文本。
    # exe 一样接受显式参数（"每日简报.exe run" 仍会去抓取）。
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        sys.argv.append("desktop")
    raise SystemExit(main())

