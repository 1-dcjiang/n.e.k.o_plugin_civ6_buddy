
local turn = 0
local p = -1
local civName = ""
local cities = 0
local techName = ""
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if okP and pPlayer ~= nil then
  p = Game.GetLocalPlayer() or -1
  pcall(function() turn = Game.GetCurrentGameTurn() or 0 end)
  pcall(function()
    local cfg = PlayerConfigurations[Game.GetLocalPlayer() or 0]
    if cfg ~= nil then
      civName = Locale.Lookup(cfg:GetCivilizationShortDescription()) or ""
    end
  end)
  local okCities, cList = pcall(function() return pPlayer:GetCities() end)
  if okCities and cList ~= nil then
    local okCnt, cnt = pcall(function() return cList:GetCount() end)
    if okCnt and cnt ~= nil then cities = cnt end
  end
  local okTechs, techs = pcall(function() return pPlayer:GetTechs() end)
  if okTechs and techs ~= nil then
    pcall(function()
      local cur = techs:GetResearchingTech()
      if cur ~= nil and GameInfo.Technologies[cur] ~= nil then
        techName = Locale.Lookup(GameInfo.Technologies[cur].Name) or ""
      end
    end)
  end
end
print("OK|turn=" .. tostring(turn) .. "|p=" .. tostring(p) .. "|civ=" .. tostring(civName) .. "|cities=" .. tostring(cities) .. "|tech=" .. tostring(techName))
