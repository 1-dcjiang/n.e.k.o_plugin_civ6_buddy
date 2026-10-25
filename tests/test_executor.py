"""civ6_ops 执行通道测试：懒加载遮蔽回归 + PrintWindow 错误路径。

真实窗口截图无法在无头 CI 下稳定复现，这里覆盖：
1. 懒加载方法名≠属性名（回归：_pg/_PIL 同名遮蔽曾导致所有截图/键鼠调用 TypeError）
2. PrintWindow 对无效窗口给出稳定错误码（不崩溃）
"""
import pytest

from civ6_ops import _Executor, _front_and_size, OpsError


def test_executor_lazy_load_returns_modules():
    ex = _Executor()
    # 方法可调用且返回模块（pyautogui / PIL.Image）
    pg = ex._pg_instance()
    pil = ex._pil_instance()
    assert pg is not None
    assert pil is not None
    assert hasattr(pg, "screenshot")
    assert hasattr(pil, "open")


def test_executor_methods_do_not_shadow_attributes():
    ex = _Executor()
    # 回归：方法名与属性名曾经同名（_pg/_PIL），实例属性遮蔽类方法，
    # 导致 `self._pg()` 解析为 None 而 TypeError: 'NoneType' object is not callable。
    assert callable(ex._pg_instance)
    assert callable(ex._pil_instance)
    # 属性与方法是不同名字
    assert hasattr(ex, "_pg_mod")
    assert hasattr(ex, "_pil_mod")


def test_capture_printwindow_invalid_hwnd_raises_stable_code():
    ex = _Executor()
    # 无效窗口句柄 → PrintWindow 通道必须给出稳定错误码（而不是裸崩溃）
    with pytest.raises(OpsError):
        ex.capture({"hwnd": 0, "left": 0, "top": 0, "right": 10, "bottom": 10})
