"""pytest 配置：从插件目录向上定位 N.E.K.O 源码根并注入 sys.path。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent  # .../plugins/civ6_buddy
_PLUGINS_ROOT = _PLUGIN_DIR.parent  # .../plugins
_NEKO_ROOT = _PLUGINS_ROOT.parent.parent  # N.E.K.O 源码根（含 plugin/ 与 utils/ 包）

for _p in (_PLUGIN_DIR, _NEKO_ROOT):
    # 根目录必须排在 neko/plugin 前，即使 pytest 已把它加入 sys.path 也要重排。
    if str(_p) in sys.path:
        sys.path.remove(str(_p))
    sys.path.insert(0, str(_p))

# pytest 收集 plugin/conftest.py 时会把 neko/plugin 放到 sys.path 前面，
# 使其中的 plugin/utils 抢先注册为顶层 utils；先固定宿主根目录的同名包，
# 再加载 plugin.sdk，否则 SDK 会找不到根目录的 utils.plugin_card_fields。
importlib.import_module("utils")
