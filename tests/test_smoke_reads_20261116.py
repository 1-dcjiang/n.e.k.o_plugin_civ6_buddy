"""真实 FireTuner 只读 smoke 发现并修复的解析/API 名称回归。"""

from __future__ import annotations

import asyncio
import importlib

_tuner = importlib.import_module("civ6_tuner")
_resources = importlib.import_module("civ6_lua_resources")


class _Writer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fake_io(monkeypatch, lines):
    writer = _Writer()

    async def handshake(*_args, **_kwargs):
        return "APP", ["3", "GameCore_Tuner", "96", "InGame"], object(), writer

    async def execute(_reader, _writer, _code, state_index=0, timeout=0):
        return list(lines)

    monkeypatch.setattr(_tuner, "_handshake_retry", handshake)
    monkeypatch.setattr(_tuner, "execute_lua", execute)
    return writer


def test_list_cities_parses_labeled_yields(monkeypatch):
    # 真实 Lua 输出带 food=/prod= 标签；旧解析直接把整段丢给 _to_int，六项全变 0。
    _fake_io(monkeypatch, ["姆班扎刚果|pop=13|food=27|prod=61|gold=113|sci=45|cult=25|faith=12|growth=?"])
    result = asyncio.run(_tuner.list_cities())
    assert result["ok"] is True
    city = result["cities"][0]
    assert city["population"] == 13
    assert city["yields"] == {
        "food": 27, "prod": 61, "gold": 113,
        "science": 45, "culture": 25, "faith": 12,
    }


def test_read_scripts_use_real_game_api_names():
    # 实测这些方法在本作 Lua 不存在，误用会让字段静默变空/变 -1。
    situation = _resources.load_lua("situation")
    culture = _resources.load_lua("read_culture")
    economy = _resources.load_lua("read_economy")
    diplomacy = _resources.load_lua("read_diplomacy")
    cities = _resources.load_lua("list_cities")

    assert "GetResearchingTech" in situation
    assert "GetCivilizationType()" not in situation
    assert "GetProgressingCivic" in culture
    assert "GetCurrentCivic" not in culture
    assert "GetReligion():GetFaithBalance()" in economy
    assert "GetFaithBalance" not in economy.replace("GetReligion():GetFaithBalance()", "")
    assert "Map.GetPlotDistance(" in diplomacy
    assert "GetPlotsDistance" not in diplomacy
    assert "local yields = {}" in cities
    assert "local yields = {{}}" not in cities
