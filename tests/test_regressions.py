"""安全、协议流与资源生命周期回归测试（无需真实游戏）。"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import threading
import types
from types import SimpleNamespace

import pytest

_ops = importlib.import_module("civ6_ops")
_parser = importlib.import_module("civ6_parser")
_tuner = importlib.import_module("civ6_tuner")
try:
    _main = importlib.import_module("plugin.plugins.civ6_buddy")
except ModuleNotFoundError:
    # 轻量测试宿主只含 plugin SDK 时，装饰器无需真实运行时即可验证工具方法。
    sdk = types.ModuleType("plugin.sdk.plugin")

    class NekoPluginBase:
        pass

    def decorator(*_args, **_kwargs):
        return lambda obj: obj

    sdk.NekoPluginBase = NekoPluginBase
    sdk.lifecycle = decorator
    sdk.llm_tool = decorator
    sdk.neko_plugin = lambda obj: obj
    sdk.plugin_entry = decorator
    sys.modules["plugin.sdk.plugin"] = sdk
    _main = importlib.import_module("plugin.plugins.civ6_buddy")


@pytest.mark.parametrize(
    ("function_name", "args"),
    [
        ("set_city_production", ("unit", 'UNIT_SCOUT"] ; print("INJECTED") --')),
        ("set_research", ("tech", 'TECH_POTTERY" ; print("INJECTED") --')),
    ],
)
def test_lua_item_rejected_before_handshake(monkeypatch, function_name, args):
    called = False

    async def forbidden_handshake():
        nonlocal called
        called = True
        raise AssertionError("非法参数不得连接游戏")

    monkeypatch.setattr(_tuner, "_handshake_retry", forbidden_handshake)
    result = asyncio.run(getattr(_tuner, function_name)(*args))
    assert result["ok"] is False
    assert result["error"] == "BAD_ITEM"
    assert called is False


def test_drain_consumes_complete_stale_frames():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(_tuner._pack(_tuner.TAG_COMMAND, "stale"))
        await _tuner._drain(reader, timeout=0.01)
        # 清理结束后才到达的新帧必须完整保留；旧实现会留下 stale 包体污染它。
        reader.feed_data(_tuner._pack(_tuner.TAG_COMMAND, "fresh"))
        tag, payload = await _tuner._recv(reader, timeout=0.01)
        assert tag == _tuner.TAG_COMMAND
        assert payload == "fresh"

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["click_abs", "press"])
def test_executor_false_result_is_not_success(monkeypatch, method):
    class FailedInput:
        def mouse_click_abs(self, *_args):
            return False

        def press_key(self, *_args):
            return False

    executor = _ops._Executor.__new__(_ops._Executor)
    executor._sm = FailedInput()

    def no_fallback():
        raise AssertionError("False 表示已处理但失败，不得 fallback 重复输入")

    monkeypatch.setattr(_ops, "_win32_guard", no_fallback)
    with pytest.raises(_ops.OpsError) as exc_info:
        getattr(executor, method)(*(1, 2) if method == "click_abs" else ("enter",))
    assert exc_info.value.code == "INPUT_FAILED"


class _FakeTimedOutProcess:
    def __init__(self):
        self.returncode = -1
        self.killed = False
        self.communicate_calls = 0

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired("node", timeout)
        return "", "terminated"

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_parser_timeout_kills_reaps_and_removes_temp(monkeypatch, tmp_path):
    temp = tmp_path / "parser-output.json"
    process = _FakeTimedOutProcess()
    monkeypatch.setattr(_parser.tempfile, "mkstemp", lambda **_kwargs: (0, str(temp))) if hasattr(_parser, "tempfile") else None
    if not hasattr(_parser, "tempfile"):
        import tempfile

        monkeypatch.setattr(tempfile, "mkstemp", lambda **_kwargs: (0, str(temp)))
    monkeypatch.setattr(_parser.os, "fdopen", lambda *_args, **_kwargs: open(temp, "w", encoding="utf-8"))
    monkeypatch.setattr(_parser.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(_parser.Civ6Error) as exc_info:
        _parser._run_parser("fixture.Civ6Save")
    assert exc_info.value.code == "PARSE_TIMEOUT"
    assert process.killed is True
    assert process.communicate_calls == 2
    assert not temp.exists()


def test_parser_node_missing_removes_temp(monkeypatch, tmp_path):
    temp = tmp_path / "missing-node-output.json"
    import tempfile

    monkeypatch.setattr(tempfile, "mkstemp", lambda **_kwargs: (0, str(temp)))
    monkeypatch.setattr(_parser.os, "fdopen", lambda *_args, **_kwargs: open(temp, "w", encoding="utf-8"))

    def missing_node(*_args, **_kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(_parser.subprocess, "Popen", missing_node)
    with pytest.raises(_parser.Civ6Error) as exc_info:
        _parser._run_parser("fixture.Civ6Save")
    assert exc_info.value.code == "NODE_MISSING"
    assert not temp.exists()


class _FakeGui:
    activation_result = False
    foreground = 999
    clicks_enabled = True

    @staticmethod
    def IsWindowVisible(_hwnd):
        return True

    @staticmethod
    def GetWindowText(_hwnd):
        return "Sid Meier's Civilization VI"

    @classmethod
    def GetWindowRect(cls, _hwnd):
        return (10, 20, 810, 620)

    @classmethod
    def EnumWindows(cls, callback, _param):
        callback(123, None)

    @staticmethod
    def ShowWindow(_hwnd, _command):
        return True

    @classmethod
    def SetForegroundWindow(cls, _hwnd):
        return cls.activation_result

    @classmethod
    def GetForegroundWindow(cls):
        return cls.foreground


class _FakeMouse:
    def __init__(self):
        self.calls = []

    def mouse_right_click_abs(self, x, y):
        self.calls.append((x, y))


def test_right_click_activation_failure_never_clicks():
    mouse = _FakeMouse()
    gui = type("Gui", (_FakeGui,), {"activation_result": False, "foreground": 123})
    result = _tuner._right_click_game_window(
        win32_modules=(gui, SimpleNamespace(SW_RESTORE=9), mouse),
        sleep=lambda _seconds: None,
    )
    assert "激活失败" in result
    assert mouse.calls == []


def test_right_click_requires_verified_foreground_window():
    mouse = _FakeMouse()
    gui = type("Gui", (_FakeGui,), {"activation_result": True, "foreground": 999})
    result = _tuner._right_click_game_window(
        win32_modules=(gui, SimpleNamespace(SW_RESTORE=9), mouse),
        sleep=lambda _seconds: None,
    )
    assert "激活失败" in result
    assert mouse.calls == []


def _bare_plugin():
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._saves_dir = ""
    plugin._ops_buttons = {}
    plugin._cfg_loaded = True
    plugin._ops_active = False
    return plugin


def test_takeover_parse_failure_does_not_leave_ops_lock(monkeypatch):
    plugin = _bare_plugin()
    lock_calls = []
    monkeypatch.setattr(_main, "_ops_lock_set", lambda: lock_calls.append("set"))
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: lock_calls.append("clear"))

    def fail_parse(*_args, **_kwargs):
        raise _main.civ6_parser.Civ6Error("NO_SAVES", "test")

    monkeypatch.setattr(_main.civ6_parser, "parse_save", fail_parse)
    result = asyncio.run(plugin.civ6_ops_takeover())
    assert result["error"] == "NO_SAVES"
    assert plugin._ops_active is False
    assert lock_calls == []


def test_shutdown_clears_ops_state_and_lock(monkeypatch):
    plugin = _bare_plugin()
    plugin._ops_active = True
    cleared = []
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: cleared.append(True))
    result = asyncio.run(plugin.on_shutdown())
    assert result["status"] == "stopped"
    assert plugin._ops_active is False
    assert cleared == [True]


def test_status_blocker_read_failure_is_partial_not_safe(monkeypatch):
    plugin = _bare_plugin()

    async def situation(timeout=0):
        return {"ok": True, "turn": 7}

    async def production(timeout=0):
        return {"ok": True, "cities": []}

    async def blockers(timeout=0):
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": "read failed", "blockers": []}

    async def unavailable(timeout=0):
        return {"ok": False, "error": "TIMEOUT"}

    for name, value in {
        "read_situation": situation,
        "read_production": production,
        "read_blockers": blockers,
        "units_blocker_status": unavailable,
        "read_economy": unavailable,
        "read_diplomacy": unavailable,
        "read_culture": unavailable,
    }.items():
        monkeypatch.setattr(_main.civ6_tuner, name, value)

    result = asyncio.run(plugin.civ6_ops_status())
    assert result["partial"] is True
    assert "无法确认" in result["output"]
    assert "可以直接结束回合" not in result["output"]


def test_status_uses_one_total_budget(monkeypatch):
    plugin = _bare_plugin()
    calls = []
    monkeypatch.setattr(_main, "_OPS_STATUS_BUDGET", 0.01)

    async def situation(timeout=0):
        return {"ok": True, "turn": 7}

    async def slow_production(timeout=0):
        calls.append(("production", timeout))
        await asyncio.sleep(0.05)
        return {"ok": True, "cities": []}

    async def must_not_run(timeout=0):
        calls.append(("unexpected", timeout))
        return {"ok": False}

    monkeypatch.setattr(_main.civ6_tuner, "read_situation", situation)
    monkeypatch.setattr(_main.civ6_tuner, "read_production", slow_production)
    for name in ("read_blockers", "units_blocker_status", "read_economy", "read_diplomacy", "read_culture"):
        monkeypatch.setattr(_main.civ6_tuner, name, must_not_run)

    result = asyncio.run(plugin.civ6_ops_status())
    assert result["partial"] is True
    assert result["error"] == "STATUS_TIMEOUT"
    assert len(calls) == 1
    assert 0 < calls[0][1] <= 0.011  # Windows 单调时钟存在约 1ms 粒度误差。


def test_list_saves_disk_io_runs_off_event_loop(monkeypatch):
    plugin = _bare_plugin()
    main_thread = threading.get_ident()
    worker_threads = []

    def list_saves(_directory):
        worker_threads.append(threading.get_ident())
        return []

    monkeypatch.setattr(_main.civ6_parser, "list_saves", list_saves)
    result = asyncio.run(plugin.civ6_list_saves())
    assert result["saves"] == []
    assert worker_threads and worker_threads[0] != main_thread



@pytest.mark.parametrize(
    ("tool_name", "implementation_name"),
    [
        ("civ6_ops_takeover", "_civ6_ops_takeover_impl"),
        ("civ6_ops_tick", "_civ6_ops_tick_impl"),
    ],
)
def test_tool_cancellation_clears_ops_state(monkeypatch, tool_name, implementation_name):
    plugin = _bare_plugin()
    plugin._ops_active = True
    cleared = []
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: cleared.append(True))

    async def blocked(_self, **_kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(_main.Civ6BuddyPlugin, implementation_name, blocked, raising=False)

    async def scenario():
        task = asyncio.create_task(getattr(plugin, tool_name)())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert plugin._ops_active is False
    assert cleared == [True]


def test_tick_zero_budget_returns_before_any_game_call(monkeypatch):
    plugin = _bare_plugin()
    plugin._ops_active = True
    monkeypatch.setattr(_main, "_OPS_TICK_BUDGET", 0.0, raising=False)
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("预算耗尽后不得读取或操作游戏")

    monkeypatch.setattr(_main.civ6_tuner, "tuner_status", forbidden)
    result = asyncio.run(plugin.civ6_ops_tick(loop_turns=50))
    assert result["error"] == "OPS_TIMEOUT"
    assert result["handoff"] is True
    assert result["turns_done"] == 0
    assert plugin._ops_active is False


def test_screen_turn_expires_before_window_or_input():
    import time

    with pytest.raises(_ops.OpsError) as exc_info:
        _ops.run_one_turn_ops({}, deadline=time.monotonic() - 1)
    assert exc_info.value.code == "OPS_TIMEOUT"


@pytest.mark.parametrize("bad_turns", ["not-a-number", None, [], {}])
def test_tick_zero_budget_handles_invalid_turn_count(monkeypatch, bad_turns):
    plugin = _bare_plugin()
    plugin._ops_active = True
    monkeypatch.setattr(_main, "_OPS_TICK_BUDGET", 0.0)
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)
    result = asyncio.run(plugin.civ6_ops_tick(loop_turns=bad_turns))
    assert result["error"] == "OPS_TIMEOUT"
    assert "已完成 0/1 回合" in result["output"]


def test_screen_turn_stops_before_input_when_cancelled():
    class Cancelled:
        @staticmethod
        def is_set():
            return True

    def forbidden_window():
        raise AssertionError("取消后不得定位或操作游戏窗口")

    original = _ops._find_window
    _ops._find_window = forbidden_window
    try:
        with pytest.raises(_ops.OpsError) as exc_info:
            _ops.run_one_turn_ops({}, cancel_event=Cancelled())
    finally:
        _ops._find_window = original
    assert exc_info.value.code == "OPS_CANCELLED"


def test_tick_cancellation_signals_screen_worker(monkeypatch):
    plugin = _bare_plugin()
    plugin._ops_active = True
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)
    monkeypatch.setattr(_main, "_ops_lock_set", lambda: None)

    async def tuner_off():
        return {"ok": False}

    async def no_war():
        return {"ok": False}

    async def no_turn(**_kwargs):
        return {"ok": False}

    monkeypatch.setattr(_main.civ6_tuner, "tuner_status", tuner_off)
    monkeypatch.setattr(_main.civ6_tuner, "at_war", no_war)
    monkeypatch.setattr(_main.civ6_tuner, "read_situation", no_turn)
    monkeypatch.setattr(_main.civ6_parser, "parse_save", lambda *_args, **_kwargs: {"game_turn": 1})
    started = threading.Event()
    stopped = threading.Event()

    def screen_turn(*_args, cancel_event=None, **_kwargs):
        import time

        started.set()
        assert cancel_event is not None
        wait_deadline = time.monotonic() + 2
        while not cancel_event.is_set() and time.monotonic() < wait_deadline:
            time.sleep(0.01)
        assert cancel_event.is_set(), "宿主取消必须通知屏幕工作线程"
        stopped.set()
        raise _ops.OpsError("OPS_CANCELLED", "cancelled")

    monkeypatch.setattr(_main.civ6_ops, "run_one_turn_ops", screen_turn)

    async def scenario():
        task = asyncio.create_task(plugin.civ6_ops_tick())
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(stopped.wait, 1)

    asyncio.run(scenario())
    assert plugin._ops_active is False


def test_parser_bundle_does_not_run_upstream_cli():
    import os
    import shutil

    node = shutil.which("node.exe" if os.name == "nt" else "node")
    if node is None:
        pytest.skip("本机没有可直接执行的 node，跳过打包入口集成测试")
    result = subprocess.run(
        [node, str(_parser._WRAPPER)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 2
    assert result.stdout == "", "bundle 不得执行上游 util.inspect CLI 或污染 JSON 输出"
    assert "usage: node wrapper.js" in result.stderr


def test_handshake_cancellation_closes_writer(monkeypatch):
    protocol = importlib.import_module("civ6_tuner_protocol")

    class Writer:
        closed = False

        def close(self):
            self.closed = True

    writer = Writer()

    async def connected(*_args):
        return object(), writer

    async def blocked_drain(*_args, **_kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(protocol, "_connect", connected)
    monkeypatch.setattr(protocol, "_drain", blocked_drain)

    async def scenario():
        task = asyncio.create_task(protocol.handshake())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert writer.closed is True
