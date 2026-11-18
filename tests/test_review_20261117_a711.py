"""A711 增量深审回归：购买/商队读回三态、延迟确认、纪念 supported、
Lua 占位符语义化，以及 sleep 间接层。

对应报告五项：
- M1 分级收口：只有 VERIFY_LAGGING 顺延，WRITE_UNCERTAIN 等一律交还
- M2 购买与商队的读回"金币没降/路线没涨"必须落到明确结论，不能只记日志
- M3 创教 UNSUPPORTED 文案带 auto_irreversible 配置指引
- M4 HasReligiousFoundingUnit 进实机清单（文档项，由 checklist 扫描覆盖）
- M5 所有等待走模块内 _sleep，测试不再改全局 asyncio.sleep
- 遗留 #6 纪念读回 supported=false 不得被当成"选择已生效"
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

_main = importlib.import_module("plugin.plugins.civ6_buddy")
_resources = importlib.import_module("civ6_lua_resources")
_auto = importlib.import_module("civ6_tuner_auto")
_tuner = importlib.import_module("civ6_tuner")
_protocol = importlib.import_module("civ6_tuner_protocol")

_LUA_ROOT = Path(_resources.__file__).resolve().parent / "lua"


async def _no_sleep(_seconds):
    return None


def _lua(name: str) -> str:
    return (_LUA_ROOT / f"{name}.lua").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# M2：购买与商队的读回三态
# --------------------------------------------------------------------------

def _capture_parse_verify(module_fn, monkeypatch):
    captured = {}

    async def fake_write_and_verify(**kwargs):
        captured["parse_verify"] = kwargs["parse_verify"]
        return {"ok": True}

    monkeypatch.setattr(_auto, "_write_and_verify", fake_write_and_verify)
    asyncio.run(module_fn(timeout=1.0))
    return captured["parse_verify"]


_MONEY_PENDING = {"gold": 200, "builders": 1, "action": "buy_builder"}


def test_money_verify_success_requires_gold_drop_and_new_builder(monkeypatch):
    parse = _capture_parse_verify(_auto.auto_manage_money, monkeypatch)
    result = parse(["MONEY_VERIFY|supported=true|matched=true|gold=50|builders=2"], _MONEY_PENDING)
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["settled"] is True
    assert result["gold"] == 50


def test_money_verify_gold_unchanged_is_mismatch_after_three_reads(monkeypatch):
    """金币没下降不能首次读回就判失败：扣款可能晚一帧，第三次仍没降才报 MISMATCH。"""
    parse = _capture_parse_verify(_auto.auto_manage_money, monkeypatch)
    pending = dict(_MONEY_PENDING)
    assert parse(
        ["MONEY_VERIFY|supported=true|matched=false|gold=200|builders=1"], pending,
    ) is None
    assert parse(
        ["MONEY_VERIFY|supported=true|matched=false|gold=200|builders=1"], pending,
    ) is None
    result = parse(
        ["MONEY_VERIFY|supported=true|matched=false|gold=200|builders=1"], pending,
    )
    assert result["ok"] is False
    assert result["error"] == "VERIFY_MISMATCH"
    assert "连续 3 次读回未下降" in result["detail"]


def test_money_verify_gold_paid_without_builder_is_lagging(monkeypatch):
    parse = _capture_parse_verify(_auto.auto_manage_money, monkeypatch)
    result = parse(
        ["MONEY_VERIFY|supported=true|matched=false|gold=140|builders=1"], _MONEY_PENDING,
    )
    assert result["ok"] is False
    assert result["error"] == "VERIFY_LAGGING"
    assert result["expected_builders"] == 2
    assert "下一回合" in result["detail"]


def test_trade_verify_unmatched_is_lagging_not_silent(monkeypatch):
    """同回合商队几乎必然还没计入路线数；旧实现让它与"请求被拒"同落成功/静默两档。"""
    parse = _capture_parse_verify(_auto.auto_trade_route, monkeypatch)
    pending = {"routes": [{"to": 2}], "operation": "trade"}
    result = parse(["TRADE_VERIFY|supported=true|matched=false|before=0|after=0"], pending)
    assert result["ok"] is False
    assert result["error"] == "VERIFY_LAGGING"
    assert result["expected_routes"] == 1


def test_empty_pending_writes_makes_no_probe(monkeypatch):
    """没有记账就不该发起确认读回（避免每回合凭空多两次 Tuner 往返）。"""
    called = []

    async def fake_confirm(expected_builders, timeout=8.0):
        called.append(expected_builders)
        return {"ok": True, "detail": "unexpected"}

    monkeypatch.setattr(_main.civ6_tuner, "confirm_money_settlement", fake_confirm)
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._auto_irreversible = False
    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["detail"] == "无待复核项"
    assert called == []


# --------------------------------------------------------------------------
# 延迟确认：下一回合只重跑读回脚本
# --------------------------------------------------------------------------

def test_confirm_money_settlement_reports_lagging_without_rewriting(monkeypatch):
    async def fake_rerun(verify_name, prefix, timeout):
        assert verify_name == "auto_manage_money_verify"
        return {"ok": True, "values": {"supported": "true", "matched": "false",
                                       "gold": "140", "builders": "1"},
                "line": "MONEY_VERIFY|..."}

    monkeypatch.setattr(_auto, "_rerun_verify", fake_rerun)
    result = asyncio.run(_auto.confirm_money_settlement(2))
    assert result["ok"] is False
    assert result["error"] == "VERIFY_LAGGING"
    assert result["lagging"] is True


def test_confirm_money_settlement_confirms_when_builder_appears(monkeypatch):
    async def fake_rerun(verify_name, prefix, timeout):
        return {"ok": True, "values": {"supported": "true", "matched": "true",
                                       "gold": "140", "builders": "2"}, "line": "MONEY_VERIFY|..."}

    monkeypatch.setattr(_auto, "_rerun_verify", fake_rerun)
    result = asyncio.run(_auto.confirm_money_settlement(2))
    assert result["ok"] is True
    assert result["settled"] is True


def test_confirm_trade_settlement_confirms_when_routes_count(monkeypatch):
    async def fake_rerun(verify_name, prefix, timeout):
        assert verify_name == "auto_trade_route_verify"
        return {"ok": True, "values": {"supported": "true", "matched": "true",
                                       "before": "0", "after": "1"}, "line": "TRADE_VERIFY|..."}

    monkeypatch.setattr(_auto, "_rerun_verify", fake_rerun)
    result = asyncio.run(_auto.confirm_trade_settlement(0, 1))
    assert result["ok"] is True


def test_confirm_never_calls_execute_lua_request_path(monkeypatch):
    """复核只读：不允许走 _write_and_verify（那条路会重发写请求）。"""
    async def boom(**kwargs):
        raise AssertionError("复核不应重发写请求")

    async def fake_rerun(verify_name, prefix, timeout):
        return {"ok": True, "values": {"supported": "true", "gold": "1", "builders": "2"},
                "line": "MONEY_VERIFY|..."}

    monkeypatch.setattr(_auto, "_write_and_verify", boom)
    monkeypatch.setattr(_auto, "_rerun_verify", fake_rerun)
    assert asyncio.run(_auto.confirm_money_settlement(2))["ok"] is True


# --------------------------------------------------------------------------
# M1：_pending_writes 跨回合复核与超龄交还
# --------------------------------------------------------------------------

def _stub_confirm(monkeypatch, payload):
    # 插件实际解析的是包内子模块，必须打在这里，否则真函数会去连 Tuner
    async def fake_confirm(expected_builders, timeout=8.0):
        return payload

    monkeypatch.setattr(_main.civ6_tuner, "confirm_money_settlement", fake_confirm)
    monkeypatch.setattr(_main.civ6_tuner, "confirm_trade_settlement", fake_confirm)


def test_pending_write_hands_off_after_two_turns_of_lagging(monkeypatch):
    _stub_confirm(monkeypatch, {"ok": False, "error": "VERIFY_LAGGING",
                                "lagging": True, "detail": "仍未见建造者"})
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._pending_writes = {"money": {"expected_builders": 2, "gold": 140, "age": 1}}
    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["handoff"] is True
    assert result["reason"] == "WRITE_UNCERTAIN"
    assert "连续 2 个回合" in result["output"]
    # 超龄交还后清账，避免每次 tick 都重复报同一件事
    assert "money" not in plugin._pending_writes


def test_pending_write_kept_for_one_more_turn(monkeypatch):
    _stub_confirm(monkeypatch, {"ok": False, "error": "VERIFY_LAGGING",
                                "lagging": True, "detail": "仍未见建造者"})
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._pending_writes = {"money": {"expected_builders": 2, "gold": 140, "age": 0}}
    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["handoff"] is False
    assert result["money_pending"] is True
    assert plugin._pending_writes["money"]["age"] == 1


def test_pending_write_cleared_on_confirm(monkeypatch):
    _stub_confirm(monkeypatch, {"ok": True, "settled": True, "detail": "建造者已到位"})
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._pending_writes = {"money": {"expected_builders": 2, "gold": 140, "age": 0}}
    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["handoff"] is False
    assert result["money_pending"] is False
    assert "money" not in plugin._pending_writes


def test_pending_write_explicit_failure_hands_off(monkeypatch):
    _stub_confirm(monkeypatch, {"ok": False, "error": "VERIFY_UNSUPPORTED", "detail": "读不到金币"})
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._pending_writes = {"money": {"expected_builders": 2, "gold": 140, "age": 0}}
    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["handoff"] is True
    assert result["reason"] == "VERIFY_UNSUPPORTED"


def test_lagging_write_records_bookkeeping():
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._remember_lagging_write("经济", {"expected_builders": 2, "gold": 140})
    plugin._remember_lagging_write("商队", {"before": 0, "expected_routes": 1})
    assert plugin._pending_writes["money"]["expected_builders"] == 2
    assert plugin._pending_writes["trade"]["expected"] == 1


# --------------------------------------------------------------------------
# 遗留 #6：纪念读回读不出来 ≠ 选择已生效
# --------------------------------------------------------------------------

def test_commemoration_verify_lua_guards_pcall_failure():
    source = _lua("auto_choose_commemoration_verify")
    assert "supported=" in source
    # supported=false 的分支必须先于"次数下降/不在列表"这类成功判断
    assert source.index("okAllowed and okChoices") < source.index("not still_available")


def test_commemoration_unsupported_is_not_reported_as_success(monkeypatch):
    writer = _FakeWriter()

    async def fake_handshake():
        return "app", ["GameCore_Tuner"], _FakeReader(), writer

    async def fake_execute(_reader, _writer, lua, **_kwargs):
        # 读回脚本里有 supported=；请求脚本只有 RESULT
        if "supported=" in lua:
            return ["COMM|supported=false|allowed=-1|before=1|still=false"]
        return ["RESULT|COMMEMORATION_ERA_GOLDEN"]

    monkeypatch.setattr(_tuner, "_handshake_retry", fake_handshake)
    monkeypatch.setattr(_tuner, "execute_lua", fake_execute)
    monkeypatch.setattr(_auto, "_sleep", _no_sleep)
    result = asyncio.run(_auto.auto_choose_commemoration(timeout=2.0))
    assert result["ok"] is False
    assert result["error"] == "VERIFY_UNSUPPORTED"


# --------------------------------------------------------------------------
# N4 尾项：Lua 占位符语义化后不得再出现 p0..p3
# --------------------------------------------------------------------------

_PLACEHOLDERS = {
    "set_research_verify": ("player_method", "getter", "wanted"),
    "auto_pick_research": ("gi_table", "player_method", "has_method", "type_field"),
    "auto_fill_policy_slots_verify": ("want_count",),
}


@pytest.mark.parametrize("name", sorted(_PLACEHOLDERS))
def test_lua_templates_use_semantic_placeholders(name):
    source = _lua(name)
    for index in range(4):
        assert f"{{p{index}}}" not in source
    for key in _PLACEHOLDERS[name]:
        assert "{" + key + "}" in source


# --------------------------------------------------------------------------
# M5：等待走 _sleep，测试不再改全局 asyncio.sleep
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module", [_main, _auto, _tuner, _protocol], ids=lambda m: m.__name__)
def test_module_waits_go_through_local_sleep(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "asyncio.sleep(" not in source.replace("await asyncio.sleep(seconds)", "")
    assert "async def _sleep(" in source


def test_tests_do_not_patch_global_asyncio_sleep():
    tests_dir = Path(__file__).resolve().parent
    # 拼出来，免得这条断言把自己算成违规
    marker = ".asyncio, " + '"slee' + 'p"'
    offenders = []
    for path in tests_dir.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        if marker in text:
            offenders.append(path.name)
    assert offenders == []


class _FakeWriter:
    def close(self):
        pass


class _FakeReader:
    pass
