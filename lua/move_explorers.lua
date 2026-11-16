
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
