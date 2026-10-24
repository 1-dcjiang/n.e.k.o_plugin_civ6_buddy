"""civ6_tuner 协议与错误码测试（不连真实游戏，CI 可跑）。

覆盖：帧打包/解包、非法帧防御、set_focus 参数校验、
无 Tuner 时 list_cities/tuner_status 返回明确错误码。
"""

import importlib

import pytest

_tuner = importlib.import_module("civ6_tuner")


def test_pack_unpack_roundtrip():
    payload = "CMD:0:print('hi')"
    raw = _tuner._pack(_tuner.TAG_COMMAND, payload)
    tag, body = _tuner._unpack(raw)
    assert tag == _tuner.TAG_COMMAND
    assert body == payload


def test_unpack_rejects_short_frame():
    with pytest.raises(_tuner.TunerError):
        _tuner._unpack(b"\x00\x01")


def test_unpack_rejects_bad_length():
    # 声称长度 9999999999（>1e7 上限）
    raw = _tuner._pack(_tuner.TAG_COMMAND, "hi")
    # 手工构造超长长度帧
    import struct
    buf = struct.pack(_tuner.HEADER_FMT, 99_999_999, _tuner.TAG_COMMAND) + b"\x00"
    with pytest.raises(_tuner.TunerError):
        _tuner._unpack(buf)


def test_tuner_status_no_game():
    import asyncio
    r = asyncio.run(_tuner.tuner_status())
    # 未开 Tuner/未装 SDK 时必然连不上（本机当前无 4318 监听）
    assert r["ok"] is False
    assert r["error"] == "GAME_TUNER_OFF"


def test_list_cities_no_game():
    import asyncio
    r = asyncio.run(_tuner.list_cities())
    assert r["ok"] is False
    assert r["error"] == "GAME_TUNER_OFF"
    assert r["cities"] == []


def test_set_focus_validates_yield():
    import asyncio
    r = asyncio.run(_tuner.set_focus("NOPE"))
    assert r["ok"] is False
    assert r["error"] == "BAD_YIELD"
    r2 = asyncio.run(_tuner.set_focus("production"))
    assert r2["ok"] is False
    assert r2["error"] == "GAME_TUNER_OFF"  # 参数合法但无游戏


def test_lua_scripts_contain_guards():
    # 脚本含本地玩家守卫，防未进局误执行
    assert "ERR:NO_PLAYER" in _tuner.LUA_LIST_CITIES
    assert "ERR:NO_PLAYER" in _tuner.LUA_SET_FOCUS
    assert "YIELD_DEFAULT" in _tuner.LUA_SET_FOCUS


def test_at_war_no_game():
    import asyncio
    r = asyncio.run(_tuner.at_war())
    # 未开 Tuner 时必然连不上
    assert r["ok"] is False
    assert r["error"] == "GAME_TUNER_OFF"


def test_at_war_lua_guard():
    # 战争脚本含本地玩家守卫 + 战争计数
    assert "ERR:NO_PLAYER" in _tuner.LUA_AT_WAR
    assert "GetAtWarCount" in _tuner.LUA_AT_WAR
    assert "at_war=" in _tuner.LUA_AT_WAR
