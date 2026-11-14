"""文明6窗口级输入；窗口前台校验与 FireTuner 运营逻辑分离。"""

from __future__ import annotations


def right_click_game_window(win32_modules=None, sleep=None) -> str:
    """确认游戏窗口已在前台后才右键中央；激活失败时绝不点击其他窗口。

    ``win32_modules`` 与 ``sleep`` 仅供无头测试注入，生产路径始终使用 ctypes 兼容层。
    """
    import time as _time

    sleep = sleep or _time.sleep
    if win32_modules is None:
        try:
            from . import _win32 as w32
        except ImportError:  # 包外直接加载
            import _win32 as w32  # type: ignore
        gui, con, mouse = w32.win32gui, w32.win32con, w32
        process_name = w32.window_process_name
    else:
        gui, con, mouse = win32_modules
        process_name = getattr(mouse, "window_process_name", None)

    hwnd = None
    hwnd_rect = None

    def find_game_window(candidate, _param):
        nonlocal hwnd, hwnd_rect
        if hwnd is not None or not gui.IsWindowVisible(candidate) or process_name is None:
            return
        try:
            executable = str(process_name(candidate)).replace("\\", "/").rsplit("/", 1)[-1].lower()
        except Exception:
            return
        # 标题可被浏览器/资源管理器伪造；只接受文明6游戏进程，失败时宁肯不点击。
        if executable.startswith("civilizationvi") and executable.endswith(".exe"):
            hwnd = candidate
            hwnd_rect = gui.GetWindowRect(candidate)

    try:
        gui.EnumWindows(find_game_window, None)
    except Exception as exc:
        return f"（枚举游戏窗口失败，未右键：{exc}）"
    if hwnd is None or hwnd_rect is None:
        return "（未找到游戏窗口，无法右键）"

    try:
        gui.ShowWindow(hwnd, con.SW_RESTORE)
        if not gui.SetForegroundWindow(hwnd):
            return "（游戏窗口激活失败，已取消右键）"
        sleep(0.6)
        if gui.GetForegroundWindow() != hwnd:
            return "（游戏窗口激活失败，已取消右键）"
    except Exception as exc:
        return f"（游戏窗口激活失败，已取消右键：{exc}）"

    try:
        x1, y1, x2, y2 = hwnd_rect
        mouse.mouse_right_click_abs((x1 + x2) // 2, (y1 + y2) // 2)
        sleep(1.5)
        return "（已激活游戏窗口并右键中央）"
    except Exception as exc:
        return f"（右键失败：{exc}）"
