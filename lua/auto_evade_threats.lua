
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
