"""随插件发布的 FireTuner Lua 资源加载器。

Lua 源码只保存在 ``lua/*.lua``；Python 侧仅对既有占位符求值，不再内嵌大段脚本。
资源名是固定白名单式文件名，拒绝路径分隔符，避免把游戏参数当成资源路径。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

__all__ = ["load_lua", "lua_resource_path"]

_LUA_ROOT = Path(__file__).resolve().parent / "lua"
_RESOURCE_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


def lua_resource_path(name: str) -> Path:
    """返回已校验资源名的路径；不存在时给出明确部署错误。"""
    if not _RESOURCE_NAME.fullmatch(name):
        raise ValueError(f"非法 Lua 资源名：{name!r}")
    path = _LUA_ROOT / f"{name}.lua"
    if not path.is_file():
        raise FileNotFoundError(f"插件缺少 Lua 资源：{path}")
    return path


@lru_cache(maxsize=None)
def load_lua(name: str) -> str:
    """读取发布包中的 UTF-8 Lua 资源并缓存；换行与脚本内容原样保留。"""
    return lua_resource_path(name).read_text(encoding="utf-8")
