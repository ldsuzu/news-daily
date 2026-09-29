"""API Key 的存放：优先 Windows 凭据管理器。

为什么不用配置文件 —— 这个仓库是公开的，而配置文件是要提交的。
凭据管理器把密钥交给操作系统加密保管，配置文件里始终留空。

读取顺序：凭据管理器 → 环境变量 NEWSPIPE_LLM_KEY → 内存暂存。
最后那档是 keyring 完全不可用时的退路：只放内存，进程退出即失效 ——
宁可每次重新输入，也不把明文写到磁盘上。
"""

from __future__ import annotations

import os

SERVICE = "newspipe"
ACCOUNT = "llm-api-key"

_in_memory: str = ""


def _keyring():
    """拿 keyring 模块；导入或初始化失败都返回 None，调用方走退路。"""
    try:
        import keyring

        keyring.get_keyring()          # 后端探测：拿不到就抛
        return keyring
    except Exception:  # noqa: BLE001 —— 后端缺失、依赖不全都算「用不了」
        return None


def get_api_key() -> str:
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE, ACCOUNT)
            if value:
                return value.strip()
        except Exception:  # noqa: BLE001
            pass

    env = os.environ.get("NEWSPIPE_LLM_KEY", "").strip()
    if env:
        return env
    return _in_memory


def set_api_key(value: str) -> bool:
    """存进凭据管理器。返回 True 表示真的落到系统里了，False 表示只在内存。"""
    global _in_memory
    value = (value or "").strip()

    if not value:
        return delete_api_key()

    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, ACCOUNT, value)
            _in_memory = ""
            return True
        except Exception:  # noqa: BLE001
            pass

    _in_memory = value
    return False


def delete_api_key() -> bool:
    global _in_memory
    _in_memory = ""
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, ACCOUNT)
            return True
        except Exception:  # noqa: BLE001 —— 本来就没有，也算删除成功
            return True
    return False


def has_api_key() -> bool:
    return bool(get_api_key())


def where_is_it() -> str:
    """凭据管理器 / 环境变量 / 内存 / 没配 —— 设置页用它说明 Key 存在哪。"""
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(SERVICE, ACCOUNT):
                return "credential"
        except Exception:  # noqa: BLE001
            pass
    if os.environ.get("NEWSPIPE_LLM_KEY", "").strip():
        return "env"
    if _in_memory:
        return "memory"
    return "none"
