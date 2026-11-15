"""civ6_tuner_auto.py — 文明6 自动决策与写操作（从 civ6_tuner 拆出）。

本模块只负责“按真实局面选择并执行不需要玩家战略决策的自动操作”：
生产配额、世界议会处理器、经济/宗教/伟人/商队/间谍/战争响应、万神殿、
使节、单位升级、总督、纪念与政策槽。

底层连接、读局面、写生产/研究、结束回合和协议解析仍在 civ6_tuner.py；
本模块通过 `_t` 引用原门面，测试仍可 monkeypatch `civ6_tuner.execute_lua`
等公共入口，不会形成第二套连接逻辑。
"""

from __future__ import annotations

import asyncio

try:
    from . import civ6_tuner as _t
except ImportError:  # 包外直接加载（测试/独立脚本）
    import civ6_tuner as _t  # type: ignore


__all__ = [
    "auto_decide_production",
    "register_wc_voter",
    "unregister_wc_voter",
    "auto_manage_money",
    "auto_found_religion",
    "auto_recruit_great_person",
    "auto_trade_route",
    "auto_manage_spy",
    "auto_war_response",
    "auto_choose_pantheon",
    "auto_send_envoy",
    "auto_promote_units",
    "auto_appoint_governor",
    "auto_choose_commemoration",
    "auto_fill_policy_slots",
]


async def auto_decide_production(timeout: float = 10.0) -> dict:
    """按真实单位构成选择无需选址的有限配额生产；配额随城市数扩展。

    不自动选择需要城市与地块坐标的区域。早期配额不是"每类各 2 个就停"：
    侦察兵、建造者、勇士的目标随城市数增长（有上限），让连续 20~50 回合运营
    不会在几座城市出完第一批单位后就立刻把生产 blocker 交还。
    返回 {"ok", "kind", "item", "reason", "eco"} 或 NO_REASONABLE_PRODUCTION。
    """
    eco = await _t.read_economy(timeout=timeout)
    if not eco.get("ok"):
        return {"ok": False, "error": eco.get("error", "ECO_FAIL"),
                "detail": eco.get("detail", "读经济失败")}
    pop = int(eco.get("population") or 0)
    cities = max(1, int(eco.get("cities") or 0))
    scouts = int(eco.get("scouts") or 0)
    builders = int(eco.get("builders") or 0)
    warriors = int(eco.get("warriors") or 0)
    # 目标随城市数扩展并设上限：2~4 侦察、2~6 建造者、1~4 勇士。
    scout_target = min(4, max(2, cities + 1))
    builder_target = min(6, max(2, cities * 2))
    warrior_target = min(4, max(1, cities))
    if scouts < scout_target:
        return {"ok": True, "kind": "unit", "item": "UNIT_SCOUT",
                "reason": f"侦察兵{scouts}/{scout_target} → 补侦察探路", "eco": eco}
    if builders < builder_target:
        return {"ok": True, "kind": "unit", "item": "UNIT_BUILDER",
                "reason": f"建造者{builders}/{builder_target} → 补工人改良地块", "eco": eco}
    if pop >= 2 and warriors < warrior_target:
        return {"ok": True, "kind": "unit", "item": "UNIT_WARRIOR",
                "reason": f"勇士{warriors}/{warrior_target} → 补城防", "eco": eco}
    return {"ok": False, "error": "NO_REASONABLE_PRODUCTION", "eco": eco,
            "detail": "侦察/建造者/勇士已按城市规模补足；区域与建筑涉及选址/路线决策，交还玩家选择"}


async def register_wc_voter(timeout: float = 10.0) -> dict:
    """注册世界议会自动投票事件处理器（InGame，Events.WorldCongressStage1）。

    官方机制：世界议会进入投票阶段时（回合推进中触发 WorldCongressStage1 事件），
    处理器自动为当前决议投票并提交（UI.RequestPlayerOperation），不再卡回合。
    投票策略（保守可靠）：全部外交支持票投给决议 A 选项、目标 0（中立偏好）；
    若已有处理器则不重复注册。幂等：无议会/已注册时安全返回。
    返回 {"ok", "registered", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
if __civ6_wc_handler then
  print("WC_ALREADY|registered")
  return
end
local function handler()
  local me = Game.GetLocalPlayer()
  local wc = Game.GetWorldCongress()
  if not wc or not wc:IsInSession() then return end
  local favor = Players[me]:GetFavor()
  local costs = wc:GetVotesandFavorCost()
  local maxV = costs.MaxVotes or 5
  local ress = wc:GetResolutions()
  if not ress or #ress == 0 then
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
    __civ6_wc_voted = true
    return
  end
  for _, res in ipairs(ress) do
    local kParams = {}
    kParams[PlayerOperations.PARAM_RESOLUTION_TYPE] = res.Type
    kParams[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = maxV
    kParams[PlayerOperations.PARAM_RESOLUTION_OPTION] = 1
    kParams[PlayerOperations.PARAM_RESOLUTION_SELECTION] = 0
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, kParams)
  end
  UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
  __civ6_wc_voted = true
end
Events.WorldCongressStage1.Add(handler)
__civ6_wc_handler = handler
print("WC_REGISTERED|ok")
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("WC_ALREADY|"):
            return {"ok": True, "app": app_id, "registered": False, "via": "tuner",
                    "detail": "世界议会投票处理器已注册（不重复）"}
        if line.startswith("WC_REGISTERED|"):
            return {"ok": True, "app": app_id, "registered": True, "via": "tuner",
                    "detail": "世界议会自动投票处理器已注册：会议阶段自动投 A 项全部票并提交"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "世界议会注册无回执（Tuner 输出丢失），请重试"}


async def unregister_wc_voter(timeout: float = 3.0) -> dict:
    """注销本插件注册的世界议会处理器，避免交还后继续替玩家投票。"""
    lua = """
if __civ6_wc_handler then
  local ok, err = pcall(function() Events.WorldCongressStage1.Remove(__civ6_wc_handler) end)
  if not ok then print("WC_UNREGISTER_ERR|" .. tostring(err)) return end
  __civ6_wc_handler = nil
  __civ6_wc_voted = nil
  print("WC_UNREGISTERED|ok")
else
  print("WC_UNREGISTERED|absent")
end
"""
    try:
        # 交还瞬间另一路连接可能仍占着唯一 Tuner 端口；注销允许一次短重试，
        # 但仍只执行一次 Lua 注销，避免重复 Remove。
        app_id, states, reader, writer = await _t._handshake_retry(retries=1)
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(
            reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout,
        )
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("WC_UNREGISTERED|"):
            return {"ok": True, "app": app_id, "registered": False, "via": "tuner",
                    "detail": "世界议会自动投票处理器已注销" if line.endswith("|ok") else "世界议会处理器本未注册"}
        if line.startswith("WC_UNREGISTER_ERR|"):
            return {"ok": False, "error": "WC_UNREGISTER_FAILED", "detail": line}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "世界议会注销无回执，不能确认已清理"}


async def auto_manage_money(timeout: float = 15.0) -> dict:
    """自动花钱：建造者少于 2 且金币足够时，尝试购买一个建造者。

    当前不会购买地块；购买请求是否实际成交以游戏反馈为准，不夸大为完整经济自动运营。
    返回 {"ok", "action", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local gold = -1
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
local builders = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_BUILDER" then builders = builders + 1 end
  end
end)
local bought = ""
-- 1) 买建造者
if builders < 2 and gold >= 150 then
  local item = GameInfo.Units["UNIT_BUILDER"]
  if item then
    local pCity = CityManager.GetCity(me, 0)
    if pCity then
      local tParams = {}
      tParams[CityOperationTypes.PARAM_UNIT_TYPE] = item.Hash
      tParams[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
      local ok, canBuy = pcall(function()
        return CityManager.CanStartOperation(pCity, CityOperationTypes.PURCHASE, tParams, true)
      end)
      if ok and canBuy then
        pcall(function() CityManager.RequestOperation(pCity, CityOperationTypes.PURCHASE, tParams) end)
        bought = "BOUGHT_BUILDER"
      else
        bought = "CANNOT_BUY_BUILDER"
      end
    end
  end
end
print("MONEY|gold=" .. tostring(gold) .. "|builders=" .. tostring(builders) .. "|" .. bought)
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("MONEY|"):
            continue
        payload = line[len("MONEY|"):]
        parts = dict(p.split("=", 1) for p in payload.split("|") if "=" in p)
        gold = round(_t._to_float(parts.get("gold", "0")), 1)
        action = ""
        if "BOUGHT_BUILDER" in payload:
            action = "已购买建造者"
        elif "CANNOT_BUY_BUILDER" in payload:
            action = "建造者可买但购买未生效（金币/价格不匹配），下次再试"
        return {"ok": True, "app": app_id, "gold": gold,
                "action": action,
                "detail": f"经济自动：金币 {gold}，建造者 {parts.get('builders', '?')}。{action or '金币不足或无购买项，本回合不花钱'}",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "经济自动无回执（Tuner 输出丢失），请重试"}


async def auto_found_religion(timeout: float = 15.0) -> dict:
    """自动创立宗教（InGame）——大预言家可用/已激活时自动创教并选信条。

    官方链路（religion.py 同款）：FOUND_RELIGION（PARAM_RELIGION_TYPE）→
    ADD_BELIEF（FOLLOWER）→ ADD_BELIEF（FOUNDER）。
    信条策略（保守可靠）：FOLLOWER 优先"信仰+金钱"类（如 RELIGIOUS_SETTLEMENTS 圣地+1人口），
    FOUNDER 优先"金钱/科技"类（如 CHURCH_PROPERTY 每城市+2金）；不可用时选第一个可用。
    幂等：已有宗教/无大预言家时安全返回。返回 {"ok", "founded", "religion", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pRel = Players[me]:GetReligion()
local created = -1
pcall(function() created = pRel:GetReligionTypeCreated() end)
if created >= 0 then print("RELIGION_NONE|already_founded") return end
-- 找第一个可用宗教类型（本文明默认宗教）
local relType = nil
for row in GameInfo.Religions() do
  if row.Type ~= "RELIGION_PANTHEON" then relType = row.Type; break end
end
if not relType then print("RELIGION_NONE|no_religion_available") return end
local relRow = GameInfo.Religions[relType]
local taken_belief = {}
-- 选信条：FOLLOWER 优先 ReligiousSettlements，FOUNDER 优先 ChurchProperty；否则第一个可用
local function pickBelief(cls, pref1)
  local first = nil
  for row in GameInfo.Beliefs() do
    if row.BeliefClassType == cls and not taken_belief[row.Index] then
      if first == nil then first = row.Type end
      if pref1 and row.Type == pref1 then return row.Type end
    end
  end
  return first
end
local fb = pickBelief("BELIEF_CLASS_FOLLOWER", "BELIEF_RELIGIOUS_SETTLEMENTS")
local fdb = pickBelief("BELIEF_CLASS_FOUNDER", "BELIEF_CHURCH_PROPERTY")
local params = {}
params[PlayerOperations.PARAM_RELIGION_TYPE] = relRow.Hash
UI.RequestPlayerOperation(me, PlayerOperations.FOUND_RELIGION, params)
if fb then
  local b1 = GameInfo.Beliefs[fb]
  local p2 = {}
  p2[PlayerOperations.PARAM_BELIEF_TYPE] = b1.Hash
  UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p2)
end
if fdb then
  local b2 = GameInfo.Beliefs[fdb]
  local p3 = {}
  p3[PlayerOperations.PARAM_BELIEF_TYPE] = b2.Hash
  UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p3)
end
print("RELIGION_OK|" .. relType .. "|" .. tostring(fb) .. "|" .. tostring(fdb))
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("RELIGION_NONE|"):
            return {"ok": True, "app": app_id, "founded": False, "via": "tuner",
                    "detail": "宗教机制未到（已有宗教或无可用宗教），自动跳过"}
        if line.startswith("RELIGION_OK|"):
            parts = line[len("RELIGION_OK|"):].split("|")
            return {"ok": True, "app": app_id, "founded": True, "religion": parts[0],
                    "detail": f"自动创立宗教 {parts[0]}（信条 {parts[1] or '默认'} / {parts[2] or '默认'}）",
                    "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "创教无回执（Tuner 输出丢失），请重试"}


async def auto_recruit_great_person(timeout: float = 15.0) -> dict:
    """自动招募可用的伟人（InGame）——可招募伟人列表非空时自动招募第一个。

    官方链路（great_people.py 同款）：RECRUIT_GREAT_PERSON（PARAM_GREAT_PERSON_INDIVIDUAL_TYPE）。
    幂等：无可用伟人时安全返回。返回 {"ok", "recruited", "name", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local recruited = ""
pcall(function()
  local gpMgr = Game.GetGreatPeople()
  if not gpMgr then return end
  local list = gpMgr:GetUnclaimedGreatPeople() or {}
  for i = 1, #list do
    local person = list[i]
    local pts = Players[me]:GetGreatPeoplePoints(person:GetGreatPersonClass():GetIndex())
    local cost = person:GetCost()
    if cost >= 0 and pts >= cost then
      local kParams = {}
      kParams[PlayerOperations.PARAM_GREAT_PERSON_INDIVIDUAL_TYPE] = person:GetIndex()
      UI.RequestPlayerOperation(me, PlayerOperations.RECRUIT_GREAT_PERSON, kParams)
      recruited = person:GetName() or ("GP#" .. tostring(person:GetIndex()))
      break
    end
  end
end)
print("GP|" .. recruited)
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("GP|"):
            continue
        name = line[len("GP|"):]
        return {"ok": True, "app": app_id, "recruited": bool(name),
                "name": name or None,
                "detail": f"伟人自动招募：{name or '无可用伟人，跳过'}", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "伟人招募无回执（Tuner 输出丢失），请重试"}


async def auto_trade_route(timeout: float = 15.0) -> dict:
    """自动开贸易路线（InGame）——闲置商队自动建立到最近城市/城邦的路线。

    官方链路（economy.py 同款）：UNITOPERATION_MAKE_TRADE_ROUTE 单位操作。
    策略：对每个闲置商队（有移动力、未在路线中），选择最近可达目标（优先城邦，
    其次外国城市，最后本国城市）发 MAKE_TRADE_ROUTE。幂等：无商队/路线满时安全返回。
    返回 {"ok", "routes": [], "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local opInfo = GameInfo.UnitOperations["UNITOPERATION_MAKE_TRADE_ROUTE"]
local created = {}
if opInfo then
  local opHash = opInfo.Hash
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_TRADER" and (u:GetMovesRemaining() or 0) > 0 then
      local ux, uy = u:GetX(), u:GetY()
      -- 最近可达城市：遍历地图找城市（简化：3 圈内非本国城市）
      local best, bestDist = nil, 99
      for dx = -6, 6 do for dy = -6, 6 do
        if dx ~= 0 or dy ~= 0 then
          local p = Map.GetPlot(ux + dx, uy + dy)
          if p then
            local owner = -1
            pcall(function() owner = p:GetOwner() end)
            if owner >= 0 and owner ~= me then
              local c = CityManager.GetCityAt(ux + dx, uy + dy)
              if c then
                local d = math.abs(dx) + math.abs(dy)
                if d < bestDist then bestDist = d; best = {ux + dx, uy + dy, owner} end
              end
            end
          end
        end
      end end
      if best then
        local params = {}
        params[UnitOperationTypes.PARAM_X] = best[1]
        params[UnitOperationTypes.PARAM_Y] = best[2]
        pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MAKE_TRADE_ROUTE, params) end)
        created[#created+1] = "trader@" .. ux .. "," .. uy .. "->" .. best[1] .. "," .. best[2]
      end
    end
  end
end
print("TRADE=" .. table.concat(created, "|"))
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("TRADE="):
            continue
        created = [t for t in line[len("TRADE="):].split("|") if t]
        return {"ok": True, "app": app_id, "routes": created,
                "detail": f"贸易路线：{len(created)} 条商队路线建立（{'、'.join(created) or '无商队/无可达目标，跳过'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "商队无回执（Tuner 输出丢失），请重试"}


async def auto_manage_spy(timeout: float = 15.0) -> dict:
    """只扫描并报告间谍单位；不派遣任务、不选择逃跑路线、不删除间谍通知。"""
    lua = """
local me = Game.GetLocalPlayer()
local found = false
for _, u in Players[me]:GetUnits():Members() do
  local info = GameInfo.Units[u:GetType()]
  if info and info.UnitType == "UNIT_SPY" then
    found = true
    print("SPY_FOUND|" .. u:GetID())
  end
end
print("SPY_SCAN|" .. tostring(found))
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    if not any(line.strip().startswith("SPY_SCAN|") for line in out):
        return {"ok": False, "error": "NO_RESPONSE", "detail": "间谍扫描无回执，不能确认是否存在间谍"}
    has_spy = any(line.strip().startswith("SPY_FOUND|") for line in out)
    return {"ok": True, "app": app_id, "has_spy": has_spy,
            "detail": f"间谍扫描：{'检测到间谍；仅报告，任务与逃跑路线由玩家决定' if has_spy else '未检测到间谍'}",
            "via": "tuner"}


async def auto_war_response(timeout: float = 15.0) -> dict:
    """战争响应（InGame）——被宣战时评估并报告，不主动宣战。

    边界说明（重要）：主动宣战会永久破坏外交关系、可能引发全面战争，属不可逆决策。
    本函数只读战争与威胁状态并报告，不声称已经替玩家完成防御或移动；由上层立即交还。
    返回 {"ok", "at_war", "threats", "advice", "detail"}。
    """
    war = await _t.at_war(timeout=timeout)
    thr = await _t.read_threats(timeout=timeout)
    if war.get("ok") and war.get("at_war"):
        adv = "检测到战争：停止运营并交还玩家，进攻、防御与议和均由玩家决定。"
        detail = f"检测到战争！{adv}"
        return {"ok": True, "at_war": True,
                "threats": thr.get("units", []) if thr.get("ok") else [],
                "advice": adv, "detail": detail, "via": "tuner"}
    return {"ok": True, "at_war": False,
            "threats": thr.get("units", []) if thr.get("ok") else [],
            "advice": "和平状态，托管继续运营。",
            "detail": f"战争检查：未在战争。{thr.get('detail', '')}", "via": "tuner"}

async def auto_choose_pantheon(timeout: float = 12.0) -> dict:
    """自动选择万神殿（InGame，FOUND_PANTHEON）——局面感知：扫描首都 3 圈地形匹配万神殿。

    ENDTURN_BLOCKING_PANTHEON（"选择万神殿信仰"）出现时调用。匹配规则：
    沙漠→DESERT_FOLKLORE、雨林→SACRED_PATH、冻土→DANCE_OF_THE_AURORA、
    河边→RIVER_GODDESS、海岸→GOD_OF_THE_SEA、默认→DIVINE_SPARK（大预言家点）。
    返回 {"ok", "chosen", "reason"}。已有万神殿或不可选时返回 ok=False + 明确原因。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pReligion = Players[me]:GetReligion()
local pan = -1
pcall(function() pan = pReligion:GetPantheon() end)
if pan >= 0 then print("RESULT|ALREADY|已有万神殿"); return end
-- 首都位置
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 扫描首都 3 圈地形计数
local terrain = {desert=0, forest=0, tundra=0, river=0, coast=0, marsh=0}
pcall(function()
  for dx = -3, 3 do for dy = -3, 3 do
    if dx*dx + dy*dy <= 9 then
      local p = Map.GetPlot(capX + dx, capY + dy)
      if p then
        local t = GameInfo.Terrains[p:GetTerrainType()]
        local tt = t and t.TerrainType or ""
        if tt:find("DESERT") then terrain.desert = terrain.desert + 1 end
        if tt:find("TUNDRA") then terrain.tundra = terrain.tundra + 1 end
        if tt:find("SNOW") then terrain.tundra = terrain.tundra + 1 end
        if tt:find("COAST") or tt:find("OCEAN") then terrain.coast = terrain.coast + 1 end
        if tt:find("MARSH") or tt:find("FLOODPLAINS") then terrain.marsh = terrain.marsh + 1 end
        local feats = p:GetFeatureType()
        local feat = feats and GameInfo.Features[feats]
        local ft = feat and feat.FeatureType or ""
        if ft:find("FOREST") or ft:find("RAINFOREST") then terrain.forest = terrain.forest + 1 end
        if p:IsRiver() then terrain.river = terrain.river + 1 end
      end
    end
  end
end)
-- 计算可选万神殿（未被其他玩家取走）
local taken = {}
pcall(function()
  for i = 0, 63 do
    if Players[i] and Players[i]:IsAlive() then
      local pr = Players[i]:GetReligion()
      if pr and pr:GetPantheon() >= 0 then taken[pr:GetPantheon()] = true end
    end
  end
end)
-- 局面匹配优先级
local pick, reason = nil, ""
local order = {}
if terrain.desert >= 4 then table.insert(order, {"BELIEF_DESERT_FOLKLORE", "首都周边沙漠多，选沙漠传说（沙漠格+信仰）"}) end
if terrain.forest >= 6 then table.insert(order, {"BELIEF_SACRED_PATH", "首都周边森林多，选神圣之路（森林格+文化）"}) end
if terrain.tundra >= 4 then table.insert(order, {"BELIEF_DANCE_OF_THE_AURORA", "首都周边冻土多，选极光之舞（冻土格+信仰）"}) end
if terrain.river >= 3 then table.insert(order, {"BELIEF_RIVER_GODDESS", "首都临河，选河神（河岸格+住房与信仰）"}) end
if terrain.coast >= 4 then table.insert(order, {"BELIEF_GOD_OF_THE_SEA", "首都沿海，选海神（海岸格+产能）"}) end
table.insert(order, {"BELIEF_DIVINE_SPARK", "无强地形匹配，选神启（+25% 大预言家点，冲宗教通用）"})
for _, cand in ipairs(order) do
  local b = GameInfo.Beliefs[cand[1]]
  if b and not taken[b.Index] then pick = cand[1]; reason = cand[2]; break end
end
if pick == nil then print("RESULT|NONE|没有可用万神殿信仰"); return end
local params = {}
params[PlayerOperations.PARAM_BELIEF_TYPE] = GameInfo.Beliefs[pick].Hash
UI.RequestPlayerOperation(me, PlayerOperations.FOUND_PANTHEON, params)
print("RESULT|" .. pick .. "|" .. reason)
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "ALREADY":
            return {"ok": False, "error": "ALREADY_HAS_PANTHEON", "detail": "已拥有万神殿，无需再选"}
        if tag == "NONE":
            return {"ok": False, "error": "NO_AVAILABLE", "detail": "没有可用万神殿信仰（可能全部被其他文明取走）"}
        reason = parts[1] if len(parts) > 1 else ""
        return {"ok": True, "app": app_id, "chosen": tag, "reason": reason,
                "detail": f"已创立万神殿：{tag}（{reason}）", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "选万神殿无回执（Tuner 输出丢失），请重试"}

async def auto_send_envoy(timeout: float = 12.0) -> dict:
    """自动派遣使节（ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN）——局面感知：派给离首都最近的城邦。

    使节是早期外交硬资源：对每个"可派"城邦（CanGiveTokensToPlayer），按距离近者优先派发，
    宗主国加成+视野收益明确，不伤害任何文明关系（城邦外交独立）。已满/无可派时明确返回。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pInfluence = Players[me]:GetInfluence()
if pInfluence:GetTokensToGive() <= 0 then print("RESULT|NO_TOKENS|没有可用使节"); return end
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 收集可派城邦（按距离升序）
local cands = {}
for i = 0, 63 do
  if Players[i] and i ~= me and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local isMajor, isBarb = true, false
    pcall(function() isMajor = Players[i]:IsMajor() end)
    pcall(function() isBarb = Players[i]:IsBarbarian() end)
    if not isMajor and not isBarb then
      local can = false
      pcall(function() can = pInfluence:CanGiveTokensToPlayer(i) end)
      if can then
        local csx, csy = -1, -1
        pcall(function()
          for _, c in Players[i]:GetCities():Members() do csx, csy = c:GetX(), c:GetY(); break end
        end)
        local dist = -1
        pcall(function() dist = Map.GetPlotsDistance(capX, capY, csx, csy) end)
        local name = cfg and Locale.Lookup(cfg:GetPlayerName()) or "?"
        cands[#cands+1] = {id=i, dist=dist, name=name}
      end
    end
  end
end
table.sort(cands, function(a, b) return a.dist < b.dist end)
if #cands == 0 then print("RESULT|NONE|没有可派的城邦（未发现或都不可派）"); return end
local target = cands[1]
local params = {}
params[PlayerOperations.PARAM_PLAYER_ONE] = target.id
params[PlayerOperations.PARAM_FLAGS] = 0
UI.RequestPlayerOperation(me, PlayerOperations.GIVE_INFLUENCE_TOKEN, params)
print("RESULT|" .. target.name .. "|dist=" .. tostring(target.dist))
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NO_TOKENS":
            return {"ok": False, "error": "NO_TOKENS", "detail": "没有可用使节（可能刚用完）"}
        if tag == "NONE":
            return {"ok": False, "error": "NO_CANDIDATE", "detail": "没有可派的城邦（未发现城邦或都不可派）"}
        detail = parts[1] if len(parts) > 1 else ""
        return {"ok": True, "app": app_id, "sent_to": tag, "reason": detail,
                "detail": f"已向 {tag} 派遣使节（{detail}——离首都最近的城邦，宗主加成收益明确）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "派使节无回执（Tuner 输出丢失），请重试"}


async def auto_promote_units(timeout: float = 15.0) -> dict:
    """自动升级有可用升级点的单位（GameCore SetPromotion——InGame RequestCommand(PROMOTE) 静默失败已证）。

    用于 ENDTURN_BLOCKING_UNIT_PROMOTION（"单位可以升级"）。升级是纯强化、不引战、
    不改变战略方向——自动选择第一个可用的同职业升级（按游戏数据顺序），
    SetPromotion 后清零 stored promotions（否则引擎持续报 blocker）。
    返回 {"ok", "promoted": ["SCOUT→ALERTNESS", ...]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local prereqMap = {}
for row in GameInfo.UnitPromotionPrereqs() do
  local pt = row.UnitPromotion
  if not prereqMap[pt] then prereqMap[pt] = {} end
  table.insert(prereqMap[pt], row.PrereqUnitPromotion)
end
local results = {}
for _, unit in Players[me]:GetUnits():Members() do
  local exp = unit:GetExperience()
  if exp then
    local stored = 0
    pcall(function() stored = exp:GetStoredPromotions() end)
    if stored > 0 then
      local ui = GameInfo.Units[unit:GetType()]
      local promClass = ui and ui.PromotionClass or ""
      local chosen = nil
      for promo in GameInfo.UnitPromotions() do
        if promo.PromotionClass == promClass and not exp:HasPromotion(promo.Index) then
          local prereqs = prereqMap[promo.UnitPromotionType]
          local okPrereq = true
          if prereqs and #prereqs > 0 then
            okPrereq = false
            for _, reqType in ipairs(prereqs) do
              local reqInfo = GameInfo.UnitPromotions[reqType]
              if reqInfo and exp:HasPromotion(reqInfo.Index) then okPrereq = true break end
            end
          end
          if okPrereq then
            local can = false
            pcall(function() can = exp:CanPromote(promo.Index) end)
            if can then chosen = promo break end
          end
        end
      end
      if chosen then
        exp:SetPromotion(chosen.Index)
        local storedNow = 0
        pcall(function() storedNow = exp:GetStoredPromotions() end)
        if storedNow > 0 then pcall(function() exp:ChangeStoredPromotions(-storedNow) end) end
        local okApply = exp:HasPromotion(chosen.Index)
        local ut = ui and ui.UnitType or "?"
        results[#results+1] = ut .. "→" .. chosen.UnitPromotionType .. "|ok=" .. tostring(okApply)
      else
        local ut = ui and ui.UnitType or "?"
        results[#results+1] = ut .. "→NONE"
      end
    end
  end
end
print("RESULT|" .. table.concat(results, ";;"))
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua,
                                state_index=_t._gamecore_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        payload = line[len("RESULT|"):]
        if not payload.strip():
            return {"ok": True, "app": app_id, "promoted": [],
                    "detail": "没有需要升级的单位（无可用升级点）", "via": "tuner"}
        items = [p for p in payload.split(";;") if p.strip()]
        promoted = []
        for it in items:
            tag, _, ok = it.partition("|")
            promoted.append(tag)
        return {"ok": True, "app": app_id, "promoted": promoted,
                "detail": f"已升级 {len(promoted)} 个单位：" + "、".join(promoted) + "（自动选第一个可用升级，纯强化不引战）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "单位升级无回执（Tuner 输出丢失），请重试"}


async def auto_appoint_governor(timeout: float = 12.0) -> dict:
    """自动任命并分配总督（InGame APPOINT_GOVERNOR + ASSIGN_GOVERNOR）。

    用于 ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT（"可以任命一位总督"）。
    选择第一个尚未任命的总督类型，任命后自动分配到首都（收益确定性最高）。
    总督是对内治理（产能/科技/忠诚加成），不改变玩家战略方向，自动处理安全。
    返回 {"ok", "governor", "city"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pGovs = nil
pcall(function() pGovs = Players[me]:GetGovernors() end)
if not pGovs then print("RESULT|NO_POINT|当前版本不支持总督系统（无 Rise&Fall/GS）"); return end
local canAppoint = false
pcall(function() canAppoint = pGovs:CanAppoint() end)
if not canAppoint then print("RESULT|NO_POINT|没有可用总督任命点数"); return end
local appointed = {}
pcall(function()
  local list = pGovs:GetGovernorsAppointed()
  if list then
    for _, idx in ipairs(list) do appointed[idx] = true end
  end
end)
local chosen = nil
for gov in GameInfo.Governors() do
  if not appointed[gov.Index] then chosen = gov break end
end
if not chosen then print("RESULT|ALL_APPOINTED|所有总督都已任命"); return end
local params = {}
params[PlayerOperations.PARAM_GOVERNOR_TYPE] = chosen.Index
UI.RequestPlayerOperation(me, PlayerOperations.APPOINT_GOVERNOR, params)
__civ6_gov_expected = chosen.Index
local cityName = "?"
pcall(function()
  local city = Players[me]:GetCities():GetCapitalCity()
  if city then
    cityName = Locale.Lookup(city:GetName())
    local params2 = {}
    params2[PlayerOperations.PARAM_GOVERNOR_TYPE] = chosen.Index
    params2[PlayerOperations.PARAM_CITY_DEST] = city:GetID()
    params2[PlayerOperations.PARAM_PLAYER_ONE] = me
    UI.RequestPlayerOperation(me, PlayerOperations.ASSIGN_GOVERNOR, params2)
  end
end)
print("RESULT|" .. chosen.GovernorType .. "|" .. (Locale.Lookup(chosen.Name) or "?") .. "|" .. cityName)
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
        result_line = next((line.strip() for line in out if line.strip().startswith("RESULT|")), "")
        if not result_line:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "任命总督无回执（Tuner 输出丢失），请重试"}
        parts = result_line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NO_POINT":
            return {"ok": False, "error": "NO_POINT", "detail": "没有可用总督任命点数（可能刚用过）"}
        if tag == "ALL_APPOINTED":
            return {"ok": False, "error": "ALL_APPOINTED", "detail": "所有总督都已任命"}
        detail = parts[1] if len(parts) > 1 else ""
        verify_lua = """
local me = Game.GetLocalPlayer()
local pGovs = nil
pcall(function() pGovs = Players[me]:GetGovernors() end)
local expected = __civ6_gov_expected or -1
local found = false
if pGovs then
  pcall(function()
    local list = pGovs:GetGovernorsAppointed()
    if list then
      for _, idx in ipairs(list) do
        if idx == expected then found = true end
      end
    end
  end)
end
if found then __civ6_gov_expected = nil end
print("GOV|found=" .. tostring(found))
"""
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.4, remaining))
            verified = await _t.execute_lua(
                reader, writer, verify_lua, state_index=_t._ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("GOV|")), "")
            if "found=true" in line:
                return {"ok": True, "app": app_id, "governor": tag, "verified": True,
                        "detail": f"已读回验证总督任命：{detail}", "via": "tuner"}
        return {"ok": False, "error": "SILENT_FAILURE",
                "detail": "任命总督后未读回任命列表，未宣称成功"}
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def auto_choose_commemoration(timeout: float = 12.0) -> dict:
    """自动选择时代纪念（黄金时代致敬）（InGame COMMEMORATE）。

    用于 ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE（"可以选择一个纪念"）。
    选择第一个可用的纪念（GetPlayerCommemorateChoices），纪念是时代增益，
    不改变战略方向，自动处理安全。返回 {"ok", "commemoration"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pEras = nil
pcall(function() pEras = Game.GetEras() end)
if not pEras then print("RESULT|NONE|当前版本不支持纪念系统"); return end
local allowed = 0
pcall(function() allowed = pEras:GetPlayerNumAllowedCommemorations(me) or 0 end)
if allowed <= 0 then print("RESULT|NONE|当前没有可选纪念"); return end
local choices = pEras:GetPlayerCommemorateChoices(me)
local chosen = nil
if choices then
  for _, idx in ipairs(choices) do
    local row = GameInfo.CommemorationTypes[idx]
    if row then chosen = {idx=idx, type=row.CommemorationType} break end
  end
end
if not chosen then print("RESULT|NONE|没有可用纪念选项"); return end
local params = {}
params[PlayerOperations.PARAM_COMMEMORATION_TYPE] = chosen.idx
UI.RequestPlayerOperation(me, PlayerOperations.COMMEMORATE, params)
__civ6_comm_before = allowed
__civ6_comm_choice = chosen.idx
print("RESULT|" .. chosen.type)
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
        result_line = next((line.strip() for line in out if line.strip().startswith("RESULT|")), "")
        if not result_line:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "选择纪念无回执（Tuner 输出丢失），请重试"}
        parts = result_line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NONE":
            return {"ok": False, "error": "NONE", "detail": "当前没有可选纪念"}
        verify_lua = """
local me = Game.GetLocalPlayer()
local pEras = nil
pcall(function() pEras = Game.GetEras() end)
local allowed, before = -1, -1
if pEras then
  pcall(function() allowed = pEras:GetPlayerNumAllowedCommemorations(me) or 0 end)
end
before = __civ6_comm_before or -1
local choice = __civ6_comm_choice or -1
local still_available = false
if pEras then
  pcall(function()
    local choices = pEras:GetPlayerCommemorateChoices(me)
    if choices then
      for _, idx in ipairs(choices) do
        if idx == choice then still_available = true end
      end
    end
  end)
end
if (before >= 0 and allowed < before) or not still_available then
  __civ6_comm_before = nil
  __civ6_comm_choice = nil
end
print("COMM|allowed=" .. tostring(allowed) .. "|before=" .. tostring(before)
      .. "|still=" .. tostring(still_available))
"""
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.4, remaining))
            verified = await _t.execute_lua(
                reader, writer, verify_lua, state_index=_t._ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("COMM|")), "")
            values = {part.split("=")[0]: part.split("=", 1)[1]
                      for part in line[len("COMM|"):].split("|") if "=" in part} if line else {}
            allowed, before = _t._to_int(values.get("allowed", "-1")), _t._to_int(values.get("before", "-1"))
            if (before >= 0 and allowed < before) or values.get("still") == "false":
                return {"ok": True, "app": app_id, "commemoration": tag, "verified": True,
                        "detail": f"已读回验证纪念选择：{tag}", "via": "tuner"}
        return {"ok": False, "error": "SILENT_FAILURE",
                "detail": "选择纪念后未读回次数/可选列表变化，未宣称成功"}
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def auto_fill_policy_slots(timeout: float = 12.0) -> dict:
    """自动填满空政策槽位（InGame，官方 UNLOCK_POLICIES + RequestPolicyChanges 三步）——局面感知。

    用于 ENDTURN_BLOCKING_FILL_CIVIC_SLOT（"填补政策槽位"）：市政完成/政府变更后
    出现空槽会挡住结束回合。**选择逻辑按局面打分**（不是机械填第一个）：
    读人口/金币/侦察兵/勇士数 → PolicyType 白名单权重（城市规划+1产=最优先；
    神王+1金=金币紧张时高；勘察+1移动=有侦察兵时高；纪律+15战力=有勇士时中；
    农业+1粮=人口少时高）→ 每空槽取"槽型匹配且分数最高"的卡。
    返回 {"ok", "filled", "slots", "picked"}（picked=["POLICY_X=理由", ...]）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local numSlots = pCulture:GetNumPolicySlots()
local slotTypeMap = {SLOT_ECONOMIC=0, SLOT_MILITARY=1, SLOT_DIPLOMATIC=2, SLOT_WILDCARD=3, SLOT_GREAT_PERSON=4}
local slotNames = {[0]="SLOT_ECONOMIC", [1]="SLOT_MILITARY", [2]="SLOT_DIPLOMATIC", [3]="SLOT_WILDCARD", [4]="SLOT_WILDCARD"}
-- 局面读数
local pop, gold, scouts, warriors = 0, -1, 0, 0
pcall(function()
  for _, c in Players[me]:GetCities():Members() do pop = pop + c:GetPopulation() end
end)
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info then
      local ut = info.UnitType
      if ut == "UNIT_SCOUT" then scouts = scouts + 1
      elseif ut == "UNIT_WARRIOR" then warriors = warriors + 1 end
    end
  end
end)
-- 政策评分表（按局面加权）
local function score(policy)
  local pt = policy.PolicyType or ""
  if pt == "POLICY_URBAN_PLANNING" then return 100, "城市规划：+1产能（首都发展核心）" end
  if pt == "POLICY_ILKUM" then return 92, "伊利库姆：工人+1移动（改良加速）" end
  if pt == "POLICY_GOD_KING" then return (gold >= 0 and gold < 25) and 90 or 72, "神王：+1金币" end
  if pt == "POLICY_SURVEY" then return (scouts > 0) and 88 or 45, "勘察：侦察兵+1移动（探图更快）" end
  if pt == "POLICY_AGRICULTURE" then return (pop < 5) and 78 or 50, "农业：+1粮食（人口少时增长快）" end
  if pt == "POLICY_DISCIPLINE" then return (warriors > 0) and 68 or 35, "纪律：+15近战战斗力" end
  if pt == "POLICY_OVERSEAS_APPOINTMENTS" then return 62, "海外任命：每城+1金币（多城时强）" end
  if pt == "POLICY_CHARTERS" then return 84, "特许状：国际商路金币+2（外交槽核心，配合商队）" end
  if pt == "POLICY_DIPLOMATIC_LEAGUE" then return 66, "外交联盟：外交支持+2/回合（议会筹码）" end
  return 30
end
-- 预收集可用政策（UNLOCK 前 CanSlotPolicy 可靠）
local usable = {}
for policy in GameInfo.Policies() do
  if pCulture:IsPolicyUnlocked(policy.Index) then
    for s = 0, numSlots - 1 do
      local ok = false
      pcall(function() ok = pCulture:CanSlotPolicy(policy.Index, s) end)
      if ok then
        local st = pCulture:GetSlotType(s)
        local pst = policy.GovernmentSlotType or "SLOT_WILDCARD"
        if st < 3 or pst == "SLOT_WILDCARD" or pst == slotNames[st] then
          local sc = score(policy)
          usable[#usable+1] = {policy=policy, slot=s, sc=sc}
          break
        end
      end
    end
  end
end
UI.RequestPlayerOperation(me, PlayerOperations.UNLOCK_POLICIES, {})
local clearList = {}
local addList = {}
local picked = {}
local taken = {}
for s = 0, numSlots - 1 do
  if pCulture:GetSlotPolicy(s) < 0 then
    local best, bestSc = nil, -1
    for _, u in ipairs(usable) do
      if u.slot == s and not taken[u.policy.Index] and u.sc > bestSc then
        best, bestSc = u.policy, u.sc
      end
    end
    if best then
      taken[best.Index] = true
      addList[s] = best.Hash
      clearList[#clearList+1] = s
      picked[#picked+1] = best.PolicyType
    end
  end
end
if #picked > 0 then
  -- addList 以槽位 0 起始，是稀疏表；picked 才是可安全取长度的密集列表。
  pcall(function() pCulture:RequestPolicyChanges(clearList, addList) end)
end
__civ6_policy_targets = addList
print("REQUESTED=" .. tostring(#picked) .. "&SLOTS=" .. table.concat(picked, "|"))
"""
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
        request_line = next((line for line in out if line.strip().startswith("REQUESTED=")), "")
        if not request_line:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "填政策槽无回执（Tuner 输出丢失），请重试"}
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in request_line.strip().split("&") if "=" in p}
        picked = kv.get("SLOTS", "").split("|") if kv.get("SLOTS") else []
        requested = _t._to_int(kv.get("REQUESTED", "0"))
        if requested == 0:
            return {"ok": True, "app": app_id, "filled": 0, "requested": 0, "picked": [],
                    "detail": "没有可填的空政策槽（或没有可用政策）", "via": "tuner"}

        # 写请求只发一次；后续只读回槽位内容，避免把“请求数”当成已填数。
        verify_lua = f"""
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local targets = __civ6_policy_targets or {{}}
local want_count = {requested}
local done, failed = 0, 0
for s = 0, pCulture:GetNumPolicySlots() - 1 do
  local want = targets[s]
  if want then
    if pCulture:GetSlotPolicy(s) == want then done = done + 1 else failed = failed + 1 end
  end
end
if done == want_count then __civ6_policy_targets = nil end
print("POLICY|done=" .. tostring(done) .. "|failed=" .. tostring(failed))
"""
        last_detail = "政策槽设置后未读回目标政策"
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))
            verified = await _t.execute_lua(
                reader, writer, verify_lua, state_index=_t._ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("POLICY|")), "")
            values = {part.split("=")[0]: part.split("=", 1)[1]
                      for part in line[len("POLICY|"):].split("|") if "=" in part} if line else {}
            done, failed = _t._to_int(values.get("done")), _t._to_int(values.get("failed"))
            if done == requested and failed == 0:
                return {"ok": True, "app": app_id, "filled": done, "requested": requested,
                        "picked": picked, "verified": True,
                        "detail": f"已读回验证 {done} 个政策槽（{'、'.join(picked) or '无'}）",
                        "via": "tuner"}
            last_detail = f"第 {attempt + 1} 次读回未匹配：成功 {done}，失败 {failed}"
        return {"ok": False, "error": "SILENT_FAILURE", "detail": last_detail + "；未宣称填写成功"}
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
