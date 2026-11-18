"""A796 增量深审回归：购买读回重试、商队复核账龄、复核顺序、
接管/交还清账、UNSUPPORTED 配置指引与 README 口径。

对应报告 N1-N7。所有用例离线运行，不连接 FireTuner，也不操作真实游戏。
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

_main = importlib.import_module("plugin.plugins.civ6_buddy")
_auto = importlib.import_module("civ6_tuner_auto")


def _plugin_with_pending(pending: dict):
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._ops_screen_cancel = None
    plugin._pending_writes = pending
    return plugin


# --------------------------------------------------------------------------
# N2：购买金币未降要等扣款落地，第三次读回才定性
# --------------------------------------------------------------------------

def _capture_parse_request(module_fn, monkeypatch):
    captured = {}

    async def fake_write_and_verify(**kwargs):
        captured["parse_request"] = kwargs["parse_request"]
        return {"ok": True}

    monkeypatch.setattr(_auto, "_write_and_verify", fake_write_and_verify)
    asyncio.run(module_fn(timeout=1.0))
    return captured["parse_request"]


def test_money_request_unsupported_has_irreversible_hint(monkeypatch):
    parse = _capture_parse_request(_auto.auto_manage_money, monkeypatch)
    result = parse(["MONEY|gold=-1|builders=1|UNSUPPORTED"], "APP")
    assert result["error"] == "VERIFY_UNSUPPORTED"
    assert "auto_irreversible" in result["detail"]


def test_trade_request_unsupported_has_irreversible_hint(monkeypatch):
    parse = _capture_parse_request(_auto.auto_trade_route, monkeypatch)
    result = parse(["TRADE_UNSUPPORTED|no_api"], "APP")
    assert result["error"] == "VERIFY_UNSUPPORTED"
    assert "auto_irreversible" in result["detail"]


# --------------------------------------------------------------------------
# N1：商队复核窗口要覆盖真实路程，不能下一回合就交还
# --------------------------------------------------------------------------

def test_trade_pending_keeps_five_rechecks_before_handoff(monkeypatch):
    async def fake_confirm(before, expected, timeout=8.0):
        return {"ok": False, "error": "VERIFY_LAGGING", "lagging": True,
                "detail": "路线仍未计入"}

    monkeypatch.setattr(_main.civ6_tuner, "confirm_trade_settlement", fake_confirm)
    plugin = _plugin_with_pending(
        {"trade": {"before": 0, "expected": 1, "age": 1}}
    )

    # 真实流程记账后 age 至少是 1：再复核 4 次仍应保留，第 5 次才到 6 交还。
    for expected_age in (2, 3, 4, 5):
        result = asyncio.run(plugin._resolve_pending_writes())
        assert result["handoff"] is False
        assert plugin._pending_writes["trade"]["age"] == expected_age

    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["handoff"] is True
    assert result["reason"] == "WRITE_UNCERTAIN"
    assert "trade" not in plugin._pending_writes
    assert "连续 6 个回合" in result["output"]


def test_money_pending_still_hands_off_after_one_more_turn(monkeypatch):
    async def fake_confirm(expected_builders, timeout=8.0):
        return {"ok": False, "error": "VERIFY_LAGGING", "lagging": True,
                "detail": "建造者仍未出现"}

    monkeypatch.setattr(_main.civ6_tuner, "confirm_money_settlement", fake_confirm)
    plugin = _plugin_with_pending(
        {"money": {"expected_builders": 2, "gold": 140, "age": 1}}
    )
    result = asyncio.run(plugin._resolve_pending_writes())
    assert result["handoff"] is True
    assert "money" not in plugin._pending_writes


# --------------------------------------------------------------------------
# N3/N4：复核顺序与清账
# --------------------------------------------------------------------------

def test_pending_verify_runs_before_blocker_handling():
    source = Path(_main.__file__).read_text(encoding="utf-8")
    assert source.count("check = await self._resolve_pending_writes()") == 1
    assert (
        source.index("check = await self._resolve_pending_writes()")
        < source.index("b = await civ6_tuner.read_blockers(timeout=8.0)")
    )


def test_stop_clears_pending_writes(monkeypatch):
    monkeypatch.setattr(_main, "_ops_lock_clear", lambda: None)
    plugin = _plugin_with_pending(
        {"money": {"expected_builders": 2, "gold": 140, "age": 1}}
    )
    plugin._request_ops_stop()
    assert plugin._pending_writes == {}


# --------------------------------------------------------------------------
# N6：README 不再把购买/商队写成"最多三次只读验证"
# --------------------------------------------------------------------------

def test_readme_documents_first_readback_buckets():
    readme = (Path(_main.__file__).resolve().parent / "README.md").read_text(encoding="utf-8")
    assert "七类操作均只发一次写请求" in readme
    assert "购买建造者与商队另有分档" in readme
    assert "金币没降先重试读回" in readme
    assert "第三次仍没降才判请求被拒" in readme
