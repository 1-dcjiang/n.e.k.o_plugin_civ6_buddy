
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
