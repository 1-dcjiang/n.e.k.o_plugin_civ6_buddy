"""v0.6.10 两轮复审修复的真实业务回归；不连接或写入真实游戏。"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

_main = importlib.import_module("plugin.plugins.civ6_buddy")
_protocol = importlib.import_module("civ6_tuner_protocol")
_resources = importlib.import_module("civ6_lua_resources")
_sdk = importlib.import_module("civ6_sdk")
_tuner = importlib.import_module("civ6_tuner")


class _Writer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fake_tuner_io(monkeypatch, responder):
    calls = []
    writer = _Writer()

    async def handshake(*_args, **_kwargs):
        return "APP", ["3", "GameCore_Tuner", "27", "InGame"], object(), writer

    async def execute(_reader, _writer, code, state_index=0, timeout=0):
        calls.append({"code": code, "state_index": state_index, "timeout": timeout})
        return responder(code)

    monkeypatch.setattr(_tuner, "_handshake_retry", handshake)
    monkeypatch.setattr(_tuner, "execute_lua", execute)
    return calls, writer


async def _no_sleep(_seconds):
    return None


_READBACK_CASES = [
    (
        "auto_manage_money", "CityOperationTypes.PURCHASE", "MONEY_VERIFY|",
        "MONEY|gold=200|builders=1|REQUESTED_BUILDER",
        "MONEY_VERIFY|supported=true|matched=true|gold=50|builders=2",
        "MONEY_VERIFY|supported=true|matched=false|gold=200|builders=1",
    ),
    (
        "auto_found_religion", "PlayerOperations.FOUND_RELIGION", "RELIGION_VERIFY|",
        "RELIGION_OK|RELIGION_BUDDHISM|BELIEF_RELIGIOUS_SETTLEMENTS|BELIEF_CHURCH_PROPERTY",
        "RELIGION_VERIFY|supported=true|matched=true|created=2",
        "RELIGION_VERIFY|supported=true|matched=false|created=-1",
    ),
    (
        "auto_recruit_great_person", "PlayerOperations.RECRUIT_GREAT_PERSON", "GP_VERIFY|",
        "GP|测试伟人", "GP_VERIFY|supported=true|matched=true|expected=7",
        "GP_VERIFY|supported=true|matched=false|expected=7",
    ),
    (
        "auto_trade_route", "UnitManager.RequestOperation", "TRADE_VERIFY|",
        "TRADE=trader@1,2->4,5", "TRADE_VERIFY|supported=true|matched=true|before=0|after=1",
        "TRADE_VERIFY|supported=true|matched=false|before=0|after=0",
    ),
    (
        "auto_choose_pantheon", "PlayerOperations.FOUND_PANTHEON", "PANTHEON_VERIFY|",
        "RESULT|BELIEF_DIVINE_SPARK|测试原因", "PANTHEON_VERIFY|supported=true|matched=true|actual=4",
        "PANTHEON_VERIFY|supported=true|matched=false|actual=-1",
    ),
    (
        "auto_send_envoy", "PlayerOperations.GIVE_INFLUENCE_TOKEN", "ENVOY_VERIFY|",
        "RESULT|测试城邦|dist=3",
        "ENVOY_VERIFY|supported=true|matched=true|envoys_before=0|envoys_after=1|tokens_before=1|tokens_after=0",
        "ENVOY_VERIFY|supported=true|matched=false|envoys_before=0|envoys_after=0|tokens_before=1|tokens_after=1",
    ),
    (
        "auto_promote_units", "exp:SetPromotion", "PROMOTION_VERIFY|",
        "RESULT|UNIT_SCOUT→PROMOTION_ALERTNESS|requested=true",
        "PROMOTION_VERIFY|supported=true|matched=true|done=1|expected=1",
        "PROMOTION_VERIFY|supported=true|matched=false|done=0|expected=1",
    ),
]


@pytest.mark.parametrize(
    ("name", "request_marker", "verify_marker", "request_line", "success_line", "_failure"),
    _READBACK_CASES,
)
def test_write_readback_success_runs_real_business_function(
    monkeypatch, name, request_marker, verify_marker, request_line, success_line, _failure,
):
    def respond(code):
        if verify_marker in code:
            return [success_line]
        assert request_marker in code
        return [request_line]

    calls, writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner, "_sleep", _no_sleep)
    result = asyncio.run(getattr(_tuner, name)(timeout=3.0))
    assert result["ok"] is True, result
    assert result["verified"] is True
    assert len(calls) == 2
    assert request_marker in calls[0]["code"]
    assert verify_marker in calls[1]["code"]
    assert writer.closed is True


@pytest.mark.parametrize(
    ("name", "request_marker", "verify_marker", "request_line", "_success", "failure_line"),
    _READBACK_CASES,
)
def test_write_readback_mismatch_never_rewrites_and_is_never_success(
    monkeypatch, name, request_marker, verify_marker, request_line, _success, failure_line,
):
    # 购买与商队的读回语义在 0.6.15 分开：金币没降＝请求被拒（MISMATCH），
    # 商队路线同回合必然还没计入（LAGGING）；两者都只读回一次就下结论，
    # 不再空轮询三次。其余写操作仍是"三次读回都不匹配＝不确定"。
    expected, expected_calls = {
        "auto_manage_money": ("VERIFY_MISMATCH", 2),
        "auto_trade_route": ("VERIFY_LAGGING", 2),
    }.get(name, ("VERIFY_UNCERTAIN", 4))

    def respond(code):
        return [failure_line] if verify_marker in code else [request_line]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner, "_sleep", _no_sleep)
    result = asyncio.run(getattr(_tuner, name)(timeout=3.0))
    assert result["ok"] is False, result
    assert result["error"] == expected, result
    assert result["verified"] is False
    assert "人工核实" in result["detail"] or "仍未见" in result["detail"] or "尚未增加" in result["detail"]
    assert len(calls) == expected_calls
    assert sum(request_marker in call["code"] for call in calls) == 1


@pytest.mark.parametrize(
    ("name", "request_marker", "verify_marker", "request_line", "_success", "_failure"),
    _READBACK_CASES,
)
def test_write_readback_unsupported_api_is_explicit(
    monkeypatch, name, request_marker, verify_marker, request_line, _success, _failure,
):
    def respond(code):
        if verify_marker in code:
            return [verify_marker + "supported=false|matched=false"]
        return [request_line]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(getattr(_tuner, name)(timeout=3.0))
    assert result["ok"] is False
    assert result["error"] == "VERIFY_UNSUPPORTED"
    assert "人工核实" in result["detail"]
    assert len(calls) == 2


def test_readback_protocol_failure_does_not_retry_purchase(monkeypatch):
    def respond(code):
        if "MONEY_VERIFY|" in code:
            raise _tuner.TunerError("controlled readback failure")
        return ["MONEY|gold=200|builders=1|REQUESTED_BUILDER"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    result = asyncio.run(_tuner.auto_manage_money(timeout=3.0))
    assert result["error"] == "VERIFY_UNCERTAIN"
    assert "人工核实" in result["detail"]
    assert sum("CityOperationTypes.PURCHASE" in call["code"] for call in calls) == 1


def test_app_options_non_utf8_read_and_write_roundtrip(tmp_path):
    path = tmp_path / "AppOptions.txt"
    # UTF-8 BOM + CP1252 正文：回退解码不得重复 BOM 或破坏 CRLF。
    raw = b"\xef\xbb\xbf" + "[Debug]\r\nNote café\r\nEnableTuner 0\r\n".encode("cp1252")
    path.write_bytes(raw)
    assert "café" in _sdk._read_app_options(path)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is True
    assert path.read_bytes().startswith(b"\xef\xbb\xbf[Debug]\r\nNote caf\xe9")
    assert path.read_bytes().count(b"\xef\xbb\xbf") == 1
    assert Path(result["backup"]).read_bytes() == raw


def test_app_options_undecodable_returns_structured_error_and_preserves_file(tmp_path):
    path = tmp_path / "AppOptions.txt"
    raw = b"\xff\xfe["  # 奇数字节的 UTF-16LE，任何兼容单字节解码都不能冒充 UTF-16 成功。
    path.write_bytes(raw)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is False
    assert result["error"] == "APP_OPTIONS_DECODE_FAILED"
    assert "原文件未修改" in result["detail"]
    assert path.read_bytes() == raw
    assert list(tmp_path.glob("*.bak_*")) == []


@pytest.mark.parametrize("order", [("civ6_tuner", "civ6_tuner_auto"), ("civ6_tuner_auto", "civ6_tuner")])
def test_direct_import_orders_work_in_fresh_process(order):
    plugin_dir = Path(_tuner.__file__).resolve().parent
    code = (
        "import importlib; "
        f"a=importlib.import_module('{order[0]}'); "
        f"b=importlib.import_module('{order[1]}'); "
        "import civ6_tuner,civ6_tuner_auto; "
        "assert civ6_tuner.auto_choose_pantheon is civ6_tuner_auto.auto_choose_pantheon"
    )
    env = dict(os.environ, PYTHONPATH=str(plugin_dir) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    result = subprocess.run([sys.executable, "-c", code], cwd=plugin_dir, env=env,
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_static_reexport_has_no_importlib_injection():
    source = Path(_tuner.__file__).read_text(encoding="utf-8")
    assert "_reexport_auto" not in source
    assert "importlib" not in source
    assert "from .civ6_tuner_auto import" in source
    assert set(importlib.import_module("civ6_tuner_auto").__all__) <= set(_tuner.__all__)


def test_all_referenced_lua_resources_exist_and_key_scripts_match():
    root = Path(_tuner.__file__).resolve().parent
    referenced = set()
    for filename in ("civ6_tuner.py", "civ6_tuner_auto.py"):
        source = (root / filename).read_text(encoding="utf-8")
        referenced.update(re.findall(r'_lua\("([a-z0-9_]+)"\)', source))
        assert 'lua = """' not in source
        assert 'lua = f"""' not in source
    assert len(referenced) >= 45
    for name in referenced:
        assert _resources.load_lua(name).strip(), name
    expected_snippets = {
        "set_city_production_request": "CityManager.RequestOperation",
        "set_city_production_verify": "GetCurrentProductionTypeHash",
        "set_research_verify": "VERIFY|current=",
        "auto_choose_pantheon": "PlayerOperations.FOUND_PANTHEON",
        "auto_choose_pantheon_verify": "GetPantheon()",
        "auto_send_envoy_verify": "GetEnvoys(target)",
        "auto_manage_money_verify": "GetGoldBalance()",
        "auto_promote_units_verify": "HasPromotion(target.promo)",
        "auto_trade_route_verify": "GetNumOutgoingRoutes()",
        "auto_found_religion_verify": "GetReligionTypeCreated()",
        "auto_recruit_great_person_verify": "GetUnclaimedGreatPeople()",
    }
    for name, snippet in expected_snippets.items():
        assert snippet in _resources.load_lua(name), name


def test_lua_template_formatting_preserves_production_parameters(monkeypatch):
    def respond(code):
        if "CityManager.RequestOperation" in code:
            return ["REQUESTED=1|OCCUPIED=0|REJECTED="]
        return ["SET|done=1|failed=0|size=1|ids=3"]

    calls, _writer = _fake_tuner_io(monkeypatch, respond)
    monkeypatch.setattr(_tuner, "_sleep", _no_sleep)
    result = asyncio.run(_tuner.set_city_production(
        "district", "DISTRICT_CAMPUS", city_id=3, plot_x=7, plot_y=8, timeout=3.0,
    ))
    assert result["ok"] is True
    request = calls[0]["code"]
    assert 'GameInfo.Districts["DISTRICT_CAMPUS"]' in request
    assert "CityOperationTypes.PARAM_DISTRICT_TYPE" in request
    assert "CityManager.GetCity(me, 3)" in request
    assert "params[CityOperationTypes.PARAM_X] = 7" in request
    assert "params[CityOperationTypes.PARAM_Y] = 8" in request
    assert not re.search(r"\{p\d+\}", request)


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


class _FakeModuleTime:
    """只替换插件模块里的 time 引用，不改 stdlib；避免污染宿主后台线程。"""

    def __init__(self, clock):
        self._clock = clock

    def monotonic(self):
        return self._clock.now

    def time(self):
        return self._clock.now


def test_cleanup_two_rounds_share_fake_monotonic_budget(monkeypatch):
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_cleanup_lock = asyncio.Lock()
    clock = _FakeClock()
    attempts = []

    async def unregister(timeout=0):
        attempts.append(timeout)
        clock.now += 4.0
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": "busy"}

    async def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(_main, "time", _FakeModuleTime(clock))
    monkeypatch.setattr(_main, "_sleep", sleep)
    monkeypatch.setattr(_main.civ6_tuner, "unregister_wc_voter", unregister)
    result = asyncio.run(plugin._cleanup_managed_runtime())
    assert result["error"] == "GAME_TUNER_OFF"
    assert len(attempts) == 2
    assert attempts == [10.0, pytest.approx(5.65)]
    assert clock.now == pytest.approx(8.35)


def test_two_handshakes_share_protocol_deadline_with_fake_clock(monkeypatch):
    clock = _FakeClock()
    attempts = []

    class Loop:
        @staticmethod
        def time():
            return clock.now

    async def handshake(timeout=5.0):
        attempts.append(timeout)
        clock.now += 2.0  # 可控阻塞，不做真实长睡眠。
        raise _protocol.TunerError("blocked")

    async def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(_protocol.asyncio, "get_running_loop", lambda: Loop())
    monkeypatch.setattr(_protocol, "_sleep", sleep)
    monkeypatch.setattr(_protocol, "handshake", handshake)
    with pytest.raises(_protocol.TunerError, match="blocked"):
        asyncio.run(_protocol._handshake_retry(retries=1, delay=0.5, timeout=6.0))
    assert attempts == [6.0, 3.5]
    assert clock.now == 4.5


def test_hung_cleanup_returns_promptly_without_real_long_sleep(monkeypatch):
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_cleanup_lock = asyncio.Lock()

    async def blocked(timeout=0):
        await asyncio.sleep(60)

    monkeypatch.setattr(_main, "_OPS_CLEANUP_BUDGET", 0.03)
    monkeypatch.setattr(_main.civ6_tuner, "unregister_wc_voter", blocked)
    started = time.monotonic()
    result = asyncio.run(plugin._cleanup_managed_runtime())
    assert result["error"] == "HANDOFF_CLEANUP_TIMEOUT"
    assert time.monotonic() - started < 0.5


def test_full_handoff_budget_leaves_five_seconds_for_host_return(monkeypatch):
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_active = True
    plugin._ops_stop_event = asyncio.Event()
    plugin._ops_screen_cancel = None
    clock = _FakeClock()
    observed = {}

    async def tick_wait(timeout=0):
        observed["tick"] = timeout
        clock.now += timeout
        return False

    async def screen_wait(timeout=0):
        observed["screen"] = timeout
        clock.now += timeout
        return False

    async def cleanup(deadline=None):
        observed["cleanup_remaining"] = deadline - clock.now
        return {"ok": False, "error": "HANDOFF_CLEANUP_TIMEOUT"}

    monkeypatch.setattr(_main, "time", _FakeModuleTime(clock))
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)
    plugin._wait_ops_tick_idle = tick_wait
    plugin._join_screen_worker = screen_wait
    plugin._cleanup_managed_runtime = cleanup
    result = asyncio.run(plugin.civ6_ops_handoff())
    assert observed == {"tick": 3.0, "screen": 3.0, "cleanup_remaining": 9.0}
    assert _main._OPS_HANDOFF_BUDGET == 15.0
    assert _main._OPS_HANDOFF_BUDGET < 20.0 - 4.0
    assert result["runtime_cleanup"]["error"] == "HANDOFF_CLEANUP_TIMEOUT"


def _bare_plugin():
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    return plugin


def _manage_stubs(monkeypatch, blocker, **overrides):
    calls = []
    values = {
        "close_ui_popups": {"ok": True, "detail": "closed"},
        "read_blockers": {"ok": True, "blockers": [{"type": blocker, "message": "test"}]},
        "set_city_production": {"ok": True, "detail": "production verified"},
        "set_research": {"ok": True, "detail": "research verified"},
        "register_wc_voter": {"ok": True, "detail": "wc ready"},
        "auto_choose_pantheon": {"ok": True, "verified": True, "detail": "pantheon verified"},
        "auto_manage_money": {"ok": True, "detail": "none"},
        "auto_found_religion": {"ok": True, "founded": False, "detail": "none"},
        "auto_recruit_great_person": {"ok": True, "recruited": False, "detail": "none"},
        "auto_trade_route": {"ok": True, "routes": [], "detail": "none"},
        "auto_manage_spy": {"ok": True, "has_spy": False, "detail": "none"},
        "auto_war_response": {"ok": True, "at_war": False, "detail": "peace"},
        "end_turn": {"ok": True, "detail": "end"},
        "read_situation": {"ok": True, "turn": 10},
    }
    values.update(overrides)
    for name, value in values.items():
        async def stub(*_args, _value=value, _name=name, **_kwargs):
            calls.append(_name)
            return _value
        monkeypatch.setattr(_main.civ6_tuner, name, stub)
    monkeypatch.setattr(_main, "_sleep", _no_sleep)
    return calls


@pytest.mark.parametrize(
    ("blocker", "operation"),
    [
        ("ENDTURN_BLOCKING_PRODUCTION", "set_city_production"),
        ("ENDTURN_BLOCKING_RESEARCH", "set_research"),
        ("ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION", "register_wc_voter"),
        ("ENDTURN_BLOCKING_PANTHEON", "auto_choose_pantheon"),
    ],
)
def test_manage_turn_business_branches_execute(monkeypatch, blocker, operation):
    calls = _manage_stubs(monkeypatch, blocker)
    result = asyncio.run(_bare_plugin()._tuner_manage_turn(
        9, False, "unit", "UNIT_BUILDER", "TECH_POTTERY", "CIVIC_CODE_OF_LAWS",
    ))
    assert result["handoff"] is False, result
    assert result["turn_after"] == 10
    assert operation in calls
    assert calls.count("end_turn") == 1


@pytest.mark.parametrize(
    ("blocker", "operation", "overrides"),
    [
        ("ENDTURN_BLOCKING_PRODUCTION", "set_city_production",
         {"set_city_production": {"ok": False, "error": "VERIFY_UNCERTAIN", "detail": "生产读回不确定"}}),
        ("ENDTURN_BLOCKING_RESEARCH", "set_research",
         {"set_research": {"ok": False, "error": "VERIFY_UNCERTAIN", "detail": "研究读回不确定"},
          "auto_pick_research": {"ok": False, "detail": "没有可替代研究"}}),
        ("ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION", "register_wc_voter",
         {"register_wc_voter": {"ok": False, "error": "NO_RESPONSE", "detail": "议会读回失败"}}),
        ("ENDTURN_BLOCKING_PANTHEON", "auto_choose_pantheon",
         {"auto_choose_pantheon": {"ok": False, "error": "VERIFY_UNCERTAIN", "detail": "万神殿读回不确定"}}),
    ],
)
def test_manage_turn_readback_failure_hands_off_before_end_turn(
    monkeypatch, blocker, operation, overrides,
):
    calls = _manage_stubs(monkeypatch, blocker, **overrides)
    result = asyncio.run(_bare_plugin()._tuner_manage_turn(
        9, False, "unit", "UNIT_BUILDER", "TECH_POTTERY", "CIVIC_CODE_OF_LAWS",
    ))
    assert result["handoff"] is True
    assert result["reason"] == "HARD_BLOCKER"
    assert operation in calls
    assert "end_turn" not in calls
    assert "读回" in result["output"] or "失败" in result["output"]
