"""civ6_tuner.py — 文明6 FireTuner 底层通道（v0.3.0）。

通过游戏内 FireTuner 调试端口（默认 TCP 4318）直连游戏 Lua 仿真层：
读城市/人口/产出/焦点，用官方焦点 API 执行市民分配（比屏幕像素点击可靠）。

前置条件（Windows）：
1. Steam 安装 "Sid Meier's Civilization VI Development Assets"（appid 597260）
   —— tuner server 在 SDK 里，游戏本体不含。
2. 游戏启用 Tuner：AppOptions.txt 加 [Debug] EnableTuner 1
   （或游戏内选项 "Tuner (disables achievements)"）——开启会禁用成就。
3. 重启游戏，端口 4318 开始监听（同屏只能一个 tuner 连接，
   需先关闭 SDK 的 FireTuner.exe）。

线协议（Firaxis Nexus）：
[4-byte LE uint32 length][4-byte LE int32 tag][null-terminated payload]
tag: 4=handshake(APP:/LSQ:), 3=command(CMD:<state>:<lua>), 1=help
"""

from __future__ import annotations

import asyncio
import logging
import struct

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_tuner")

HEADER_FMT = "<Ii"  # length(uint32 LE), tag(int32 LE)
HEADER_SIZE = struct.calcsize(HEADER_FMT)

TAG_HANDSHAKE = 4
TAG_COMMAND = 3

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4318


class TunerError(Exception):
    """FireTuner 连接/执行错误。"""


def _pack(tag: int, payload: str) -> bytes:
    data = payload.encode("utf-8") + b"\x00"
    return struct.pack(HEADER_FMT, len(data), tag) + data


def _unpack(buf: bytes) -> tuple[int, str]:
    if len(buf) < HEADER_SIZE:
        raise TunerError(f"帧过短：{len(buf)} 字节")
    length, tag = struct.unpack(HEADER_FMT, buf[:HEADER_SIZE])
    body = buf[HEADER_SIZE:HEADER_SIZE + length]
    if len(body) < length:
        raise TunerError(f"帧体不完整：需要 {length}，实际 {len(body)}")
    return tag, body.rstrip(b"\x00").decode("utf-8", errors="replace")


async def _send(writer: asyncio.StreamWriter, tag: int, payload: str) -> None:
    writer.write(_pack(tag, payload))
    await writer.drain()


async def _recv(reader: asyncio.StreamReader, timeout: float) -> tuple[int, str]:
    try:
        header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
    except asyncio.TimeoutError:
        raise TunerError("读取超时（游戏无响应，可尝试重启游戏）") from None
    length, tag = struct.unpack(HEADER_FMT, header)
    if length <= 0 or length > 10_000_000:
        raise TunerError(f"非法帧长度：{length}")
    body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    return tag, body.rstrip(b"\x00").decode("utf-8", errors="replace")


async def _drain(reader: asyncio.StreamReader, timeout: float = 0.2) -> None:
    """清空未请求消息（握手/命令后残余输出）。"""
    while True:
        try:
            await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
        except asyncio.TimeoutError:
            return
        except asyncio.IncompleteReadError:
            return


async def _connect(host: str, port: int, timeout: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except OSError as exc:
        raise TunerError(f"无法连接 4318 端口：{exc}（游戏未运行或未启用 Tuner）") from None
    except asyncio.TimeoutError:
        raise TunerError("连接超时") from None


async def handshake(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 5.0,
) -> tuple[str, list[str], asyncio.StreamReader, asyncio.StreamWriter]:
    """连接 + 握手。返回 (app_identity, lua_states, reader, writer)。"""
    reader, writer = await _connect(host, port, timeout)
    try:
        await _drain(reader)
        await _send(writer, TAG_HANDSHAKE, "APP:")
        _, app_id = await _recv(reader, timeout)
        await _send(writer, TAG_HANDSHAKE, "LSQ:")
        _, raw = await _recv(reader, timeout)
        states = [s.strip() for s in raw.replace("\x00", "\n").split("\n") if s.strip()]
        return app_id, states, reader, writer
    except Exception:
        writer.close()
        raise


async def execute_lua(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    code: str,
    state_index: int = 0,
    timeout: float = 8.0,
) -> str:
    """在指定 Lua state 执行脚本，返回游戏 print 输出。"""
    await _send(writer, TAG_COMMAND, f"CMD:{state_index}:{code}")
    _, payload = await _recv(reader, timeout)
    await _drain(reader)
    return payload


async def tuner_status() -> dict:
    """只读探测：Tuner 是否可用。无 Tuner 时返回明确错误码。"""
    try:
        app_id, states, reader, writer = await handshake()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "states": []}
    try:
        writer.close()
    except Exception:
        pass
    return {"ok": True, "app": app_id, "states": states,
            "detail": f"Tuner 已连接（{app_id}，Lua states：{len(states)}）"}


# ── Lua 脚本（焦点式市民分配，游戏官方机制） ──

LUA_SET_FOCUS = """
local yields = {"YIELD_FOOD","YIELD_PRODUCTION","YIELD_GOLD","YIELD_SCIENCE","YIELD_CULTURE","YIELD_FAITH"}
local pCity = nil
local pPlayer = Players[Game.GetLocalPlayer() or 0]
if pPlayer == nil then print("ERR:NO_PLAYER") return end
local targetYield = "{yield_type}"
local yRow = GameInfo.Yields[targetYield]
if yRow == nil then print("ERR:YIELD_NOT_FOUND|" .. targetYield) return end
-- 遍历该玩家城市，设置焦点（DEFAULT 清空全部焦点）
for i, pCity in ipairs(pPlayer:GetCities()) do
  local citz = pCity:GetCitizens()
  for yr in GameInfo.Yields() do
    if citz:IsFavoredYield(yr.Index) then citz:SetYieldFocus(yr.Index, false) end
  end
  if targetYield ~= "YIELD_DEFAULT" then
    citz:SetYieldFocus(yRow.Index, true)
  end
  print("OK|" .. pCity:GetName() .. "|focus=" .. targetYield)
end
"""

LUA_LIST_CITIES = """
local pPlayer = Players[Game.GetLocalPlayer() or 0]
if pPlayer == nil then print("ERR:NO_PLAYER") return end
for i, c in ipairs(pPlayer:GetCities()) do
  local g = c:GetGrowth()
  print(string.format("%s|pop=%d|food=%s|prod=%s|gold=%s|sci=%s|cult=%s|faith=%s|growth=%s",
    c:GetName(), c:GetPopulation(),
    c:GetYield(0), c:GetYield(1), c:GetYield(2), c:GetYield(3), c:GetYield(4), c:GetYield(5),
    tostring(g and g:GetTurnsUntilGrowth())))
end
"""


async def list_cities(timeout: float = 8.0) -> dict:
    """读本城全部城市的人口与六项产出（FireTuner）。"""
    try:
        app_id, states, reader, writer = await handshake()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "cities": []}
    try:
        out = await execute_lua(reader, writer, LUA_LIST_CITIES, state_index=0, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "cities": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    cities = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0].startswith("ERR"):
            return {"ok": False, "error": parts[0], "detail": line, "cities": cities}
        if len(parts) < 2 or "pop=" not in line:
            continue
        cities.append({
            "name": parts[0],
            "population": parts[1].replace("pop=", ""),
            "yields": {k: parts[2 + i] for i, k in
                       enumerate(("food", "prod", "gold", "science", "culture", "faith"))
                       if 2 + i < len(parts)},
        })
    return {"ok": True, "app": app_id, "cities": cities}


# 战争状态（v0.4.0）：Tuner 真实读取——比像素横幅检测可靠得多，
# 用于"分清打仗和运营"：战争中 YUI 拒绝运营、立即交还控制权。
LUA_AT_WAR = """
local pPlayer = Players[Game.GetLocalPlayer() or 0]
if pPlayer == nil then print("ERR:NO_PLAYER") return end
local pDiplo = pPlayer:GetDiplomacy()
if pDiplo == nil then print("ERR:NO_DIPLOMACY") return end
local count = pDiplo:GetAtWarCount()
print("OK|at_war=" .. tostring(count or 0))
"""


async def at_war(timeout: float = 8.0) -> dict:
    """查询本地玩家当前处于战争的对象数（FireTuner 官方 API）。

    返回 {"ok": True, "at_war": int, ...}；Tuner 不可用返回明确错误码
    （调用方据此回退到像素横幅检测，不阻断运营）。"""
    try:
        app_id, states, reader, writer = await handshake()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, LUA_AT_WAR, state_index=0, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR"):
            return {"ok": False, "error": line.split("|")[0], "detail": line}
        if line.startswith("OK|at_war="):
            try:
                return {"ok": True, "app": app_id, "at_war": int(line.split("=")[1])}
            except (IndexError, ValueError):
                return {"ok": False, "error": "BAD_RESPONSE", "detail": line}
    return {"ok": False, "error": "NO_RESPONSE", "detail": out.strip()}


async def set_focus(yield_name: str, timeout: float = 8.0) -> dict:
    """为本城全部城市设置产出焦点（市民分配，官方机制）。
    yield_name: FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT(清空)。"""
    yield_name = str(yield_name or "").strip().upper()
    if not yield_name.startswith("YIELD_"):
        yield_name = "YIELD_" + yield_name
    valid = {"YIELD_FOOD", "YIELD_PRODUCTION", "YIELD_GOLD", "YIELD_SCIENCE",
             "YIELD_CULTURE", "YIELD_FAITH", "YIELD_DEFAULT"}
    if yield_name not in valid:
        return {"ok": False, "error": "BAD_YIELD",
                "detail": f"未知焦点 {yield_name}，可用：FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT"}
    try:
        app_id, states, reader, writer = await handshake()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "results": []}
    try:
        out = await execute_lua(reader, writer, LUA_SET_FOCUS.format(yield_type=yield_name),
                                state_index=0, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "results": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    results = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0].startswith("ERR"):
            return {"ok": False, "error": parts[0], "detail": line, "results": results}
        if parts[0] == "OK":
            results.append({"city": parts[1], "focus": parts[2]})
    return {"ok": True, "app": app_id, "focus": yield_name, "results": results}
