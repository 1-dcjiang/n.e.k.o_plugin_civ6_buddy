"""civ6_buddy win32 兼容层（v0.4.11）。

插件进程运行在 N.E.K.O 打包环境（resources\\bin）下，**没有 pywin32**
（win32gui/win32api/win32con/win32process 均不可用）。本模块用 ctypes 调用
user32/kernel32 提供同名的 win32gui / win32api / win32con / win32process，
API 与 pywin32 常用调用兼容（civ6_ops 的窗口查找/前置逻辑无需改动）。

覆盖：
- win32gui.IsWindowVisible / GetWindowText / EnumWindows / GetWindowRect
  / ShowWindow / GetForegroundWindow / SetForegroundWindow / BringWindowToTop
- win32api.GetCurrentThreadId
- win32process.GetWindowThreadProcessId / AttachThreadInput
- win32con.SW_RESTORE 等常量
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── user32 签名 ──
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL

_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int

_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL

_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL

_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND

_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL

_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class win32gui:
    """win32gui 的 ctypes 等价实现。"""

    @staticmethod
    def IsWindowVisible(hwnd: int) -> bool:
        return bool(_user32.IsWindowVisible(hwnd))

    @staticmethod
    def GetWindowText(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def EnumWindows(callback, lParam) -> None:
        """枚举所有顶层窗口。callback(hwnd, lParam) 每次调用后返回 None/True 即继续
        （与 pywin32 语义一致：回调返回 0 才停止；ctypes 回调返回 None 会被转成 0，
        因此包装为恒 True）。"""
        @WNDENUMPROC
        def _wrap(hwnd, lparam):  # noqa: ANN001
            try:
                callback(hwnd, lparam)
            except Exception:
                pass
            return True
        _user32.EnumWindows(_wrap, lParam or 0)

    @staticmethod
    def GetWindowRect(hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (rect.left, rect.top, rect.right, rect.bottom)

    @staticmethod
    def ShowWindow(hwnd: int, cmdShow: int) -> bool:
        return bool(_user32.ShowWindow(hwnd, cmdShow))

    @staticmethod
    def GetForegroundWindow() -> int:
        return _user32.GetForegroundWindow()

    @staticmethod
    def SetForegroundWindow(hwnd: int) -> bool:
        return bool(_user32.SetForegroundWindow(hwnd))

    @staticmethod
    def BringWindowToTop(hwnd: int) -> bool:
        return bool(_user32.BringWindowToTop(hwnd))


class win32api:
    """win32api 的 ctypes 等价实现（civ6_ops 用到的子集）。"""

    @staticmethod
    def GetCurrentThreadId() -> int:
        return int(_kernel32.GetCurrentThreadId())


class win32process:
    """win32process 的 ctypes 等价实现（civ6_ops 用到的子集）。"""

    @staticmethod
    def GetWindowThreadProcessId(hwnd: int) -> tuple[int, int]:
        pid = wintypes.DWORD()
        tid = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (int(tid), int(pid.value))

    @staticmethod
    def AttachThreadInput(idAttach: int, idAttachTo: int, fAttach: bool) -> bool:
        return bool(_user32.AttachThreadInput(idAttach, idAttachTo, fAttach))


class win32con:
    """win32con 常量（civ6_ops 用到的子集）。"""

    SW_RESTORE = 9
    SW_SHOWNORMAL = 1
    SW_MINIMIZE = 6
    SW_MAXIMIZE = 3
