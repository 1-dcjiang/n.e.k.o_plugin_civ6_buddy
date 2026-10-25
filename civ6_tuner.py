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


def _parse_lua_states(raw: list[str]) -> dict[int, str]:
    """LSQ 返回交错 [index, name] 列表 → {index: name}。

    示例：['0','Main State','1','DebugHotloadCache',...,'3','GameCore_Tuner',...]"""
    result: dict[int, str] = {}
    i = 0
    while i + 1 < len(raw):
        try:
            idx = int(raw[i].strip())
        except ValueError:
            i += 1
            continue
        result[idx] = raw[i + 1].strip()
        i += 2
    return result


def _gamecore_state_index(states: list[str], fallback: int = 0) -> int:
    """从 LSQ 原始列表解析出可执行 Lua 的 state 索引。

    优先 GameCore_Tuner（游戏核心状态：Players/Game 可用）；
    次选 InGame；都没有才回退 0。"""
    parsed = _parse_lua_states(states)
    for idx, name in parsed.items():
        if name == "GameCore_Tuner":
            return idx
    for idx, name in parsed.items():
        if name == "InGame":
            return idx
    return fallback


class TunerError(Exception):
    """FireTuner 连接/执行错误。"""


def _to_int(raw) -> int:
    """数值字段安全转 int（游戏返回可能带小数/空格/异常值，失败返回 0）。"""
    try:
        return int(float(str(raw or 0).strip()))
    except (TypeError, ValueError):
        return 0


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
        except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionAbortedError, OSError):
            # 服务端关闭连接/重置 = 没有更多数据，幂等返回
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
    except TunerError:
        writer.close()
        raise
    except Exception as exc:
        writer.close()
        raise TunerError(f"握手失败：{exc}") from None


def _parse_output_payload(payload: str) -> str | None:
    """从 print 输出帧提取内容。

    FireTuner 的 print 输出帧格式：`O\x00<context_name>: <value>`
    （O + null + Lua state 名 + ': ' + 值）。非输出帧（空 ack 等）返回 None。"""
    if not payload.startswith("O"):
        return None
    sep = payload.find(": ", 2)
    if sep >= 0:
        return payload[sep + 2:]
    return payload.lstrip("O").lstrip("\x00").strip()


async def execute_lua(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    code: str,
    state_index: int = 0,
    timeout: float = 8.0,
) -> list[str]:
    """在指定 Lua state 执行脚本，**循环收集**全部 print 输出行。

    FireTuner 的 print 输出是多帧异步流（`O\\x00<state>: <value>`），
    单帧读取会丢行/错位。此处：执行前清陈旧帧 → 发 CMD → 循环读帧
    直到超时 → 解析输出行 → 再清尾部。"""
    await _drain(reader, timeout=0.1)
    await _send(writer, TAG_COMMAND, f"CMD:{state_index}:{code}")
    lines: list[str] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            _, payload = await _recv(reader, min(remaining, 2.0))
        except TunerError:
            break  # 读取超时/帧损坏 → 停止收集
        text = _parse_output_payload(payload)
        if text is not None and text.strip():
            lines.append(text.strip())
    await _drain(reader, timeout=0.2)
    return lines


async def _handshake_retry(retries: int = 2, delay: float = 0.5):
    """握手带重试：游戏 Tuner 单连接限制下，紧接的连接可能被 RST（前连接未释放），
    短暂等待后重试即可成功。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await handshake()
        except TunerError as exc:
            last = exc
            if attempt < retries:
                await asyncio.sleep(delay)
    raise last if last else TunerError("握手失败")


async def tuner_status() -> dict:
    """只读探测：Tuner 是否可用。无 Tuner 时返回明确错误码。"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "states": []}
    try:
        writer.close()
    except Exception:
        pass
    parsed = _parse_lua_states(states)
    return {"ok": True, "app": app_id, "states": states,
            "gamecore_index": _gamecore_state_index(states),
            "state_count": len(parsed),
            "detail": f"Tuner 已连接（{app_id}，Lua states：{len(parsed)} 个，GameCore={_gamecore_state_index(states)}）"}


# ── Lua 脚本（焦点式市民分配，游戏官方机制） ──
# 主逻辑统一用 pcall 包裹：API 名在不同版本/资料片有差异，失败输出明确 ERR 而非挂死超时。

LUA_SET_FOCUS = """
local targetYield = "{yield_type}"
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local yRow = GameInfo.Yields[targetYield]
if yRow == nil then print("ERR:YIELD_NOT_FOUND|" .. targetYield) return end
local okApply = pcall(function()
  -- 遍历该玩家全部城市（GetCities():Members()），设置焦点（DEFAULT 清空全部焦点）
  for i, pCity in pPlayer:GetCities():Members() do
    local citz = pCity:GetCitizens()
    for yr in GameInfo.Yields() do
      local okFav, fav = pcall(function() return citz:IsFavoredYield(yr.Index) end)
      if okFav and fav then pcall(function() citz:SetYieldFocus(yr.Index, false) end) end
    end
    if targetYield ~= "YIELD_DEFAULT" then
      local okSet = pcall(function() citz:SetYieldFocus(yRow.Index, true) end)
      if not okSet then print("ERR:SETFOCUS_FAILED|" .. pCity:GetName()) end
    end
    print("OK|" .. pCity:GetName() .. "|focus=" .. targetYield)
  end
end)
if not okApply then print("ERR:SETFOCUS_FAILED") end
"""

LUA_LIST_CITIES = """
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local okList = pcall(function()
  for i, c in pPlayer:GetCities():Members() do
    local pop, growth = c:GetPopulation(), nil
    local okG = pcall(function()
      local g = c:GetGrowth()
      if g then growth = tostring(g:GetTurnsUntilGrowth()) end
    end)
    if not okG then growth = "?" end
    local yields = {{}}
    for y = 0, 5 do
      local okY, v = pcall(function() return c:GetYield(y) end)
      yields[y + 1] = okY and tostring(v) or "?"
    end
    print(string.format("%s|pop=%d|food=%s|prod=%s|gold=%s|sci=%s|cult=%s|faith=%s|growth=%s",
      c:GetName(), pop, yields[1], yields[2], yields[3], yields[4], yields[5], yields[6], growth))
  end
end)
if not okList then print("ERR:LISTCITIES_FAILED") end
"""


async def list_cities(timeout: float = 8.0) -> dict:
    """读本城全部城市的人口与六项产出（FireTuner）。"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "cities": []}
    try:
        out = await execute_lua(reader, writer, LUA_LIST_CITIES,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "cities": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    cities = []
    for line in out:
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
            "population": _to_int(parts[1].replace("pop=", "")),
            "yields": {k: _to_int(parts[2 + i]) for i, k in
                       enumerate(("food", "prod", "gold", "science", "culture", "faith"))
                       if 2 + i < len(parts)},
        })
    return {"ok": True, "app": app_id, "cities": cities}


# 战争状态（v0.4.0）：Tuner 真实读取——比像素横幅检测可靠得多，
# 用于"分清打仗和运营"：战争中 YUI 拒绝运营、立即交还控制权。
# pcall 容错：不同版本 API 名可能有差异，失败返回明确 ERR 而非挂死/超时。
LUA_AT_WAR = """
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local ok2, pDiplo = pcall(function() return pPlayer:GetDiplomacy() end)
if not ok2 or pDiplo == nil then print("ERR:NO_DIPLOMACY") return end
local count = 0
-- 遍历全部玩家槽（硬编码 0..62：GetNumPlayers 在该环境不可靠），逐人查 IsAtWarWith
for i = 0, 62 do
  local p = Players[i]
  if p ~= nil then
    local okAlive, alive = pcall(function() return p:IsAlive() end)
    if okAlive and alive then
      local okWar, atWar = pcall(function() return pDiplo:IsAtWarWith(i) end)
      if okWar and atWar then count = count + 1 end
    end
  end
end
print("OK|at_war=" .. tostring(count))
"""


async def at_war(timeout: float = 8.0) -> dict:
    """查询本地玩家当前处于战争的对象数（FireTuner 官方 API）。

    返回 {"ok": True, "at_war": int, ...}；Tuner 不可用返回明确错误码
    （调用方据此回退到像素横幅检测，不阻断运营）。"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, LUA_AT_WAR,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
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
    return {"ok": False, "error": "NO_RESPONSE", "detail": "|".join(out)}


LUA_SITUATION = """
local turn = 0
local p = -1
local civName = ""
local cities = 0
local techName = ""
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if okP and pPlayer ~= nil then
  p = Game.GetLocalPlayer() or -1
  turn = Game.GetGameTurn() or 0
  local okCiv, civType = pcall(function() return pPlayer:GetCivilizationType() end)
  if okCiv and civType ~= nil and GameInfo.Civilizations[civType] ~= nil then
    civName = tostring(GameInfo.Civilizations[civType].Name or "")
  end
  local okCities, cList = pcall(function() return pPlayer:GetCities() end)
  if okCities and cList ~= nil then
    local okCnt, cnt = pcall(function() return cList:GetCount() end)
    if okCnt and cnt ~= nil then cities = cnt end
  end
  local okTechs, techs = pcall(function() return pPlayer:GetTechs() end)
  if okTechs and techs ~= nil then
    local okCur, cur = pcall(function() return techs:GetCurrentResearch() end)
    if okCur and cur ~= nil and GameInfo.Technologies[cur] ~= nil then
      techName = tostring(GameInfo.Technologies[cur].Name or "")
    end
  end
end
print("OK|turn=" .. tostring(turn) .. "|p=" .. tostring(p) .. "|civ=" .. tostring(civName) .. "|cities=" .. tostring(cities) .. "|tech=" .. tostring(techName))
"""


async def read_situation(timeout: float = 8.0) -> dict:
    """Tuner 实时读取当前局面：回合 / 本地玩家 / 文明 / 城市数 / 当前研究科技。

    返回 {"ok": True, "app", "turn", "player", "civ", "cities", "tech", "via": "tuner"}；
    Tuner 不可用返回 {"ok": False, "error": "GAME_TUNER_OFF"|...}（调用方回退存档解析）。
    """
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, LUA_SITUATION,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR"):
            return {"ok": False, "error": line.split("|")[0], "detail": line}
        if line.startswith("OK|"):
            vals: dict[str, str] = {}
            for part in line[3:].split("|"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    vals[k] = v
            try:
                turn = int(vals.get("turn", "0"))
            except ValueError:
                turn = 0
            try:
                player = int(vals.get("p", "-1"))
            except ValueError:
                player = -1
            try:
                cities = int(vals.get("cities", "0"))
            except ValueError:
                cities = 0
            return {
                "ok": True,
                "app": app_id,
                "turn": turn,
                "player": player,
                "civ": vals.get("civ", ""),
                "cities": cities,
                "tech": vals.get("tech", ""),
                "via": "tuner",
            }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "|".join(out)}


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
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "results": []}
    try:
        out = await execute_lua(reader, writer, LUA_SET_FOCUS.format(yield_type=yield_name),
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "results": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    results = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0].startswith("ERR"):
            return {"ok": False, "error": parts[0], "detail": line, "results": results}
        if parts[0] == "OK":
            results.append({"city": parts[1], "focus": parts[2]})
    return {"ok": True, "app": app_id, "focus": yield_name, "results": results}
