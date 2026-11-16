"""2026-11-14 增量深审：托管主链路行为回归（不连接真实游戏）。"""

from __future__ import annotations

import asyncio
import importlib
import struct
import time
from pathlib import Path

import pytest

_main = importlib.import_module("plugin.plugins.civ6_buddy")
_ops = importlib.import_module("civ6_ops")
_parser = importlib.import_module("civ6_parser")
_protocol = importlib.import_module("civ6_tuner_protocol")
_sdk = importlib.import_module("civ6_sdk")
_tuner = importlib.import_module("civ6_tuner")


class _Writer:
    def __init__(self):
        self.closed = False
        self.payloads = []

    def write(self, payload):
        self.payloads.append(payload)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


def _bare_plugin():
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._saves_dir = ""
    plugin._ops_buttons = {}
    plugin._cfg_loaded = True
    plugin._ops_active = True
    plugin._ops_tick_lock = asyncio.Lock()
    plugin._ops_stop_event = asyncio.Event()
    plugin._ops_screen_cancel = None
    plugin._ops_screen_thread = None
    plugin._ops_cleanup_lock = asyncio.Lock()

    async def cleanup(deadline=None):
        return {"ok": True, "registered": False}

    plugin._cleanup_managed_runtime = cleanup
    return plugin


def _fake_tuner_io(monkeypatch, responder, states=None):
    calls = []
    writer = _Writer()

    async def handshake(*_args, **_kwargs):
        return "APP", states or ["3", "GameCore_Tuner", "27", "InGame"], object(), writer

    async def execute(_reader, _writer, code, state_index=0, timeout=0):
        calls.append({"code": code, "state_index": state_index, "timeout": timeout})
        return responder(code)

    monkeypatch.setattr(_tuner, "_handshake_retry", handshake)
    monkeypatch.setattr(_tuner, "execute_lua", execute)
    return calls, writer


def _manage_stubs(monkeypatch, blockers=None, turn=9, **overrides):
    calls = []
    values = {
        "close_ui_popups": {"ok": True, "closed": 0, "nw_closed": True},
        "read_blockers": {"ok": True, "blockers": blockers or []},
        "register_wc_voter": {"ok": True, "registered": True},
        "auto_manage_money": {"ok": True, "action": ""},
        "auto_found_religion": {"ok": True, "founded": False},
        "auto_recruit_great_person": {"ok": True, "recruited": False},
        "auto_trade_route": {"ok": True, "routes": []},
        "auto_manage_spy": {"ok": True, "has_spy": False},
        "auto_war_response": {"ok": True, "at_war": False},
        "end_turn": {"ok": True},
        "read_situation": {"ok": True, "turn": turn},
    }
    values.update(overrides)
    for name, value in values.items():
        if callable(value):
            monkeypatch.setattr(_main.civ6_tuner, name, value)
            continue

        async def stub(*_args, _value=value, _name=name, **_kwargs):
            calls.append(_name)
            return _value

        monkeypatch.setattr(_main.civ6_tuner, name, stub)
    return calls


async def _no_sleep(_seconds):
    return None


@pytest.mark.parametrize("partial", [False, True])
def test_recv_body_or_partial_header_timeout_is_fatal(partial):
    async def scenario():
        reader = asyncio.StreamReader()
        if partial:
            reader.feed_data(b"\x05\x00")
        else:
            reader.feed_data(struct.pack(_protocol.HEADER_FMT, 5, _protocol.TAG_COMMAND) + b"x")
        with pytest.raises(_protocol.TunerError) as exc_info:
            await _protocol._recv(reader, timeout=0.01)
        assert not isinstance(exc_info.value, _protocol.TunerTimeout)

    asyncio.run(scenario())


def test_recv_disconnect_is_tuner_error():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_eof()
        with pytest.raises(_protocol.TunerError):
            await _protocol._recv(reader, timeout=0.01)

    asyncio.run(scenario())


def test_execute_lua_waits_beyond_old_two_second_cutoff():
    async def scenario():
        reader = asyncio.StreamReader()
        writer = _Writer()

        async def delayed_output():
            await asyncio.sleep(2.15)
            reader.feed_data(_protocol._pack(_protocol.TAG_COMMAND, "O\x00InGame: DONE"))

        feeder = asyncio.create_task(delayed_output())
        started = time.monotonic()
        lines = await _protocol.execute_lua(reader, writer, "print('DONE')", timeout=3.0)
        await feeder
        assert lines == ["DONE"]
        assert time.monotonic() - started >= 2.0

    asyncio.run(scenario())


def test_execute_lua_propagates_fatal_protocol_error(monkeypatch):
    async def no_drain(*_args, **_kwargs):
        return None

    async def no_send(*_args, **_kwargs):
        return None

    async def disconnected(*_args, **_kwargs):
        raise _protocol.TunerError("connection lost")

    monkeypatch.setattr(_protocol, "_drain", no_drain)
    monkeypatch.setattr(_protocol, "_send", no_send)
    monkeypatch.setattr(_protocol, "_recv", disconnected)

    async def scenario():
        with pytest.raises(_protocol.TunerError, match="connection lost"):
            await _protocol.execute_lua(object(), object(), "print(1)", timeout=1.0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "item", "parameter", "table", "extra"),
    [
        ("unit", "UNIT_SCOUT", "PARAM_UNIT_TYPE", "Units", {}),
        ("building", "BUILDING_MONUMENT", "PARAM_BUILDING_TYPE", "Buildings", {}),
        ("district", "DISTRICT_CAMPUS", "PARAM_DISTRICT_TYPE", "Districts",
         {"city_id": 2, "plot_x": 4, "plot_y": 5}),
        ("project", "PROJECT_LAUNCH_EARTH_SATELLITE", "PARAM_PROJECT_TYPE", "Projects", {}),
    ],
)
def test_production_uses_correct_parameters_and_dynamic_ingame(
    monkeypatch, kind, item, parameter, table, extra,
):
    expected = 1 if kind == "district" else 2

    def respond(code):
        if "CityManager.RequestOperation" in code:
            return [f"REQUESTED={expected}|OCCUPIED=0|REJECTED="]
        return [f"SET|done={expected}|failed=0|size={expected}|ids="]

    calls, writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_tuner.set_city_production(kind, item, timeout=3.0, **extra))
    assert result["ok"] is True
    assert result["changed"] == expected
    request = calls[0]["code"]
    assert f"GameInfo.{table}" in request
    assert f"CityOperationTypes.{parameter}" in request
    assert "os.clock" not in request
    assert sum("CityManager.RequestOperation" in call["code"] for call in calls) == 1
    assert all(call["state_index"] == 27 for call in calls)
    assert writer.closed is True
    if kind == "district":
        assert "params[CityOperationTypes.PARAM_X] = 4" in request
        assert "params[CityOperationTypes.PARAM_Y] = 5" in request
    else:
        assert "for pCity in Players[me]:GetCities():Members()" in request


def test_district_requires_explicit_location_before_connecting(monkeypatch):
    async def forbidden():
        raise AssertionError("缺区域坐标不得连接游戏")

    monkeypatch.setattr(_tuner, "_handshake_retry", forbidden)
    result = asyncio.run(_tuner.set_city_production("district", "DISTRICT_CAMPUS"))
    assert result["error"] == "DISTRICT_LOCATION_REQUIRED"


def test_production_wrong_hash_never_reports_success_and_never_rewrites(monkeypatch):
    responses = []

    def respond(code):
        if "CityManager.RequestOperation" in code:
            responses.append("request")
            return ["REQUESTED=2|OCCUPIED=0|REJECTED="]
        responses.append("verify")
        return ["SET|done=0|failed=2|size=1|ids=1,2"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_tuner.set_city_production("unit", "UNIT_SCOUT", timeout=3.0))
    assert result["ok"] is False
    assert result["error"] == "SILENT_FAILURE"
    assert responses == ["request", "verify", "verify", "verify"]
    assert len(calls) == 4


def test_auto_production_quota_scales_with_cities(monkeypatch):
    async def economy(timeout=0):
        return {"ok": True, "population": 8, "cities": 2, "scouts": 2,
                "builders": 2, "warriors": 1, "districts": 0}

    monkeypatch.setattr(_tuner, "read_economy", economy)
    result = asyncio.run(_tuner.auto_decide_production())
    assert result["ok"] is True
    assert result["item"] == "UNIT_SCOUT"  # 2 城目标 3 侦察，不再按旧“各 2 就停”交还
    assert "3" in result["reason"]


def test_auto_production_stops_after_extended_quotas(monkeypatch):
    async def economy(timeout=0):
        return {"ok": True, "population": 8, "cities": 2, "scouts": 4,
                "builders": 6, "warriors": 2, "districts": 0}

    monkeypatch.setattr(_tuner, "read_economy", economy)
    result = asyncio.run(_tuner.auto_decide_production())
    assert result["ok"] is False
    assert result["error"] == "NO_REASONABLE_PRODUCTION"


def test_ingame_index_comes_from_interleaved_lsq():
    assert _tuner._ingame_state_index(["0", "Main State", "41", "InGame", "3", "GameCore_Tuner"]) == 41


def test_threat_logic_uses_unit_owner_war_and_combat_not_plot_owner(monkeypatch):
    scripts = []

    def respond(code):
        scripts.append(code)
        return ["THREATS|"] if "THREATS|" in code else ["EVADED="]

    _fake_tuner_io(monkeypatch, respond)
    assert asyncio.run(_tuner.read_threats())["ok"] is True
    assert asyncio.run(_tuner.auto_evade_threats())["ok"] is True
    assert len(scripts) == 2
    for lua in scripts:
        assert "IsAtWarWith" in lua
        assert "IsBarbarian" in lua
        assert "cs > 0" in lua
        assert "p:GetOwner()" not in lua


def test_policy_uses_dense_picked_count_and_reads_back(monkeypatch):
    def respond(code):
        if "RequestPolicyChanges" in code:
            return ["REQUESTED=1&SLOTS=POLICY_URBAN_PLANNING"]
        return ["POLICY|done=1|failed=0"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(_tuner.auto_fill_policy_slots())
    assert result["filled"] == 1
    assert result["verified"] is True
    lua = calls[0]["code"]
    assert "if #picked > 0 then" in lua
    assert "if #addList > 0" not in lua
    assert "GetSlotPolicy" in calls[-1]["code"]
    assert "local want_count = 1" in calls[-1]["code"]


def test_policy_reports_silent_failure_when_readback_does_not_match(monkeypatch):
    def respond(code):
        if "RequestPolicyChanges" in code:
            return ["REQUESTED=1&SLOTS=POLICY_URBAN_PLANNING"]
        return ["POLICY|done=0|failed=1"]

    _calls, _writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_tuner.auto_fill_policy_slots())
    assert result["ok"] is False
    assert result["error"] == "SILENT_FAILURE"


def test_research_reads_back_before_reporting_success(monkeypatch):
    def respond(code):
        if "RequestPlayerOperation" in code:
            return ["OK:RESEARCHING|TECH_POTTERY|idx=4|cleared=false"]
        return ["VERIFY|current=4|want=4"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(_tuner.set_research("tech", "TECH_POTTERY"))
    assert result["ok"] is True, result
    assert result["verified"] is True
    assert "GetResearchingTech" in calls[-1]["code"]
    assert calls[0]["code"].count("RequestPlayerOperation") == 1


def test_research_silent_failure_does_not_pick_again(monkeypatch):
    def respond(code):
        if "RequestPlayerOperation" in code:
            return ["OK:RESEARCHING|TECH_POTTERY|idx=4|cleared=false"]
        return ["VERIFY|current=3|want=4"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_tuner.set_research("tech", "TECH_POTTERY", timeout=3.0))
    assert result["ok"] is False
    assert result["error"] == "SILENT_FAILURE"
    assert all("RequestPlayerOperation" not in call["code"] for call in calls[1:])


def test_governor_reads_back_appointment(monkeypatch):
    def respond(code):
        if "APPOINT_GOVERNOR" in code:
            return ["RESULT|GOVERNOR_TYPE|测试总督|首都"]
        return ["GOV|found=true"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(_tuner.auto_appoint_governor())
    assert result["ok"] is True
    assert result["verified"] is True
    assert "GetGovernorsAppointed" in calls[-1]["code"]


def test_commemoration_reads_back_count_change(monkeypatch):
    def respond(code):
        if "COMMEMORATE" in code:
            return ["RESULT|COMMEMORATION"]
        return ["COMM|allowed=0|before=1|still=true"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(_tuner.auto_choose_commemoration())
    assert result["ok"] is True
    assert result["verified"] is True
    assert "GetPlayerNumAllowedCommemorations" in calls[-1]["code"]


def test_close_popups_does_not_dismiss_notifications_or_right_click(monkeypatch):
    def respond(_code):
        return ["CLOSED=0&NW=true&MENU=false"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)

    def forbidden_click():
        raise AssertionError("弹窗不得通过游戏中心右键猜测关闭")

    monkeypatch.setattr(_tuner, "_right_click_game_window", forbidden_click)
    result = asyncio.run(_tuner.close_ui_popups())
    assert result["ok"] is False
    assert result["error"] == "POPUP_UNRESOLVED"
    assert result["nw_closed"] is False
    assert "NotificationManager" not in calls[0]["code"]


def test_world_congress_handler_is_removed(monkeypatch):
    def respond(_code):
        return ["WC_UNREGISTERED|ok"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(_tuner.unregister_wc_voter())
    assert result["ok"] is True
    assert "Events.WorldCongressStage1.Remove(__civ6_wc_handler)" in calls[0]["code"]
    assert "__civ6_wc_handler = nil" in calls[0]["code"]


def test_units_with_moves_execute_evade_builder_and_explorer(monkeypatch):
    executed = []

    def record(name, result=None):
        async def func(*_args, **_kwargs):
            executed.append(name)
            return result or {"ok": True}

        return func

    blockers = [{"type": "ENDTURN_BLOCKING_UNITS"}]
    _manage_stubs(
        monkeypatch, blockers,
        units_blocker_status=record("status", {"ok": True, "units_with_moves": ["SCOUT:2"],
                                               "skip_idle_ok": False}),
        auto_evade_threats=record("evade"), auto_use_builder=record("builder"),
        move_explorers=record("explorer"),
        skip_idle_units=record("skip", {"ok": False, "detail": "stop after unit test"}),
    )
    monkeypatch.setattr(_main.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_bare_plugin()._tuner_manage_turn(9, False, "unit", "UNIT_SCOUT", "TECH", "CIVIC"))
    assert result["handoff"] is True
    assert executed == ["status", "evade", "builder", "explorer", "skip"]


def test_war_hands_off_before_any_unit_movement(monkeypatch):
    """从完整 tick 入口侦测战争，不向回合管理器注入 war_flag。"""
    plugin = _bare_plugin()
    plugin._ops_active = True

    async def tuner_status(*_args, **_kwargs):
        return {"ok": True}

    async def at_war(*_args, **_kwargs):
        return {"ok": True, "at_war": 1}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("战争时不得读单位、避险、改良、探路、跳过或结束回合")

    monkeypatch.setattr(_main.civ6_tuner, "tuner_status", tuner_status)
    monkeypatch.setattr(_main.civ6_tuner, "at_war", at_war)
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)
    for name in (
        "units_blocker_status", "auto_evade_threats", "auto_use_builder", "move_explorers",
        "skip_idle_units", "end_turn",
    ):
        monkeypatch.setattr(_main.civ6_tuner, name, forbidden)
    result = asyncio.run(plugin.civ6_ops_tick(loop_turns=1))
    assert result["handoff"] is True
    assert result["reason"] == "WAR_TUNER"
    assert result["active"] is False
    assert "战争" in result["output"]


def test_end_turn_is_sent_once_without_right_click_self_healing(monkeypatch):
    calls = _manage_stubs(monkeypatch, turn=9)

    async def forbidden_click():
        raise AssertionError("未推进也不得右键")

    monkeypatch.setattr(_main.civ6_tuner, "_right_click_game_window", forbidden_click)
    monkeypatch.setattr(_main.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_bare_plugin()._tuner_manage_turn(9, False, "unit", "UNIT_SCOUT", "TECH", "CIVIC"))
    assert result["reason"] == "TURN_NOT_ADVANCED"
    assert calls.count("end_turn") == 1
    assert calls.count("read_situation") == 12
    assert calls.count("close_ui_popups") == 2


def test_second_tick_is_rejected_not_queued(monkeypatch):
    plugin = _bare_plugin()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_self, cancel_event=None, **_kwargs):
        started.set()
        await release.wait()
        return {"ok": True, "handoff": False}

    monkeypatch.setattr(_main.Civ6BuddyPlugin, "_civ6_ops_tick_impl", blocked)

    async def scenario():
        first = asyncio.create_task(plugin.civ6_ops_tick())
        await started.wait()
        second = await plugin.civ6_ops_tick()
        assert second["error"] == "OPS_TICK_BUSY"
        release.set()
        assert (await first)["handoff"] is False

    asyncio.run(scenario())


def test_handoff_cancels_current_tick_wait(monkeypatch):
    plugin = _bare_plugin()
    started = asyncio.Event()
    after_wait = []

    async def blocked(_self, cancel_event=None, **_kwargs):
        started.set()
        await asyncio.sleep(60)
        after_wait.append(True)
        return {"ok": True, "handoff": False}

    monkeypatch.setattr(_main.Civ6BuddyPlugin, "_civ6_ops_tick_impl", blocked)
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)

    async def scenario():
        task = asyncio.create_task(plugin.civ6_ops_tick())
        await started.wait()
        handoff = await plugin.civ6_ops_handoff()
        result = await asyncio.wait_for(task, timeout=1.0)
        assert handoff["active"] is False
        assert result["error"] == "OPS_CANCELLED"
        assert not after_wait
        assert plugin._ops_stop_event.is_set()

    asyncio.run(scenario())


def test_protocol_failure_is_not_misreported_as_total_budget_timeout(monkeypatch):
    plugin = _bare_plugin()

    async def failed(_self, **_kwargs):
        # 主入口使用包内协议模块；低层协议用例则独立验证直接导入路径。
        raise _main.civ6_tuner_protocol.TunerError("frame body failed")

    monkeypatch.setattr(_main.Civ6BuddyPlugin, "_civ6_ops_tick_impl", failed)
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)
    result = asyncio.run(plugin.civ6_ops_tick())
    assert result["error"] == "TUNER_EXEC_FAILED"
    assert result["error"] != "OPS_TIMEOUT"
    assert plugin._ops_active is False
    assert plugin._ops_tick_lock.locked() is False


def test_public_production_tool_passes_city_and_plot(monkeypatch):
    plugin = _bare_plugin()
    received = {}

    async def production(*args, **kwargs):
        received.update(kwargs)
        return {"ok": True, "detail": "verified", "queue_size": 1, "changed": 1}

    monkeypatch.setattr(_main.civ6_tuner, "set_city_production", production)
    result = asyncio.run(plugin.civ6_ops_set_production(
        item="DISTRICT_CAMPUS", kind="district", city_id=3, plot_x=7, plot_y=8,
    ))
    assert result["is_error"] is False
    assert received == {"timeout": 12.0, "city_id": 3, "plot_x": 7, "plot_y": 8}


def test_app_options_inserts_into_real_debug_section_atomically(tmp_path):
    path = tmp_path / "AppOptions.txt"
    raw = (b"\xef\xbb\xbf[UserSettings]\r\nSomeOption 1\r\n[Debug]\r\nFoo 0\r\n"
           b"[Audio]\r\nVolume 5\r\n")
    path.write_bytes(raw)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is True
    assert Path(result["backup"]).read_bytes() == raw
    updated = path.read_bytes()
    assert updated.startswith(b"\xef\xbb\xbf")
    assert b"[Debug]\r\nFoo 0\r\nEnableTuner 1\r\n[Audio]" in updated
    assert updated.count(b"[Debug]") == 1
    assert b"Volume 5\r\n" in updated
    assert _sdk.ensure_tuner_enabled(path)["changed"] is False


def test_app_options_equals_form_is_idempotent(tmp_path):
    path = tmp_path / "AppOptions.txt"
    path.write_bytes(b"[Debug]\r\nEnableTuner = 1\r\n")
    original = path.read_bytes()
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is False
    assert path.read_bytes() == original


def test_tuner_enabled_ignores_other_sections_and_repairs_debug(tmp_path):
    path = tmp_path / "AppOptions.txt"
    path.write_bytes(b"[Other]\nEnableTuner 1\n[Debug]\nEnableTuner = 0\n")
    assert _sdk.tuner_enabled(path) is False
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is True
    assert path.read_bytes() == b"[Other]\nEnableTuner 1\n[Debug]\nEnableTuner 1\n"
    assert _sdk.tuner_enabled(path) is True


def test_app_options_non_utf8_is_preserved_on_write(tmp_path):
    path = tmp_path / "AppOptions.txt"
    raw = "[Debug]\r\nNote café\r\nEnableTuner 0\r\n".encode("cp1252")
    path.write_bytes(raw)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is True
    assert Path(result["backup"]).read_bytes() == raw
    updated = path.read_bytes()
    assert updated.decode("cp1252").startswith("[Debug]\r\nNote café")
    assert "EnableTuner 1" in updated.decode("cp1252")
    assert "cp1252" in result["detail"]


def test_app_options_utf16_is_preserved_on_write(tmp_path):
    path = tmp_path / "AppOptions.txt"
    text = "[Debug]\r\nEnableTuner 0\r\n[Audio]\r\nVolume 5\r\n"
    raw = b"\xff\xfe" + text.encode("utf-16-le")
    path.write_bytes(raw)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is True
    assert Path(result["backup"]).read_bytes() == raw
    updated = path.read_bytes()
    assert updated.startswith(b"\xff\xfe")
    assert updated[2:].decode("utf-16-le") == text.replace(
        "EnableTuner 0", "EnableTuner 1"
    )


def test_app_options_failed_replace_preserves_original(monkeypatch, tmp_path):
    path = tmp_path / "AppOptions.txt"
    raw = b"[Debug]\r\nEnableTuner 0\r\n"
    path.write_bytes(raw)

    def failed_replace(_src, _dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(_sdk.os, "replace", failed_replace)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is False
    assert result["error"] == "APP_OPTIONS_WRITE_FAILED"
    assert "simulated" in result["detail"]
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob("*.tmp"))
    assert Path(result["backup"]).read_bytes() == raw


def test_list_saves_stats_each_file_once_and_skips_vanished(monkeypatch, tmp_path):
    for name in ("A.Civ6Save", "B.Civ6Save"):
        (tmp_path / name).write_bytes(b"save")
    counts = {}
    original = Path.stat

    def stat_once(path, *args, **kwargs):
        counts[path.name] = counts.get(path.name, 0) + 1
        if path.name == "A.Civ6Save":
            raise FileNotFoundError("auto-save rotated")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_once)
    saves = _parser.list_saves(str(tmp_path))
    assert [item["rel"] for item in saves] == ["B.Civ6Save"]
    assert counts["A.Civ6Save"] == 1
    assert counts["B.Civ6Save"] == 1


def test_fake_game_title_cannot_trigger_right_click():
    class Gui:
        activations = 0

        @staticmethod
        def IsWindowVisible(_hwnd):
            return True

        @staticmethod
        def GetWindowText(_hwnd):
            return "Sid Meier's Civilization VI - browser"

        @staticmethod
        def GetWindowRect(_hwnd):
            return (0, 0, 800, 600)

        @staticmethod
        def EnumWindows(callback, _param):
            callback(123, None)

        @classmethod
        def SetForegroundWindow(cls, _hwnd):
            cls.activations += 1
            return True

        @staticmethod
        def ShowWindow(_hwnd, _command):
            return True

        @staticmethod
        def GetForegroundWindow():
            return 123

    class Mouse:
        calls = []

        @staticmethod
        def window_process_name(_hwnd):
            return r"C:\Program Files\Google\Chrome.exe"

        def mouse_right_click_abs(self, x, y):
            self.calls.append((x, y))

    mouse = Mouse()
    result = _tuner._right_click_game_window(
        win32_modules=(Gui, type("Con", (), {"SW_RESTORE": 9}), mouse), sleep=lambda _seconds: None,
    )
    assert "未找到" in result
    assert mouse.calls == []
    assert Gui.activations == 0
    assert _ops._verified_game_window(Gui, Mouse.window_process_name, 123) is False


def _screen_turn_setup(monkeypatch, screen):
    win = {"hwnd": 1, "left": 0, "top": 0, "right": 1000, "bottom": 800}
    monkeypatch.setattr(_ops, "_find_window", lambda: win)
    monkeypatch.setattr(_ops, "_front_and_size", lambda value: {**value, "fronted": True})
    monkeypatch.setattr(_ops, "_shot", lambda _win: screen)

    def forbidden_click(*_args):
        raise AssertionError("不确定画面不得点击")

    monkeypatch.setattr(_ops._EXEC, "click_abs", forbidden_click)


def test_screen_research_brightness_hands_off_without_clicks(monkeypatch):
    np = pytest.importorskip("numpy")
    _screen_turn_setup(monkeypatch, np.zeros((800, 1000, 3), dtype=np.uint8))
    result = _ops.run_one_turn_ops({}, war_state=False)
    assert result["handoff"] is True
    assert result["reason"] == "RESEARCH_PANEL_UNCERTAIN"


def test_screen_light_popup_hands_off_without_clicks(monkeypatch):
    np = pytest.importorskip("numpy")
    screen = np.full((800, 1000, 3), 150, dtype=np.uint8)
    screen[240:560, 300:700] = 255
    _screen_turn_setup(monkeypatch, screen)
    result = _ops.run_one_turn_ops({}, war_state=False)
    assert result["handoff"] is True
    assert result["reason"] == "POPUP_UNCERTAIN"


def test_screen_missing_next_template_never_uses_default_coordinate(monkeypatch):
    np = pytest.importorskip("numpy")
    screen = np.full((800, 1000, 3), 150, dtype=np.uint8)
    _screen_turn_setup(monkeypatch, screen)
    monkeypatch.setattr(_ops, "_template_click_if_found", lambda *_args, **_kwargs: False)
    result = _ops.run_one_turn_ops({}, war_state=False)
    assert result["handoff"] is True
    assert result["reason"] == "NEXT_BUTTON_NOT_CONFIRMED"
