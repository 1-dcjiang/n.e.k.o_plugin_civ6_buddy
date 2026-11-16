
local me = Game.GetLocalPlayer()
local gold, faith = -1, -1
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
pcall(function() faith = Players[me]:GetReligion():GetFaithBalance() end)
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
