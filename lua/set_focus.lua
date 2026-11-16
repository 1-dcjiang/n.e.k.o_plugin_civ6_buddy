
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
