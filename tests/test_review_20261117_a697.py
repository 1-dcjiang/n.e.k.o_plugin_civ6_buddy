"""A697 增量深审回归：创教 pcall 语义、读回滞后分级、伟人提示与不可逆开关。

对应报告四项：
- N1 创教 pcall 失败与"确实没有创教单位"必须分开，前者报 RELIGION_UNSUPPORTED
- N2 购买/商队读回滞后只记日志，创教/伟人读不回仍交还
- N3 伟人 API 不可用的交还文案带 auto_irreversible 配置提示
- N6 _auto_irreversible 用类属性给出真实默认值，不再靠 getattr 兜底
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

_main = importlib.import_module("plugin.plugins.civ6_buddy")
_resources = importlib.import_module("civ6_lua_resources")
_auto = importlib.import_module("civ6_tuner_auto")

_LUA_ROOT = Path(_resources.__file__).resolve().parent / "lua"


async def _no_sleep(_seconds):
    return None


def _lua(name: str) -> str:
    return (_LUA_ROOT / f"{name}.lua").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# N1：pcall 失败 ≠ 没有创教单位
# --------------------------------------------------------------------------

def test_found_religion_checks_pcall_result():
    source = _lua("auto_found_religion")
    assert 'local has_founder_ok = pcall(' in source
    assert 'if not has_founder_ok then print("RELIGION_UNSUPPORTED|' in source
    assert 'if not has_founder then print("RELIGION_NONE|no_founding_unit")' in source
    # UNSUPPORTED 必须先于 NONE 判断，否则调用失败仍会被当成"这局不用创教"
    assert source.index("RELIGION_UNSUPPORTED|") < source.index("no_founding_unit")


def _capture_parse_request(module_fn, monkeypatch):
    """拦住 _write_and_verify，把内部 parse_request 抠出来单独断言。"""
    captured = {}

    async def fake_write_and_verify(**kwargs):
        captured["parse_request"] = kwargs["parse_request"]
        return {"ok": True}

    monkeypatch.setattr(_auto, "_write_and_verify", fake_write_and_verify)
    asyncio.run(module_fn(timeout=1.0))
    return captured["parse_request"]


def test_religion_unsupported_is_not_treated_as_no_founding_unit(monkeypatch):
    parse = _capture_parse_request(_auto.auto_found_religion, monkeypatch)
    result = parse(["RELIGION_UNSUPPORTED|has_religious_founding_unit"], "app")
    assert result["ok"] is False
    assert result["error"] == "VERIFY_UNSUPPORTED"
    assert "HasReligiousFoundingUnit" in result["detail"]
    assert "未发送创教请求" in result["detail"]


def test_religion_none_keeps_skip_semantics(monkeypatch):
    parse = _capture_parse_request(_auto.auto_found_religion, monkeypatch)
    result = parse(["RELIGION_NONE|no_founding_unit"], "app")
    assert result["ok"] is True
    assert result["founded"] is False
    assert "未创教" in result["detail"]


# --------------------------------------------------------------------------
# N3：伟人 API 不可用的交还文案要能指路
# --------------------------------------------------------------------------

def test_great_person_unsupported_carries_config_hint(monkeypatch):
    parse = _capture_parse_request(_auto.auto_recruit_great_person, monkeypatch)
    result = parse(["GP|UNSUPPORTED"], "app")
    assert result["error"] == "VERIFY_UNSUPPORTED"
    assert "auto_irreversible" in result["detail"]


# --------------------------------------------------------------------------
# N2：读回滞后分级
# --------------------------------------------------------------------------

def _plugin_with_stubs(monkeypatch, overrides=None):
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    calls = []

    def stub(name, value):
        async def _stub(*_args, **_kwargs):
            calls.append(name)
            return value

        return _stub

    values = {
        "close_ui_popups": {"ok": True, "detail": "closed"},
        "read_blockers": {"ok": True, "blockers": [{"type": "ENDTURN_BLOCKING_PRODUCTION", "message": "test"}]},
        "set_city_production": {"ok": True, "detail": "production verified"},
        "set_research": {"ok": True, "detail": "research verified"},
        "register_wc_voter": {"ok": True, "detail": "wc ready"},
        "auto_choose_pantheon": {"ok": True, "verified": True, "detail": "pantheon verified"},
        "auto_manage_money": {"ok": True, "detail": "money"},
        "auto_found_religion": {"ok": True, "founded": False, "detail": "none"},
        "auto_recruit_great_person": {"ok": True, "recruited": False, "detail": "none"},
        "auto_trade_route": {"ok": True, "routes": [], "detail": "none"},
        "auto_manage_spy": {"ok": True, "has_spy": False, "detail": "none"},
        "auto_war_response": {"ok": True, "at_war": False, "detail": "peace"},
        "end_turn": {"ok": True, "detail": "end"},
        "read_situation": {"ok": True, "turn": 10},
    }
    values.update(overrides or {})
    for name, value in values.items():
        monkeypatch.setattr(_main.civ6_tuner, name, stub(name, value))
    monkeypatch.setattr(_main, "_sleep", _no_sleep)
    return plugin, calls


def _manage_turn(plugin):
    return asyncio.run(
        plugin._tuner_manage_turn(
            9, False, "unit", "UNIT_BUILDER", "TECH_POTTERY", "CIVIC_CODE_OF_LAWS",
        )
    )


_READBACK_LAGGING = {
    "money": {"ok": False, "error": "VERIFY_LAGGING", "detail": "金币已扣、建造者仍未见",
              "gold": 50, "builders": 1, "expected_builders": 2},
    "trade": {"ok": False, "error": "VERIFY_LAGGING", "detail": "路线数尚未增加",
              "before": 0, "after": 0, "expected_routes": 1},
}

_READBACK_UNCERTAIN = [
    {"ok": False, "error": "VERIFY_UNCERTAIN", "detail": "读回后状态仍不匹配"},
    {"ok": False, "error": "WRITE_UNCERTAIN", "detail": "无请求回执"},
    {"ok": False, "error": "VERIFY_MISMATCH", "detail": "读回不匹配"},
    {"ok": False, "error": "VERIFY_TIMEOUT", "detail": "读回超时"},
]


def test_money_lagging_is_deferred_to_next_turn(monkeypatch):
    """只有 VERIFY_LAGGING（金币已扣、单位还没出现）才顺延到下一回合复核。"""
    plugin, calls = _plugin_with_stubs(monkeypatch, {"auto_manage_money": _READBACK_LAGGING["money"]})
    result = _manage_turn(plugin)
    assert result["handoff"] is False
    assert result["ok"] is True
    assert "end_turn" in calls
    assert any("读回滞后" in line and "经济" in line for line in result["log"])
    # 顺延必须记账，否则下一回合既不会复核也不会拦重复花钱
    assert plugin._pending_writes["money"]["expected_builders"] == 2
    assert plugin._pending_writes["money"]["age"] == 1


def test_trade_lagging_is_deferred_to_next_turn(monkeypatch):
    plugin, calls = _plugin_with_stubs(monkeypatch, {"auto_trade_route": _READBACK_LAGGING["trade"]})
    result = _manage_turn(plugin)
    assert result["handoff"] is False
    assert "end_turn" in calls
    assert any("读回滞后" in line and "商队" in line for line in result["log"])
    assert plugin._pending_writes["trade"]["expected"] == 1


@pytest.mark.parametrize("payload", _READBACK_UNCERTAIN, ids=lambda p: p["error"])
def test_money_unreadable_hands_off(monkeypatch, payload):
    """请求可能根本没下发、读回失败或状态不匹配，都不是"再等等就好"，必须交还。

    0.6.14 把这些码与滞后档揉在一起，等于默认写成功了；这里逐码守回来。
    """
    plugin, calls = _plugin_with_stubs(monkeypatch, {"auto_manage_money": payload})
    result = _manage_turn(plugin)
    assert result["handoff"] is True
    assert result["reason"] == "WRITE_UNCERTAIN"
    assert "经济" in result["output"]
    assert "end_turn" not in calls
    assert "money" not in plugin._pending_writes


@pytest.mark.parametrize("payload", _READBACK_UNCERTAIN, ids=lambda p: p["error"])
def test_trade_route_unreadable_hands_off(monkeypatch, payload):
    plugin, calls = _plugin_with_stubs(monkeypatch, {"auto_trade_route": payload})
    result = _manage_turn(plugin)
    assert result["handoff"] is True
    assert "商队" in result["output"]
    assert "end_turn" not in calls


def test_religion_unreadable_still_hands_off(monkeypatch):
    plugin, calls = _plugin_with_stubs(
        monkeypatch,
        {"auto_found_religion": {"ok": False, "error": "WRITE_UNCERTAIN", "detail": "创教无请求回执"}},
    )
    result = _manage_turn(plugin)
    assert result["handoff"] is True
    assert result["reason"] == "WRITE_UNCERTAIN"
    assert "宗教" in result["output"]
    assert "end_turn" not in calls


def test_great_person_unreadable_still_hands_off(monkeypatch):
    plugin, calls = _plugin_with_stubs(
        monkeypatch,
        {"auto_recruit_great_person": {"ok": False, "error": "VERIFY_UNSUPPORTED", "detail": "无伟人列表"}},
    )
    result = _manage_turn(plugin)
    assert result["handoff"] is True
    assert "伟人" in result["output"]
    assert "end_turn" not in calls


def test_money_api_unsupported_still_hands_off(monkeypatch):
    """顺着 N2 放宽的分级：只放宽读回滞后，API 真不可用仍是明确失败，必须交还。"""
    plugin, calls = _plugin_with_stubs(
        monkeypatch,
        {"auto_manage_money": {"ok": False, "error": "VERIFY_UNSUPPORTED", "detail": "读不到金币"}},
    )
    result = _manage_turn(plugin)
    assert result["handoff"] is True
    assert result["reason"] == "WRITE_UNCERTAIN"
    assert "经济" in result["output"]
    assert "end_turn" not in calls


# --------------------------------------------------------------------------
# N6：不可逆开关的默认值来自类属性
# --------------------------------------------------------------------------

def test_auto_irreversible_default_comes_from_class_attribute():
    source = Path(_main.__file__).read_text(encoding="utf-8")
    assert 'getattr(self, "_auto_irreversible"' not in source
    assert _main.Civ6BuddyPlugin._auto_irreversible is True
    # 不跑构造的调用方（测试常用 __new__）也应读到真实默认值
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    assert plugin._auto_irreversible is True
