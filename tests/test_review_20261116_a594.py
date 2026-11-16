"""A594 增量深审回归：Lua 前置条件、读回判定、决策弹窗与写操作交还。"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
from pathlib import Path

import pytest

_main = importlib.import_module("plugin.plugins.civ6_buddy")
_resources = importlib.import_module("civ6_lua_resources")
_tuner = importlib.import_module("civ6_tuner")
_sdk = importlib.import_module("civ6_sdk")

_LUA_ROOT = Path(_resources.__file__).resolve().parent / "lua"


class _Writer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fake_tuner_io(monkeypatch, lines):
    writer = _Writer()

    async def handshake(*_args, **_kwargs):
        return "APP", ["3", "GameCore_Tuner", "27", "InGame"], object(), writer

    async def execute(*_args, **_kwargs):
        return lines

    monkeypatch.setattr(_tuner, "_handshake_retry", handshake)
    monkeypatch.setattr(_tuner, "execute_lua", execute)
    return writer


async def _no_sleep(_seconds):
    return None


def _lua(name: str) -> str:
    return (_LUA_ROOT / f"{name}.lua").read_text(encoding="utf-8")


def test_found_religion_prechecks_founding_unit_before_write():
    source = _lua("auto_found_religion")
    assert "HasReligiousFoundingUnit()" in source
    guard = source.index("HasReligiousFoundingUnit()")
    write = source.index("PlayerOperations.FOUND_RELIGION")
    assert guard < write
    assert "PARAM_INSERT_MODE" in source
    assert source.count("PlayerOperations.VALUE_EXCLUSIVE") >= 3


def test_promotion_targets_reset_each_request_and_missing_is_mismatch():
    source = _lua("auto_promote_units")
    assert "__civ6_promo_targets = {}" in source

    verify = _lua("auto_promote_units_verify")
    assert "local done, missing = 0, 0" in verify
    assert "missing == 0" in verify
    assert "|missing=" in verify


def test_recruit_great_person_reads_name_before_request():
    source = _lua("auto_recruit_great_person")
    name = source.index("local name = person:GetName()")
    request = source.index("PlayerOperations.RECRUIT_GREAT_PERSON")
    assert name < request


def test_policy_verify_compares_index_not_hash():
    request = _lua("auto_fill_policy_slots")
    assert "addList[s] = best.Hash" in request
    assert "policyTargets[s] = best.Index" in request
    assert "__civ6_policy_targets = policyTargets" in request

    verify = _lua("auto_fill_policy_slots_verify")
    assert "targets[s]" in verify
    assert "GetSlotPolicy(s) == want" in verify


def test_decision_popups_are_not_hidden_but_reported():
    source = _lua("close_ui_popups")
    hide_list = source.split("local paths =", 1)[1].split("}", 1)[0]
    for name in ("RazeCity", "ChooseArtifact", "EventPopup", "EspionagePopup"):
        assert name not in hide_list
    assert "DECISION=" in source
    assert "/InGame/RazeCity" in source


def test_close_ui_popups_handoff_on_decision_popup(monkeypatch):
    _fake_tuner_io(
        monkeypatch,
        ["CLOSED=0&NW=false&MENU=false&DECISION=/InGame/RazeCity"],
    )
    result = asyncio.run(_tuner.close_ui_popups(timeout=3.0))
    assert result["ok"] is False
    assert result["error"] == "POPUP_UNRESOLVED"
    assert "RazeCity" in result["detail"]


def test_manage_turn_soft_write_failure_hands_off_before_end_turn(monkeypatch):
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
        "auto_manage_money": {"ok": False, "error": "VERIFY_UNCERTAIN", "detail": "购买读回不确定"},
        "auto_found_religion": {"ok": True, "founded": False, "detail": "none"},
        "auto_recruit_great_person": {"ok": True, "recruited": False, "detail": "none"},
        "auto_trade_route": {"ok": True, "routes": [], "detail": "none"},
        "auto_manage_spy": {"ok": True, "has_spy": False, "detail": "none"},
        "auto_war_response": {"ok": True, "at_war": False, "detail": "peace"},
        "end_turn": {"ok": True, "detail": "end"},
        "read_situation": {"ok": True, "turn": 10},
    }
    for name, value in values.items():
        monkeypatch.setattr(_main.civ6_tuner, name, stub(name, value))
    monkeypatch.setattr(_main.asyncio, "sleep", _no_sleep)

    result = asyncio.run(plugin._tuner_manage_turn(
        9, False, "unit", "UNIT_BUILDER", "TECH_POTTERY", "CIVIC_CODE_OF_LAWS",
    ))
    assert result["handoff"] is True
    assert result["reason"] == "WRITE_UNCERTAIN"
    assert "经济" in result["output"]
    assert "end_turn" not in calls


def test_manage_turn_skips_irreversible_when_disabled(monkeypatch):
    plugin = _main.Civ6BuddyPlugin.__new__(_main.Civ6BuddyPlugin)
    plugin._ops_stop_event = asyncio.Event()
    plugin._auto_irreversible = False
    calls = []

    def stub(name, value):
        async def _stub(*_args, **_kwargs):
            calls.append(name)
            return value
        return _stub

    values = {
        "close_ui_popups": {"ok": True, "detail": "closed"},
        "read_blockers": {"ok": True, "blockers": []},
        "register_wc_voter": {"ok": True, "detail": "wc ready"},
        "auto_manage_spy": {"ok": True, "has_spy": False, "detail": "none"},
        "auto_war_response": {"ok": True, "at_war": False, "detail": "peace"},
        "end_turn": {"ok": True, "detail": "end"},
        "read_situation": {"ok": True, "turn": 10},
    }
    for name, value in values.items():
        monkeypatch.setattr(_main.civ6_tuner, name, stub(name, value))
    monkeypatch.setattr(_main.asyncio, "sleep", _no_sleep)

    result = asyncio.run(plugin._tuner_manage_turn(
        9, False, "unit", "UNIT_BUILDER", "TECH_POTTERY", "CIVIC_CODE_OF_LAWS",
    ))
    assert result["handoff"] is False
    assert "auto_manage_money" not in calls
    assert "auto_found_religion" not in calls
    assert "auto_recruit_great_person" not in calls
    assert "auto_trade_route" not in calls
    assert "auto_irreversible=false" in "".join(result["log"])


def test_commemoration_missing_before_is_not_reported_as_success(monkeypatch):
    writer = _Writer()

    async def handshake(*_args, **_kwargs):
        return "APP", ["3", "GameCore_Tuner", "27", "InGame"], object(), writer

    async def execute(_reader, _writer, code, **_kwargs):
        if "COMM|" in code:
            return ["COMM|allowed=3|before=-1|still=false"]
        return ["RESULT|TEST_COMMEMORATION"]

    monkeypatch.setattr(_tuner, "_handshake_retry", handshake)
    monkeypatch.setattr(_tuner, "execute_lua", execute)
    monkeypatch.setattr(_tuner.asyncio, "sleep", _no_sleep)
    result = asyncio.run(_tuner.auto_choose_commemoration(timeout=2.0))
    assert result["ok"] is False
    assert result["error"] == "SILENT_FAILURE"
    assert result.get("verified") is not True
    assert writer.closed is True


def test_ops_lock_path_falls_back_when_localappdata_empty(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "")
    path = _main._ops_lock_path()
    assert path.endswith(os.path.join("N.E.K.O", "config", "civ6_ops_active"))
    assert os.path.isabs(path)
    assert str(Path.home()) in path


def test_app_options_read_failure_is_structured(monkeypatch, tmp_path):
    path = tmp_path / "AppOptions.txt"
    path.write_bytes(b"[Debug]\r\nEnableTuner 0\r\n")

    def boom(_self):
        raise OSError("simulated read failure")

    monkeypatch.setattr(_sdk.Path, "read_bytes", boom)
    result = _sdk.ensure_tuner_enabled(path)
    assert result["changed"] is False
    assert result["error"] == "APP_OPTIONS_READ_FAILED"
    assert result["detail"].find("simulated read failure") >= 0
    with path.open("rb") as handle:
        assert handle.read() == b"[Debug]\r\nEnableTuner 0\r\n"


def _strip_lua_noise(text: str) -> str:
    """去掉注释与字符串，保留块关键字，供平衡检查使用。"""
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            if text.startswith("--[[", i):
                end = text.find("]]", i + 4)
                i = n if end < 0 else end + 2
                continue
            newline = text.find("\n", i)
            i = n if newline < 0 else newline + 1
            continue
        if text.startswith("[[", i):
            end = text.find("]]", i + 2)
            i = n if end < 0 else end + 2
            continue
        if text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


_BLOCK_RX = re.compile(r"\b(function|if|do|repeat|end|until)\b")


def _block_balance(source: str) -> tuple[int, int]:
    tokens = _BLOCK_RX.findall(_strip_lua_noise(source))
    opens = sum(token in {"function", "if", "do", "repeat"} for token in tokens)
    closes = sum(token in {"end", "until"} for token in tokens)
    return opens, closes


@pytest.mark.parametrize("path", sorted(_LUA_ROOT.glob("*.lua")), ids=lambda p: p.stem)
def test_all_lua_files_have_balanced_blocks(path):
    opens, closes = _block_balance(path.read_text(encoding="utf-8"))
    assert opens == closes, f"{path.name}: {opens} opens vs {closes} closes"


_FORMAT_TEMPLATES = {
    "set_focus": {"yield_type": "YIELD_FOOD"},
    "set_city_production_request": {
        "p0": "1", "p1": "1", "p2": "1", "p3": "1", "p4": "1", "p5": "1",
        "p6": "1", "p7": "unit", "p8": "PARAM_UNIT_TYPE", "p9": "true",
        "p10": "UNIT_SCOUT", "p11": "UNIT_SCOUT", "p12": "Units",
    },
    "set_city_production_verify": {"p0": "true", "p1": "UNIT_SCOUT", "p2": "Units"},
    "set_research": {
        "p0": "TECH_POTTERY", "p1": "TECH_POTTERY", "p2": "TECH_POTTERY",
        "p3": "RESEARCH", "p4": "PARAM_TECH_TYPE", "p5": "TECH_POTTERY",
        "p6": "HasTech", "p7": "GetTechs", "p8": "TECH_POTTERY",
        "p9": "TECH_POTTERY", "p10": "TechnologyType", "p11": "Technologies",
    },
    "set_research_verify": {"p0": "TECH_POTTERY", "p1": "GetResearchingTech", "p2": "GetTechs"},
    "skip_idle_units": {"forced": "false"},
    "auto_pick_research": {
        "p0": "TechnologyType", "p1": "HasTech", "p2": "GetTechs", "p3": "Technologies",
    },
    "auto_fill_policy_slots_verify": {"p0": "1"},
}


@pytest.mark.parametrize("name", sorted(_FORMAT_TEMPLATES))
def test_format_templates_expand_with_known_placeholders(name):
    _resources.load_lua(name).format(**_FORMAT_TEMPLATES[name])
