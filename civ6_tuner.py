"""civ6_tuner.py — 文明6 FireTuner 底层通道（v0.5.0）。

通过游戏内 FireTuner 调试端口（默认 TCP 4318）直连游戏 Lua 仿真层：
读城市/人口/产出/焦点/生产队列/回合，用官方 API 执行市民分配、
生产设置（CityManager.RequestOperation）与结束回合（ACTION_ENDTURN）。

【Lua state 分工（实测铁律）】
- GameCore（gamecore_index）：读局面常量（回合=Game.GetCurrentGameTurn、文明、科技名等）。
- InGame：UI 仿真层。写操作必须在此（RequestOperation / ACTION_ENDTURN 在此才生效）；
  状态索引由 LSQ 动态解析（历史实测曾为 96，不能硬编码）。生产队列也必须在 InGame 读，
  GameCore 读到的是旧/空状态，会误判"设置生产失败"。

【回合读取】正确 API 是 Game.GetCurrentGameTurn()（不是 GetGameTurn——后者在本
游戏版本为 nil，会导致回合数恒为 0 的假 bug）。

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
import re

try:
    from . import civ6_tuner_protocol as _protocol
    from . import civ6_window as _civ6_window
except ImportError:  # 包外直接加载（测试/独立脚本）
    import civ6_tuner_protocol as _protocol  # type: ignore
    import civ6_window as _civ6_window  # type: ignore

# 协议与窗口接口保持原模块路径兼容；运营决策仍由本模块负责。
HEADER_FMT = _protocol.HEADER_FMT
HEADER_SIZE = _protocol.HEADER_SIZE
TAG_HANDSHAKE = _protocol.TAG_HANDSHAKE
TAG_COMMAND = _protocol.TAG_COMMAND
DEFAULT_HOST = _protocol.DEFAULT_HOST
DEFAULT_PORT = _protocol.DEFAULT_PORT
TunerError = _protocol.TunerError
_parse_lua_states = _protocol._parse_lua_states
_gamecore_state_index = _protocol._gamecore_state_index
_to_int = _protocol._to_int
_to_float = _protocol._to_float
_pack = _protocol._pack
_unpack = _protocol._unpack
_send = _protocol._send
_recv = _protocol._recv
_drain = _protocol._drain
_connect = _protocol._connect
handshake = _protocol.handshake
_parse_output_payload = _protocol._parse_output_payload
execute_lua = _protocol.execute_lua
_handshake_retry = _protocol._handshake_retry
_right_click_game_window = _civ6_window.right_click_game_window


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
  pcall(function() turn = Game.GetCurrentGameTurn() or 0 end)
  local okCiv, civType = pcall(function() return pPlayer:GetCivilizationType() end)
  if okCiv and civType ~= nil then
    pcall(function()
      if GameInfo and GameInfo.Civilizations and GameInfo.Civilizations[civType] ~= nil then
        civName = tostring(GameInfo.Civilizations[civType].Name or "")
      end
    end)
  end
  local okCities, cList = pcall(function() return pPlayer:GetCities() end)
  if okCities and cList ~= nil then
    local okCnt, cnt = pcall(function() return cList:GetCount() end)
    if okCnt and cnt ~= nil then cities = cnt end
  end
  local okTechs, techs = pcall(function() return pPlayer:GetTechs() end)
  if okTechs and techs ~= nil then
    local okCur, cur = pcall(function() return techs:GetCurrentResearch() end)
    if okCur and cur ~= nil then
      pcall(function()
        if GameInfo and GameInfo.Technologies and GameInfo.Technologies[cur] ~= nil then
          techName = tostring(GameInfo.Technologies[cur].Name or "")
        end
      end)
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


# ══════════ v0.5.0：Tuner 写链路（生产设置 / 结束回合 / 生产队列读取） ══════════

# InGame（UI 仿真层）state 索引。实测：写操作（RequestOperation/ACTION_ENDTURN）与
# 生产队列读取必须在 InGame 执行；GameCore 读队列会拿到旧/空状态。
INGAME_STATE = 96

# 物品类型 → GameInfo 表名
_ITEM_KIND_TO_TABLE = {
    "unit": "Units",
    "building": "Buildings",
    "district": "Districts",
    "project": "Projects",
}


def _ingame_state_index(states: list[str] | None, fallback: int = INGAME_STATE) -> int:
    """按 LSQ 的交错 [index, name] 格式解析 InGame；仅无法识别时使用兼容回退。"""
    parsed = _parse_lua_states(list(states or []))
    for index, name in parsed.items():
        if name == "InGame" or name.endswith("/InGame"):
            return index
    return fallback


async def read_production(timeout: float = 8.0) -> dict:
    """读取本地玩家全部城市的生产队列（InGame 层，与写操作同 context）。

    返回 {"ok": True, "turn", "cities":[{name, size, current_type, current_hash,
    turns_left}], "via": "tuner"}。生产队列为空（size=0）说明城市没选生产，
    文明6 会拒绝结束回合——这是"挂机不动"的根因，调用方需先 set_city_production。
    """
    lua = """
local s = {}
local okT, t = pcall(function() return Game.GetCurrentGameTurn() end)
s[#s+1] = "TURN=" .. tostring(t or -1)
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not okP or pPlayer == nil then
  s[#s+1] = "ERR=NO_PLAYER"
  print(table.concat(s, "&"))
  return
end
pcall(function()
  local idx = 0
  for i, c in pPlayer:GetCities():Members() do
    idx = idx + 1
    local q = c:GetBuildQueue()
    local sz = 0
    pcall(function() sz = q:GetSize() or 0 end)
    local curH = 0
    pcall(function() curH = q:GetCurrentProductionTypeHash() or 0 end)
    local tl = -1
    pcall(function() tl = q:GetTurnsLeft() or -1 end)
    s[#s+1] = string.format("C%d|sz=%d|h=%d|tl=%d", idx, sz, curH, tl)
  end
end)
print(table.concat(s, "&"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "cities": []}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "cities": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    turn: int | None = None
    cities: list[dict] = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR="):
            return {"ok": False, "error": "NO_PLAYER", "detail": line, "cities": cities}
        # 单帧汇总格式：TURN=9&C1|sz=1|h=1872107673|tl=1&C2|sz=0|h=0|tl=-1
        for chunk in line.split("&"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.startswith("TURN="):
                turn = _to_int(chunk[len("TURN="):])
            elif chunk.startswith("C") and "|" in chunk:
                parts = chunk.split("|")
                kv = {p.split("=")[0]: p.split("=", 1)[1] for p in parts[1:] if "=" in p}
                cities.append({
                    "name": parts[0],
                    "size": _to_int(kv.get("sz", "0")),
                    "hash": _to_int(kv.get("h", "0")),
                    "current_type": "",
                    "turns_left": _to_int(kv.get("tl", "-1")),
                })
    # 城市名/当前生产类型：交给调用方用 list_cities 或 ItemHash 映射补全
    return {"ok": True, "app": app_id, "turn": turn, "cities": cities, "via": "tuner"}


_ITEM_KIND_TO_PARAM = {
    "unit": "PARAM_UNIT_TYPE",
    "building": "PARAM_BUILDING_TYPE",
    "district": "PARAM_DISTRICT_TYPE",
    "project": "PARAM_PROJECT_TYPE",
}


async def set_city_production(
    item_kind: str = "unit",
    item_name: str = "UNIT_SCOUT",
    timeout: float = 10.0,
    city_id: int | None = None,
    plot_x: int | None = None,
    plot_y: int | None = None,
) -> dict:
    """设置生产并读回项目哈希；不阻塞游戏 UI 线程，也不把已有队列当成功。

    未指定 ``city_id`` 时处理全部空队列城市；指定城市时允许替换其当前队列。
    区域还必须明确 ``plot_x/plot_y``，避免缺少选址参数时静默失败。
    """
    kind = (item_kind or "unit").strip().lower()
    if kind not in _ITEM_KIND_TO_TABLE:
        return {"ok": False, "error": "BAD_KIND", "detail": f"item_kind 需为 {list(_ITEM_KIND_TO_TABLE)}"}
    item_name = (item_name or "").strip()
    if not item_name:
        return {"ok": False, "error": "NO_ITEM", "detail": "缺少 item_name（如 UNIT_SCOUT）"}
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", item_name):
        return {"ok": False, "error": "BAD_ITEM", "detail": "item_name 仅允许大写 GameInfo Type（A-Z、0-9、下划线）"}
    if city_id is not None and (not isinstance(city_id, int) or city_id < 0):
        return {"ok": False, "error": "BAD_CITY", "detail": "city_id 必须是非负整数"}
    if kind == "district" and (
        city_id is None or not isinstance(plot_x, int) or not isinstance(plot_y, int)
        or plot_x < 0 or plot_y < 0
    ):
        return {"ok": False, "error": "DISTRICT_LOCATION_REQUIRED",
                "detail": "区域生产必须同时提供非负 city_id、plot_x、plot_y，不能沿用单位参数或自动猜地块"}

    param = _ITEM_KIND_TO_PARAM[kind]
    selected_city = -1 if city_id is None else city_id
    only_empty = "true" if city_id is None else "false"
    x, y = -1 if plot_x is None else plot_x, -1 if plot_y is None else plot_y
    request_lua = f"""
local me = Game.GetLocalPlayer()
local item = GameInfo.{_ITEM_KIND_TO_TABLE[kind]}["{item_name}"]
if item == nil then print("ERR:ITEM_NOT_FOUND|{item_name}") return end
local targets = {{}}
local rejected = {{}}
local occupied = 0
local function consider(pCity)
  if pCity == nil then return end
  local id = pCity:GetID()
  local bq = pCity:GetBuildQueue()
  local size = bq:GetSize() or 0
  if {only_empty} and size > 0 then occupied = occupied + 1; return end
  local params = {{}}
  params[CityOperationTypes.{param}] = item.Hash
  params[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
  if "{kind}" == "district" then
    params[CityOperationTypes.PARAM_X] = {x}
    params[CityOperationTypes.PARAM_Y] = {y}
    if Map.GetPlot({x}, {y}) == nil then rejected[#rejected+1] = id .. ":PLOT"; return end
  end
  local canProduce, canStart = false, false
  pcall(function() canProduce = bq:CanProduce(item.Hash, true) end)
  pcall(function() canStart = CityManager.CanStartOperation(pCity, CityOperationTypes.BUILD, params, true) end)
  if not canProduce or not canStart then rejected[#rejected+1] = id .. ":UNAVAILABLE"; return end
  CityManager.RequestOperation(pCity, CityOperationTypes.BUILD, params)
  targets[#targets+1] = id
end
if {selected_city} >= 0 then
  local pCity = CityManager.GetCity(me, {selected_city})
  if pCity == nil then print("ERR:CITY_NOT_FOUND|{selected_city}") return end
  consider(pCity)
else
  for pCity in Players[me]:GetCities():Members() do consider(pCity) end
end
__civ6_prod_targets = targets
print("REQUESTED=" .. tostring(#targets) .. "|OCCUPIED=" .. tostring(occupied)
      .. "|REJECTED=" .. table.concat(rejected, ","))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    last_detail = "设置生产无回执"
    try:
        # 写请求只发一次；后续仅轮询读回，避免重试把同一项目重复入队。
        requested = await execute_lua(
            reader, writer, request_lua, state_index=_ingame_state_index(states), timeout=timeout,
        )
        request_line = next((line for line in requested if line.startswith(("REQUESTED=", "ERR:"))), "")
        if request_line.startswith("ERR:"):
            return {"ok": False, "error": request_line.split("|")[0], "detail": request_line}
        if not request_line.startswith("REQUESTED="):
            return {"ok": False, "error": "NO_RESPONSE", "detail": "生产请求无回执，未宣称成功"}
        requested_count = _to_int(request_line.split("=", 1)[1].split("|", 1)[0])
        rejected = request_line.split("REJECTED=", 1)[1] if "REJECTED=" in request_line else ""
        if requested_count == 0:
            if rejected:
                return {"ok": False, "error": "CANNOT_PRODUCE", "detail": f"生产项目不可用：{rejected}"}
            return {"ok": True, "app": app_id, "changed": 0, "queue_size": 0,
                    "item": item_name, "kind": kind, "cities": [],
                    "detail": "所有城市已有生产队列，本次未改写，也未把旧队列冒充为新设置"}

        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return {"ok": False, "error": "TIMEOUT", "detail": "生产读回总预算已耗尽，未宣称成功"}
            await asyncio.sleep(min(0.35, remaining))
            cleanup = "true" if attempt == 2 else "false"
            verify_lua = f"""
local me = Game.GetLocalPlayer()
local item = GameInfo.{_ITEM_KIND_TO_TABLE[kind]}["{item_name}"]
local targets = __civ6_prod_targets or {{}}
local done, failed, queueSize = 0, 0, 0
local failedIds = {{}}
for _, id in ipairs(targets) do
  local city = CityManager.GetCity(me, id)
  local queue = city and city:GetBuildQueue() or nil
  local size, current = 0, 0
  if queue then
    pcall(function() size = queue:GetSize() or 0 end)
    pcall(function() current = queue:GetCurrentProductionTypeHash() or 0 end)
  end
  queueSize = queueSize + size
  if size >= 1 and current == item.Hash then done = done + 1
  else failed = failed + 1; failedIds[#failedIds+1] = id end
end
if done == #targets or {cleanup} then __civ6_prod_targets = nil end
print("SET|done=" .. tostring(done) .. "|failed=" .. tostring(failed)
      .. "|size=" .. tostring(queueSize) .. "|ids=" .. table.concat(failedIds, ","))
"""
            verified = await execute_lua(
                reader, writer, verify_lua, state_index=_ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("SET|")), "")
            values = {part.split("=")[0]: part.split("=", 1)[1]
                      for part in line[4:].split("|") if "=" in part}
            done, failed = _to_int(values.get("done")), _to_int(values.get("failed"))
            if line and done == requested_count and failed == 0:
                return {"ok": True, "app": app_id, "changed": done,
                        "queue_size": _to_int(values.get("size")), "item": item_name, "kind": kind,
                        "detail": f"已读回验证 {done} 座城市的 {item_name} 生产队列（项目哈希匹配）"}
            last_detail = f"第 {attempt + 1} 次读回未匹配：成功 {done}，失败 {failed}，城市 {values.get('ids', '')}"
        return {"ok": False, "error": "SILENT_FAILURE", "detail": last_detail + "；未宣称设置成功"}
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def end_turn(timeout: float = 8.0) -> dict:
    """结束当前回合（InGame 层 ACTION_ENDTURN）。玩家回合时有效。

    返回 {"ok": True, "detail": "回合结束指令已发出"}；Tuner 不可用返回明确错误码。
    调用方应在发出后轮询 read_production/read_situation 的回合号确认推进。
    """
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(
            reader, writer,
            "UI.RequestAction(ActionTypes.ACTION_ENDTURN); print('ENDT_OK')",
            state_index=_ingame_state_index(states), timeout=timeout,
        )
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        if line.strip().startswith("ENDT_OK"):
            return {"ok": True, "app": app_id, "detail": "回合结束指令已发出（ACTION_ENDTURN）"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "结束回合无回执（Tuner 输出丢失），请重试"}


async def read_blockers(timeout: float = 8.0) -> dict:
    """读取当前阻挡结束回合的通知（InGame NotificationManager，官方 API）。

    返回 {"ok", "blockers": [{"type": "ENDTURN_BLOCKING_PRODUCTION", "message": ...}]}。
    blockers 为空 = 游戏允许结束回合。常见类型：
    PRODUCTION（未选生产）/RESEARCH（未选科技）/CIVIC（未选市政）/
    UNIT_PROMOTION（单位可升级）/GOVERNOR_IDLE（总督空闲）/WORLD_CONGRESS_*（世界议会）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local list = NotificationManager.GetList(me)
local seen = {}
local found = 0
if list then
    for _, nid in ipairs(list) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local bt = entry:GetEndTurnBlocking()
            if bt and bt ~= 0 then
                local typeName = "UNKNOWN"
                for k, v in pairs(EndTurnBlockingTypes) do
                    if v == bt then typeName = k; break end
                end
                if not seen[typeName] then
                    seen[typeName] = true
                    local msg = (entry:GetMessage() or ""):gsub("|", "/")
                    print("BLOCKING|" .. typeName .. "|" .. msg)
                    found = found + 1
                end
            end
        end
    end
end
if found == 0 then print("NONE") end
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "blockers": []}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "blockers": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    blockers: list[dict] = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("BLOCKING|"):
            parts = line.split("|", 2)
            blockers.append({"type": parts[1], "message": parts[2] if len(parts) > 2 else ""})
    return {"ok": True, "app": app_id, "blockers": blockers, "via": "tuner"}


async def set_research(
    category: str = "tech",
    item_name: str = "TECH_BRONZE_WORKING",
    timeout: float = 10.0,
) -> dict:
    """设置当前研究（科技/市政，InGame RequestPlayerOperation 官方 API）。

    文明6 未选科技/市政会产生 ENDTURN_BLOCKING_RESEARCH / _CIVIC blocker，
    游戏拒绝结束回合——开局"挂机不动"的常见原因。本函数设置研究项并消除 blocker。

    参数 category：tech（科技，默认）/civic（市政）；
    item_name：GameInfo 的 Type（如 TECH_BRONZE_WORKING / CIVIC_CODE_OF_LAWS）。
    返回 {"ok", "category", "item", "detail"}；失败含 error（NOT_FOUND/ALREADY_COMPLETED 等）。
    """
    cat = (category or "tech").strip().lower()
    if cat not in ("tech", "civic"):
        return {"ok": False, "error": "BAD_CATEGORY", "detail": "category 需为 tech/civic"}
    item_name = (item_name or "").strip()
    if not item_name:
        return {"ok": False, "error": "NO_ITEM", "detail": "缺少 item_name（如 TECH_BRONZE_WORKING）"}
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", item_name):
        return {"ok": False, "error": "BAD_ITEM", "detail": "item_name 仅允许大写 GameInfo Type（A-Z、0-9、下划线）"}

    if cat == "tech":
        gi_table, type_field, param, operation, blocking, ok_prefix, has_m, player_m = (
            "Technologies", "TechnologyType", "PARAM_TECH_TYPE", "RESEARCH",
            "ENDTURN_BLOCKING_RESEARCH", "OK:RESEARCHING", "HasTech", "GetTechs",
        )
    else:
        gi_table, type_field, param, operation, blocking, ok_prefix, has_m, player_m = (
            "Civics", "CivicType", "PARAM_CIVIC_TYPE", "PROGRESS_CIVIC",
            "ENDTURN_BLOCKING_CIVIC", "OK:PROGRESSING", "HasCivic", "GetCulture",
        )

    lua = f"""
local id = Game.GetLocalPlayer()
local idx = nil
for row in GameInfo.{gi_table}() do
    if row.{type_field} == "{item_name}" then idx = row.Index; break end
end
if idx == nil then print("ERR:NOT_FOUND|{item_name}") return end
if Players[id]:{player_m}():{has_m}(idx) then
    print("ERR:ALREADY_COMPLETED|{item_name}") return
end
local params = {{}}
params[PlayerOperations.{param}] = idx
UI.RequestPlayerOperation(id, PlayerOperations.{operation}, params)
local cleared = 0
pcall(function()
  local list = NotificationManager.GetList(id)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(id, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt and bt == EndTurnBlockingTypes.{blocking} then
          pcall(function() NotificationManager.SendActivated(id, nid) end)
          pcall(function() NotificationManager.Dismiss(id, nid) end)
          cleared = cleared + 1
        end
      end
    end
  end
end)
print("{ok_prefix}|{item_name}|idx=" .. tostring(idx) .. "|cleared=" .. tostring(cleared))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
        wanted = -1
        for line in out:
            line = line.strip()
            if not line:
                continue
            if line.startswith("ERR"):
                return {"ok": False, "error": line.split("|")[0], "detail": line}
            if line.startswith(ok_prefix):
                parts = dict(p.split("=", 1) for p in line.split("|") if "=" in p)
                wanted = _to_int(parts.get("idx", "-1"))
                break
        if wanted < 0:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "设置研究无回执（Tuner 输出丢失），请重试"}

        # 写请求只发一次；后续只读回当前研究项，避免重复 RequestOperation。
        verify_lua = f"""
local id = Game.GetLocalPlayer()
local current = -1
pcall(function() current = Players[id]:{player_m}():{ "GetResearchingTech" if cat == "tech" else "GetProgressingCivic" }() end)
print("VERIFY|current=" .. tostring(current) .. "|want={wanted}")
"""
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.4, remaining))
            verified = await execute_lua(
                reader, writer, verify_lua, state_index=_ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("VERIFY|")), "")
            values = {part.split("=")[0]: part.split("=", 1)[1]
                      for part in line[len("VERIFY|"):].split("|") if "=" in part} if line else {}
            current = _to_int(values.get("current", "-1"))
            if current == wanted:
                return {"ok": True, "app": app_id, "category": cat, "item": item_name,
                        "verified": True,
                        "detail": f"已读回验证研究项：{item_name}（{cat}）"}
        return {"ok": False, "error": "SILENT_FAILURE",
                "detail": "设置研究后未读回目标研究项，未宣称成功"}
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def units_blocker_status(timeout: float = 8.0) -> dict:
    """检查"单位需要命令" blocker（ENDTURN_BLOCKING_UNITS）。

    文明6 单位有剩余移动力时会阻挡结束回合。本函数返回：
    {"ok", "blocked": bool, "units_with_moves": [名称列表], "skip_idle_ok": bool}
    - units_with_moves：有剩余移动力的单位（需要指挥）。
    - skip_idle_ok：是否全部单位剩余移动力 <= 0（此时可安全 FinishMoves 跳过）。
    注意：有移动力的单位属于"打仗/探索归玩家"的范围，运营方不应擅自动作；
    若运营托管且用户授权自动跳过（侦察兵探路类），可调 skip_idle_units(force=True)。
    """
    lua = """
local me = Game.GetLocalPlayer()
local busy = {}
local idle = 0
local total = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetX() ~= -9999 then
      total = total + 1
      local m = u:GetMovesRemaining() or 0
      if m > 0 then
        local nm = ""
        pcall(function() nm = u:GetName() end)
        busy[#busy+1] = tostring(nm) .. ":" .. tostring(m)
      else
        idle = idle + 1
      end
    end
  end
end)
local parts = {}
parts[#parts+1] = "TOTAL=" .. tostring(total)
parts[#parts+1] = "IDLE=" .. tostring(idle)
parts[#parts+1] = "BUSY=" .. tostring(#busy)
parts[#parts+1] = "LIST=" .. table.concat(busy, ",")
print(table.concat(parts, "&"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or "&" not in line:
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.split("&") if "=" in p}
        total = _to_int(kv.get("TOTAL", "0"))
        idle = _to_int(kv.get("IDLE", "0"))
        busy = [u for u in kv.get("LIST", "").split(",") if u]
        blocked = len(busy) > 0
        return {
            "ok": True, "app": app_id,
            "blocked": blocked,
            "units_with_moves": busy,
            "total_units": total,
            "idle_units": idle,
            "skip_idle_ok": total > 0 and not blocked,
            "via": "tuner",
        }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "单位状态无回执（Tuner 输出丢失），请重试"}


async def skip_idle_units(force: bool = False, timeout: float = 8.0) -> dict:
    """跳过单位剩余移动（GameCore_Tuner 的 UnitManager.FinishMoves），解除 UNITS blocker。

    **必须用 GameCore_Tuner(3) context**——实测 InGame(96) 的 FinishMoves/FORTIFY/WAIT_FOR/
    SetActivityType(SLEEP) 全部无效（单位移动力不变、blocker 不消），GameCore 一次生效。

    force=False（默认）：仅跳过剩余移动力 <= 0 的单位（安全，不碰有移动力的单位）。
    force=True：跳过全部单位（包括有移动力的侦察兵/非战斗单位——运营托管自动探路场景）。
    注意：不要对战争单位使用 force=True（打仗归玩家，不能替玩家动兵）。
    返回 {"ok", "skipped", "left_with_moves"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local skipped = 0
local left = 0
local forced = %s
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetX() ~= -9999 then
      local m = u:GetMovesRemaining() or 0
      if forced or m <= 0 then
        pcall(function() UnitManager.FinishMoves(u) end)
        skipped = skipped + 1
      else
        left = left + 1
      end
    end
  end
end)
print("SKIPPED=" .. tostring(skipped) .. "&LEFT=" .. tostring(left))
""" % ("true" if force else "false")
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        gc = _gamecore_state_index(states)
        out = await execute_lua(reader, writer, lua, state_index=gc, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or "&" not in line:
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.split("&") if "=" in p}
        return {
            "ok": True, "app": app_id,
            "skipped": _to_int(kv.get("SKIPPED", "0")),
            "left_with_moves": _to_int(kv.get("LEFT", "0")),
            "detail": f"跳过 {kv.get('SKIPPED', '0')} 个单位剩余移动，"
                      f"仍有 {kv.get('LEFT', '0')} 个单位有移动力待指挥",
            "via": "tuner",
        }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "跳过单位无回执（Tuner 输出丢失），请重试"}


async def auto_pick_research(category: str = "tech", timeout: float = 8.0) -> dict:
    """选第一个"可研究且未完成"的科技/市政（InGame 官方 API CanResearch）。

    用于 set_research 兜底：默认科技可能已完成（如用户已学采矿），此时自动改选
    第一个可用项（POTTERY/ANIMAL_HUSBANDRY/SAILING...）。返回 {"ok", "item", "category"}。
    """
    cat = (category or "tech").strip().lower()
    if cat not in ("tech", "civic"):
        return {"ok": False, "error": "BAD_CATEGORY", "detail": "category 需为 tech/civic"}
    if cat == "tech":
        gi_table, has_m, player_m = "Technologies", "HasTech", "GetTechs"
        type_field = "TechnologyType"
    else:
        gi_table, has_m, player_m = "Civics", "HasCivic", "GetCulture"
        type_field = "CivicType"
    lua = f"""
local id = Game.GetLocalPlayer()
local pick = ""
pcall(function()
  for row in GameInfo.{gi_table}() do
    local p = Players[id]:{player_m}()
    if p and not p:{has_m}(row.Index) then
      local ok = false
      pcall(function() ok = p:CanResearch(row.Index) end)
      if ok then pick = row.{type_field}; break end
    end
  end
end)
print("PICK=" .. pick)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("PICK="):
            continue
        item = line[len("PICK="):].strip()
        if not item:
            return {"ok": False, "error": "NO_AVAILABLE",
                    "detail": f"当前没有可研究的{cat}（可能全部完成或需先解决前置）"}
        return {"ok": True, "app": app_id, "item": item, "category": cat, "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "自动选研究无回执（Tuner 输出丢失），请重试"}


async def move_explorers(timeout: float = 20.0) -> dict:
    """让侦察兵真正向外探路（InGame RequestOperation MOVE_TO，异步生效）。

    回答"单位为什么不移动"：v0.5.1 的 skip_idle_units 只是原地结束移动力（保回合推进），
    侦察兵不会动。本函数：对每个有移动力的侦察兵（UNIT_SCOUT），选择"远离首都方向"的
    相邻可通行格（排除水域/山脉/已有我方单位），RequestOperation MOVE_TO 移动一格；
    其余单位（勇士/建造者）不动——战斗单位不主动移动（不引战，打仗归玩家），
    建造者等策略工具指挥。MOVE_TO 异步生效：调用方应先执行本函数（发命令），
    Python 侧短等待后再由 skip_idle_units 结束剩余移动力（blocker 消除）。
    返回 {"ok", "moved": n, "targets": ["9,13->10,13", ...]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
if capX < 0 then
  pcall(function()
    for _, c in Players[me]:GetCities():Members() do capX, capY = c:GetX(), c:GetY(); break end
  end)
end
local targets = {}
local seen = {}
for _, u in Players[me]:GetUnits():Members() do
  if u:GetX() ~= -9999 and not seen[u:GetID()] then
    seen[u:GetID()] = true
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_SCOUT" and (u:GetMovesRemaining() or 0) > 0 then
      local x, y = u:GetX(), u:GetY()
      local best, bestScore = nil, -999999
      for dx = -1, 1 do for dy = -1, 1 do
        if dx ~= 0 or dy ~= 0 then
          local nx, ny = x + dx, y + dy
          local p = Map.GetPlot(nx, ny)
          if p and not p:IsWater() and not p:IsMountain() then
            local occ = false
            local us = Map.GetUnitsAt(nx, ny)
            if us then
              for o in us:Units() do if o:GetOwner() == me then occ = true; break end end
            end
            if not occ then
              local score = (nx - capX) * dx + (ny - capY) * dy
              if score > bestScore then bestScore = score; best = {nx, ny} end
            end
          end
        end
      end end
      if best then
        local params = {}
        params[UnitOperationTypes.PARAM_X] = best[1]
        params[UnitOperationTypes.PARAM_Y] = best[2]
        pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params) end)
        targets[#targets+1] = tostring(x) .. "," .. tostring(y) .. "->" .. tostring(best[1]) .. "," .. tostring(best[2])
      end
    end
  end
end
print("MOVED=" .. table.concat(targets, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("MOVED="):
            continue
        targets = [t for t in line[len("MOVED="):].split("|") if t]
        return {"ok": True, "app": app_id, "moved": len(targets),
                "targets": targets,
                "detail": f"侦察兵已向外探路：{len(targets)} 个单位移动（{'、'.join(targets) or '原地'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "侦察兵移动无回执（Tuner 输出丢失），请重试"}


async def read_threats(timeout: float = 10.0) -> dict:
    """扫描本地玩家单位的威胁态势（InGame）。

    对每个己方单位读：位置/类型/战力/剩余移动力，并扫描周围 5 格内的敌方单位
    （可见范围内），输出敌方最强战力与数量。供避险决策使用——回答"碰到比自己
    强大的敌人怎么办"：先看清敌我力量对比，再决定撤/守。
    返回 {"ok", "units": [{id, type, x, y, str, moves, threat_str, threat_count, threat_dist}]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local function isHostile(owner)
  if owner == nil or owner < 0 or owner == me then return false end
  local other = Players[owner]
  if other == nil then return false end
  local barbarian = false
  pcall(function() barbarian = other:IsBarbarian() end)
  if barbarian then return true end
  local atWar = false
  pcall(function() atWar = Players[me]:GetDiplomacy():IsAtWarWith(owner) end)
  return atWar
end
local rows = {}
for _, u in Players[me]:GetUnits():Members() do
  local x, y = u:GetX(), u:GetY()
  if x ~= -9999 then
    local info = GameInfo.Units[u:GetType()]
    local ut = info and info.UnitType or "?"
    local str = 0
    if info and info.Combat then str = info.Combat end
    local moves = 0
    pcall(function() moves = u:GetMovesRemaining() or 0 end)
    local tStr, tCount, tDist = -1, 0, 99
    for dx = -2, 2 do for dy = -2, 2 do
      if dx ~= 0 or dy ~= 0 then
        local nx, ny = x + dx, y + dy
        if Map.GetPlot(nx, ny) then
          pcall(function()
            local us = Map.GetUnitsAt(nx, ny)
            if us then
              for o in us:Units() do
                -- 敌我由单位所有者和外交战争状态决定，与地块归属无关；平民不构成战斗威胁。
                local ei = GameInfo.Units[o:GetType()]
                local cs = ei and ei.Combat or 0
                if isHostile(o:GetOwner()) and cs > 0 then
                  tCount = tCount + 1
                  if cs > tStr then
                    tStr = cs
                    tDist = math.max(math.abs(dx), math.abs(dy))
                  end
                end
              end
            end
          end)
        end
      end
    end end
    rows[#rows+1] = string.format("%d|%s|%d,%d|%d|%d|%d|%d|%d",
        u:GetID(), ut, x, y, str, moves, tStr, tCount, tDist)
  end
end
print("THREATS|" .. table.concat(rows, ";;"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("THREATS|"):
            continue
        payload = line[len("THREATS|"):]
        units = []
        for part in payload.split(";;"):
            f = part.split("|")
            if len(f) == 8:
                xy = f[2].split(",")
                units.append({
                    "id": int(f[0]), "type": f[1], "x": int(xy[0]), "y": int(xy[1]),
                    "str": int(f[3]), "moves": int(f[4]),
                    "threat_str": int(f[5]), "threat_count": int(f[6]), "threat_dist": int(f[7]),
                })
        return {"ok": True, "app": app_id, "units": units,
                "detail": f"威胁扫描完成（{len(units)} 个单位，含 {sum(1 for u in units if u['threat_count'] > 0)} 个受威胁单位）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "威胁扫描无回执（Tuner 输出丢失），请重试"}


async def auto_evade_threats(timeout: float = 15.0) -> dict:
    """避险移动：对受威胁单位（附近敌方战力 ≥ 我方 1.2 倍）向远离威胁方向撤退一格。

    回答"碰到比自己强大的敌人怎么办"——托管纪律明确：
    1. 敌强我弱（敌方战力 ≥ 我方 1.2 倍）→ 自动向远离威胁方向撤退（自保，不送死）；
    2. 敌弱我强或势均力敌 → 原地警戒（不主动攻击——战争归玩家，YUI 不引战）；
    3. 城市被威胁 → 在回合摘要里明确报告，建议玩家决策。
    侦察兵/勇士/建造者都适用（建造者遇到蛮族也会撤）。移动用 InGame MOVE_TO（异步生效），
    调用方短等待后再 skip。返回 {"ok", "evaded": ["WARRIOR:9,13->8,14 敌强撤"]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local function isHostile(owner)
  if owner == nil or owner < 0 or owner == me then return false end
  local other = Players[owner]
  if other == nil then return false end
  local barbarian = false
  pcall(function() barbarian = other:IsBarbarian() end)
  if barbarian then return true end
  local atWar = false
  pcall(function() atWar = Players[me]:GetDiplomacy():IsAtWarWith(owner) end)
  return atWar
end
local evaded = {}
-- 先收集威胁信息（同 read_threats），对受威胁且有移动力的单位找远离方向
local danger = {}
for _, u in Players[me]:GetUnits():Members() do
  local x, y = u:GetX(), u:GetY()
  if x ~= -9999 then
    local uinfo = GameInfo.Units[u:GetType()]
    local str = 0
    if uinfo and uinfo.Combat then str = uinfo.Combat end
    local tStr, tCount, tDX, tDY = -1, 0, 0, 0
    for dx = -2, 2 do for dy = -2, 2 do
      if dx ~= 0 or dy ~= 0 then
        if Map.GetPlot(x + dx, y + dy) then
          pcall(function()
            local us = Map.GetUnitsAt(x + dx, y + dy)
            if us then
              for o in us:Units() do
                local ei = GameInfo.Units[o:GetType()]
                local cs = ei and ei.Combat or 0
                if isHostile(o:GetOwner()) and cs > 0 then
                  tCount = tCount + 1
                  if cs > tStr then tStr = cs; tDX = dx; tDY = dy end
                end
              end
            end
          end)
        end
      end
    end end
    if tCount > 0 and tStr >= str * 1.2 then
      danger[u:GetID()] = {x=x, y=y, str=str, tStr=tStr, tDX=tDX, tDY=tDY}
    end
  end
end
-- 对受威胁单位：向远离威胁方向找相邻可行格移动
for _, u in Players[me]:GetUnits():Members() do
  local d = danger[u:GetID()]
  if d and (u:GetMovesRemaining() or 0) > 0 then
    local best, bestScore = nil, -999999
    for dx = -1, 1 do for dy = -1, 1 do
      if dx ~= 0 or dy ~= 0 then
        local nx, ny = d.x + dx, d.y + dy
        local p = Map.GetPlot(nx, ny)
        if p and not p:IsWater() and not p:IsMountain() then
          local occ = false
          local us = Map.GetUnitsAt(nx, ny)
          if us then
            for o in us:Units() do if o:GetOwner() == me then occ = true break end end
          end
          if not occ then
            -- 远离威胁方向分（沿威胁反向）+ 靠近首都分
            local score = -((nx - d.x) * d.tDX + (ny - d.y) * d.tDY)
            if capX >= 0 then
              local towardCap = (d.x - capX) * (d.x - nx) + (d.y - capY) * (d.y - ny)
              score = score + towardCap * 0.5
            end
            if score > bestScore then bestScore = score; best = {nx, ny} end
          end
        end
      end
    end end
    if best then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = best[1]
      params[UnitOperationTypes.PARAM_Y] = best[2]
      pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params) end)
      local info = GameInfo.Units[u:GetType()]
      local ut = info and info.UnitType or "?"
      evaded[#evaded+1] = string.format("%s:%d,%d->%d,%d 敌强(%d)撤",
          ut, d.x, d.y, best[1], best[2], d.tStr)
    end
  end
end
print("EVADED=" .. table.concat(evaded, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("EVADED="):
            continue
        evaded = [t for t in line[len("EVADED="):].split("|") if t]
        return {"ok": True, "app": app_id, "evaded": evaded,
                "detail": f"避险移动：{len(evaded)} 个单位撤退（{'；'.join(evaded) or '无受威胁单位，无需撤退'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "避险移动无回执（Tuner 输出丢失），请重试"}


async def auto_use_builder(timeout: float = 20.0) -> dict:
    """让建造者自动改良地块（InGame BUILD_IMPROVEMENT）——回答"工人会建建筑吗"。

    对每个有移动力的建造者：找其相邻格中"我方领土 + 无已有改良 + 无己方单位 +
    可建改良"的最优格（改良白名单 FARM/MINE/PASTURE/QUARRY/PLANTATION/CAMP/
    LUMBER_MILL，资源格优先 + 食物/生产力按格型加分 + 靠近首都加分），
    RequestOperation BUILD_IMPROVEMENT 原地建造（官方写法：PARAM_X/Y 为所在格，
    与 CanStartOperation 校验一致）。无可用相邻格则不动（不瞎跑）。
    返回 {"ok", "built": ["FARM@10,14"], "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local whitelist = {"IMPROVEMENT_FARM", "IMPROVEMENT_MINE", "IMPROVEMENT_PASTURE",
                   "IMPROVEMENT_QUARRY", "IMPROVEMENT_PLANTATION", "IMPROVEMENT_CAMP",
                   "IMPROVEMENT_LUMBER_MILL"}
local built = {}
local moved = {}
local done = {}
local function pick_improvement(u, nx, ny)
  local p = Map.GetPlot(nx, ny)
  if p == nil then return nil end
  local owner = -1
  pcall(function() owner = p:GetOwner() end)
  if owner ~= me then return nil end
  local hasImp = -1
  pcall(function() hasImp = p:GetImprovementType() end)
  if hasImp >= 0 then return nil end
  local occ = false
  local us = Map.GetUnitsAt(nx, ny)
  if us then
    for o in us:Units() do if o:GetOwner() == me then occ = true break end end
  end
  if occ then return nil end
  local bestImp, impScore = nil, -999999
  for _, impName in ipairs(whitelist) do
    local imp = GameInfo.Improvements[impName]
    if imp then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = nx
      params[UnitOperationTypes.PARAM_Y] = ny
      params[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = imp.Hash
      local ok, can = pcall(function()
        return UnitManager.CanStartOperation(u, UnitOperationTypes.BUILD_IMPROVEMENT, nil, params)
      end)
      if ok and can then
        local sc = 0
        local res = -1
        pcall(function() res = p:GetResourceType() end)
        if res >= 0 then sc = 100 end
        if impName == "IMPROVEMENT_FARM" then sc = sc + 50 end
        if impName == "IMPROVEMENT_MINE" then sc = sc + 40 end
        if sc > impScore then impScore = sc; bestImp = impName end
      end
    end
  end
  if bestImp then
    local sc = impScore
    if capX >= 0 then
      sc = sc - (math.abs(nx - capX) + math.abs(ny - capY))
    end
    return {nx, ny, bestImp, sc}
  end
  return nil
end
for _, u in Players[me]:GetUnits():Members() do
  if not done[u:GetID()] then
    done[u:GetID()] = true
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_BUILDER" and (u:GetMovesRemaining() or 0) > 0 then
      local x, y = u:GetX(), u:GetY()
      if x ~= -9999 then
        -- 情况1：当前格可建 → 原地直接建（BUILD 要求单位在目标格）
        local cur = pick_improvement(u, x, y)
        if cur then
          local params = {}
          params[UnitOperationTypes.PARAM_X] = cur[1]
          params[UnitOperationTypes.PARAM_Y] = cur[2]
          params[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = GameInfo.Improvements[cur[3]].Hash
          pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.BUILD_IMPROVEMENT, params) end)
          built[#built+1] = cur[3] .. "@" .. cur[1] .. "," .. cur[2]
        else
          -- 情况2：相邻最优可建格 → 先移动过去（下回合建）
          local best, bestScore = nil, -999999
          for dx = -1, 1 do for dy = -1, 1 do
            if dx ~= 0 or dy ~= 0 then
              local cand = pick_improvement(u, x + dx, y + dy)
              if cand and cand[4] > bestScore then bestScore = cand[4]; best = cand end
            end
          end end
          if best then
            local params = {}
            params[UnitOperationTypes.PARAM_X] = best[1]
            params[UnitOperationTypes.PARAM_Y] = best[2]
            pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params) end)
            moved[#moved+1] = x .. "," .. y .. "->" .. best[1] .. "," .. best[2] .. "(待建" .. best[3] .. ")"
          end
        end
      end
    end
  end
end
print("BUILT=" .. table.concat(built, "|") .. "|MOVED=" .. table.concat(moved, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("BUILT="):
            continue
        payload = line[len("BUILT="):]
        built_part, _, moved_part = payload.partition("|MOVED=")
        built = [t for t in built_part.split("|") if t]
        moved = [t for t in moved_part.split("|") if t]
        return {"ok": True, "app": app_id, "built": built, "moved": moved,
                "detail": f"建造者工作：建成 {len(built)} 处（{'、'.join(built) or '无'}），"
                          f"移动待建 {len(moved)} 处（{'、'.join(moved) or '无'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "建造者工作无回执（Tuner 输出丢失），请重试"}




async def read_economy(timeout: float = 8.0) -> dict:
    """读经济/宗教/单位构成（InGame）：金币、信仰、总人口、城市数、侦察/勇士/建造者数、首都坐标。

    供 civ6_ops_status 汇报与政策/万神殿局面感知决策。
    """
    lua = """
local me = Game.GetLocalPlayer()
local gold, faith = -1, -1
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
pcall(function() faith = Players[me]:GetFaithBalance() end)
if faith < 0 then pcall(function() faith = Players[me]:GetReligion():GetFaith() end) end
local pop, cities = 0, 0
pcall(function()
  for _, c in Players[me]:GetCities():Members() do cities = cities + 1; pop = pop + c:GetPopulation() end
end)
local scouts, warriors, builders = 0, 0, 0
local districts = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info then
      local ut = info.UnitType
      if ut == "UNIT_SCOUT" then scouts = scouts + 1
      elseif ut == "UNIT_WARRIOR" then warriors = warriors + 1
      elseif ut == "UNIT_BUILDER" then builders = builders + 1 end
    end
  end
end)
pcall(function()
  for _, c in Players[me]:GetCities():Members() do
    for _, d in c:GetDistricts():Members() do
      if not d:IsPillaged() then districts = districts + 1 end
    end
  end
end)
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local pan = -1
pcall(function() pan = Players[me]:GetReligion():GetPantheon() end)
print("ECO|gold=" .. tostring(gold) .. "|faith=" .. tostring(faith) .. "|pop=" .. tostring(pop) ..
      "|cities=" .. tostring(cities) .. "|scouts=" .. tostring(scouts) ..
      "|warriors=" .. tostring(warriors) .. "|builders=" .. tostring(builders) ..
      "|districts=" .. tostring(districts) ..
      "|cap=" .. tostring(capX) .. "," .. tostring(capY) .. "|pantheon=" .. tostring(pan))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("ECO|"):
            continue
        parts = dict(p.split("=", 1) for p in line[len("ECO|"):].split("|") if "=" in p)
        cap = parts.get("cap", "-1,-1").split(",")
        return {"ok": True, "app": app_id, "via": "tuner",
                "gold": round(_to_float(parts.get("gold", "-1")), 1),
                "faith": round(_to_float(parts.get("faith", "-1")), 1),
                "population": _to_int(parts.get("pop", "0")),
                "cities": _to_int(parts.get("cities", "0")),
                "scouts": _to_int(parts.get("scouts", "0")),
                "warriors": _to_int(parts.get("warriors", "0")),
                "builders": _to_int(parts.get("builders", "0")),
                "districts": _to_int(parts.get("districts", "0")),
                "capital": (int(cap[0]) if cap[0].lstrip("-").isdigit() else -1,
                            int(cap[1]) if len(cap) > 1 and cap[1].lstrip("-").isdigit() else -1),
                "pantheon": _to_int(parts.get("pantheon", "-1"))}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "读经济无回执（Tuner 输出丢失），请重试"}




async def read_diplomacy(timeout: float = 10.0) -> dict:
    """读外交局面（InGame）：与每个文明的关系状态 + 城邦（名字/距离/能否派使节/使节数）+ 可用使节。

    供 civ6_ops_status 外交面板与 GIVE_INFLUENCE_TOKEN 自动决策。
    """
    lua = """
local me = Game.GetLocalPlayer()
local rows = {}
-- 我方使节资源
local tokens = 0
pcall(function() tokens = Players[me]:GetInfluence():GetTokensToGive() end)
-- 我方首都（算距离用）
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 我方对各文明的关系状态（对方AI视角）
local rels = {}
for i = 0, 63 do
  if Players[i] and i ~= me and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local civName = cfg and Locale.Lookup(cfg:GetCivilizationShortDescription()) or "?"
    local stateIdx = -1
    pcall(function() stateIdx = Players[i]:GetDiplomaticAI():GetDiplomaticStateIndex(me) end)
    local stName = "?"
    pcall(function()
      local st = GameInfo.DiplomaticStates[stateIdx]
      if st then stName = st.StateType or tostring(stateIdx) end
    end)
    local isMajor, isBarb = true, false
    pcall(function() isMajor = Players[i]:IsMajor() end)
    pcall(function() isBarb = Players[i]:IsBarbarian() end)
    if not isMajor and not isBarb then
      local csx, csy = -1, -1
      pcall(function()
        for _, c in Players[i]:GetCities():Members() do csx, csy = c:GetX(), c:GetY(); break end
      end)
      local dist = -1
      pcall(function() dist = Map.GetPlotsDistance(capX, capY, csx, csy) end)
      local canGive = false
      pcall(function() canGive = Players[me]:GetInfluence():CanGiveTokensToPlayer(i) end)
      local envoys = 0
      pcall(function() envoys = Players[me]:GetInfluence():GetEnvoys(i) end)
      rows[#rows+1] = "CS|" .. civName .. "|dist=" .. tostring(dist) ..
                      "|envoys=" .. tostring(envoys) .. "|can=" .. tostring(canGive)
    else
      rows[#rows+1] = "CIV|" .. civName .. "|" .. stName
    end
  end
end
print("DIPLOMACY|tokens=" .. tostring(tokens) .. "&ROWS=" .. table.concat(rows, ";"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("DIPLOMACY|"):
            continue
        payload = line[len("DIPLOMACY|"):]
        kv = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in payload.split("&") if "=" in p}
        tokens = _to_int(kv.get("tokens", "0"))
        civs, cs = [], []
        for r in kv.get("ROWS", "").split(";"):
            if not r:
                continue
            parts = r.split("|")
            if parts[0] == "CIV":
                civs.append({"name": parts[1], "state": parts[2] if len(parts) > 2 else "?"})
            elif parts[0] == "CS":
                d = {}
                for f in parts[2:]:
                    if "=" in f:
                        k, v = f.split("=", 1)
                        d[k] = v
                cs.append({"name": parts[1], "dist": _to_int(d.get("dist", "-1")),
                           "envoys": _to_int(d.get("envoys", "0")),
                           "can_send": str(d.get("can", "false")).lower() == "true"})
        return {"ok": True, "app": app_id, "tokens": tokens, "civs": civs, "city_states": cs,
                "detail": f"外交：使节 {tokens}；文明关系 {len(civs)} 家；城邦 {len(cs)} 个", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "读外交无回执（Tuner 输出丢失），请重试"}


async def read_culture(timeout: float = 10.0) -> dict:
    """读文化局面（InGame）：每回合文化产出、旅游业绩、巨作数、当前市政。

    供 civ6_ops_status 文化面板——文化胜利要素可视化（读，不自动决策）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local cultYield, tourism, gwCount = -1, -1, 0
pcall(function() cultYield = Players[me]:GetCulture():GetCultureYield() end)
pcall(function() tourism = Players[me]:GetStats():GetTourism() end)
-- 巨作计数：遍历我方城市建筑槽
pcall(function()
  for _, c in Players[me]:GetCities():Members() do
    local bldgs = c:GetBuildings()
    for b in GameInfo.Buildings() do
      local slots = -1
      pcall(function() slots = bldgs:GetNumGreatWorkSlots(b.Index) end)
      if slots > 0 then
        for s = 0, slots - 1 do
          local ok, gw = pcall(function() return bldgs:GetGreatWorkInSlot(b.Index, s) end)
          if ok and gw >= 0 then gwCount = gwCount + 1 end
        end
      end
    end
  end
end)
local civic = -1
pcall(function() civic = Players[me]:GetCulture():GetCurrentCivic() or -1 end)
local civicName = "?"
pcall(function()
  local ci = GameInfo.Civics[civic]
  if ci then civicName = ci.CivicType end
end)
print("CULTURE|yield=" .. tostring(cultYield) .. "|tourism=" .. tostring(tourism) ..
      "|gw=" .. tostring(gwCount) .. "|civic=" .. tostring(civicName))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("CULTURE|"):
            continue
        parts = dict(p.split("=", 1) for p in line[len("CULTURE|"):].split("|") if "=" in p)
        return {"ok": True, "app": app_id,
                "culture_yield": round(_to_float(parts.get("yield", "-1")), 1),
                "tourism": round(_to_float(parts.get("tourism", "-1")), 1),
                "great_works": _to_int(parts.get("gw", "0")),
                "civic": parts.get("civic", "?"),
                "detail": f"文化：产出 {_to_float(parts.get('yield', '-1')):.1f}/回合｜旅游业绩 {parts.get('tourism', '?')}｜巨作 {parts.get('gw', '0')} 件｜市政 {parts.get('civic', '?')}",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "读文化无回执（Tuner 输出丢失），请重试"}




async def close_ui_popups(timeout: float = 10.0) -> dict:
    """只关闭已知的 UI 控件；不批量激活/吞噬通知，不用右键猜测关闭弹窗。

    自然奇观或菜单在官方调用后仍可见时返回失败，由上层交还玩家处理。
    """
    lua = """
local paths = {"/InGame/AdvisorPopup/AdvisorBase", "/InGame/AdvisorPopup",
               "/InGame/TechCivicCompletedPopup", "/InGame/NewTurnPanel",
               "/InGame/EraCompletePopup", "/InGame/GreatPeoplePopup",
               "/InGame/ChooseArtifact", "/InGame/UnitPromotionPopup",
               "/InGame/RazeCity", "/InGame/EspionagePopup", "/InGame/EventPopup",
               "/InGame/BoostUnlockedPopup"}
local closed = 0
for pass = 1, 3 do
  for _, p in ipairs(paths) do
    pcall(function()
      local c = ContextPtr:LookUpControl(p)
      if c then c:SetHide(true); closed = closed + 1 end
    end)
  end
end
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/InGameTopOptionsMenu")
  if c and not c:IsHidden() then
    LuaEvents.InGameTopOptionsMenu_Close()
    UIManager:DequeuePopup(c)
    c:SetHide(true)
    closed = closed + 1
  end
end)
for _, p in ipairs({"/InGame/Options", "/InGame/LoadGameMenu", "/InGame/SaveGameMenu",
                    "/InGame/InGameTopOptionsMenu/Options",
                    "/InGame/InGameTopOptionsMenu/LoadGameMenu",
                    "/InGame/InGameTopOptionsMenu/SaveGameMenu"}) do
  pcall(function()
    local c = ContextPtr:LookUpControl(p)
    if c and not c:IsHidden() then c:SetHide(true); closed = closed + 1 end
  end)
end
pcall(function() LuaEvents.NaturalWonderPopup_Closed() end)
pcall(function() Events.HideLeaderScreen() end)
local nwVisible, menuVisible = false, false
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/NaturalWonderPopup")
  if c then nwVisible = not c:IsHidden() end
end)
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/InGameTopOptionsMenu")
  if c then menuVisible = not c:IsHidden() end
end)
print("CLOSED=" .. tostring(closed) .. "&NW=" .. tostring(nwVisible) .. "&MENU=" .. tostring(menuVisible))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        if not line.strip().startswith("CLOSED="):
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.strip().split("&") if "=" in p}
        closed = _to_int(kv.get("CLOSED", "0"))
        nw = str(kv.get("NW", "false")).lower() == "true"
        menu = str(kv.get("MENU", "false")).lower() == "true"
        result = {"app": app_id, "closed": closed, "dismissed": 0,
                  "nw_closed": not nw, "menu_closed": not menu, "via": "tuner"}
        if nw or menu:
            visible = "、".join(name for name, shown in (("自然奇观", nw), ("暂停菜单", menu)) if shown)
            return {**result, "ok": False, "error": "POPUP_UNRESOLVED",
                    "detail": f"{visible}仍可见；未右键或吞噬通知，请手动关闭"}
        return {**result, "ok": True, "detail": f"已处理 {closed} 个已知弹窗控件；未清理普通通知"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "关闭弹窗无回执（Tuner 输出丢失），请重试"}


async def auto_resolve_soft_blockers(timeout: float = 10.0) -> dict:
    """自动解决可安全消除的结束回合 blocker（InGame 官方 API）。

    处理：GOVERNOR_IDLE（总督空闲：SendActivated+Dismiss）、
    CONSIDER_GOVERNMENT_CHANGE（考虑更换政体：置 considered=true）、
    WORLD_CONGRESS_LOOK / WORLD_CONGRESS_SPECIAL_SESSION（世界议会查看/特别会议：dismiss）。
    返回 {"ok", "resolved": [类型列表]}。
    生产/科技/市政/单位/议会投票/间谍逃跑路线等策略性 blocker 不在此处理，交玩家决策。
    """
    lua = """
local me = Game.GetLocalPlayer()
local resolved = {}
local mark = function(name)
  local dup = false
  for _, v in ipairs(resolved) do if v == name then dup = true break end end
  if not dup then resolved[#resolved+1] = name end
end
-- 遍历通知，仅处理明确支持的软 blocker；宣战与间谍等普通/策略通知保持原样。
pcall(function()
  local list = NotificationManager.GetList(me)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(me, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt then
          if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_GOVERNOR_IDLE then
            pcall(function() NotificationManager.SendActivated(me, nid) end)
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("GOVERNOR_IDLE")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("WORLD_CONGRESS_LOOK")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("WORLD_CONGRESS_SPECIAL_SESSION")
          end
        end
      end
    end
  end
end)
-- 考虑更换政体（不依赖通知）
pcall(function()
  local p = Players[me]
  if p and p:GetCulture() and p:GetCulture():GetGovernmentChangeConsidered() == false then
    p:GetCulture():SetGovernmentChangeConsidered(true)
    mark("GOVERNMENT_CHANGE")
  end
end)
-- 世界议会"已查看"标记
pcall(function()
  UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {})
end)
print("RESOLVED=" .. table.concat(resolved, ","))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=_ingame_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESOLVED="):
            continue
        names = [n for n in line[len("RESOLVED="):].split(",") if n]
        return {"ok": True, "app": app_id, "resolved": names,
                "detail": "已自动解决：" + ("、".join(names) if names else "无（无需处理）"),
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "软 blocker 处理无回执（Tuner 输出丢失），请重试"}


# 自动决策/写操作已拆到 civ6_tuner_auto.py；这里重新导出，保持原有模块路径兼容。
try:
    from .civ6_tuner_auto import *  # noqa: E402,F403
except ImportError:  # 包外直接加载（测试/独立脚本）
    from civ6_tuner_auto import *  # type: ignore  # noqa: E402,F403
