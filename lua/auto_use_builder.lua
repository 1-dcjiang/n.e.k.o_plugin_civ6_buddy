
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
