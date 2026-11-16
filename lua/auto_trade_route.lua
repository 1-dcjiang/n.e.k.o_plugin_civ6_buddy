
local me = Game.GetLocalPlayer()
local opInfo = GameInfo.UnitOperations["UNITOPERATION_MAKE_TRADE_ROUTE"]
local created = {}
local pTrade = Players[me]:GetTrade()
local before = -1
local supported = pTrade ~= nil
if supported then
  local ok = pcall(function() before = pTrade:GetNumOutgoingRoutes() end)
  supported = ok and before >= 0
end
if not supported then print("TRADE_UNSUPPORTED|routes") return end
if opInfo then
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_TRADER" and (u:GetMovesRemaining() or 0) > 0 then
      local ux, uy = u:GetX(), u:GetY()
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
        UnitManager.RequestOperation(u, UnitOperationTypes.MAKE_TRADE_ROUTE, params)
        created[#created+1] = "trader@" .. ux .. "," .. uy .. "->" .. best[1] .. "," .. best[2]
      end
    end
  end
end
__civ6_trade_before = before
__civ6_trade_expected = #created
print("TRADE=" .. table.concat(created, "|"))
