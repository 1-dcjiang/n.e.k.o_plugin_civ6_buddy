
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
