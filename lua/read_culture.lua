
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
