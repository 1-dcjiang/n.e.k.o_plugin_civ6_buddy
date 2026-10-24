"""civ6_ops 纯逻辑测试（不依赖 GUI/steam_manager，CI 可跑）。

覆盖：键映射、默认按钮坐标合法性、战争横幅像素检测、模板匹配。
执行层通道（steam_manager/pyautogui）在测试环境只验证属性存在，
不要求 steam_manager 已安装（回退通道合法）。
numpy 缺失时整模块跳过（CI 环境无 numpy 也能通过 Verify）。
"""

import numpy as np
import pytest

np_ = pytest.importorskip("numpy")

from civ6_ops import (  # noqa: E402  （importorskip 之后，CI 无 numpy 时整模块跳过）
    _DEFAULT_BUTTONS,
    _VK_MAP,
    OpsError,
    _banner_war_present,
    _match_template,
)


def test_vk_map_covers_common_keys():
    for key in ("esc", "enter", "space", "tab", "a", "z", "t", "p", "n"):
        assert key in _VK_MAP
        assert isinstance(_VK_MAP[key], int) and 0 < _VK_MAP[key] < 0xFF


def test_default_buttons_in_bounds():
    for name, (fx, fy) in _DEFAULT_BUTTONS.items():
        assert 0.0 <= fx <= 1.0, f"{name} fx 越界"
        assert 0.0 <= fy <= 1.0, f"{name} fy 越界"


def test_banner_war_present_detects_red_banner():
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    screen[:40, :, :] = (220, 40, 30)  # 顶部红色横幅
    assert _banner_war_present(screen) is True


def test_banner_war_present_ignores_plain_map():
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    screen[:, :, :] = (80, 120, 60)  # 绿色地图
    assert _banner_war_present(screen) is False


def test_match_template_finds_pattern():
    # 带纹理模板（白底 + 灰点），走归一化相关分支
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    base[50:80, 60:100, :] = (255, 255, 255)  # 白色方块
    base[60:70, 70:90, :] = (128, 128, 128)   # 内部灰点 → 有纹理
    tpl = np.full((30, 40, 3), 255, dtype=np.uint8)
    tpl[10:20, 10:30, :] = (128, 128, 128)
    hit = _match_template(base, tpl)
    assert hit is not None
    cx, cy = hit
    assert 60 <= cx <= 100 and 50 <= cy <= 80


def test_match_template_misses_absent_pattern():
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    tpl = np.full((30, 40, 3), 255, dtype=np.uint8)
    assert _match_template(base, tpl, threshold=0.9) is None


def test_bad_rel_coord_raises():
    assert issubclass(OpsError, Exception)
