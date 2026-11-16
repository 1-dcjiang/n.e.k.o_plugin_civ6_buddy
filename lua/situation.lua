
local turn = 0
local p = -1
local civName = ""
local cities = 0
local techName = ""
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if okP and pPlayer ~= nil then
  p = Game.GetLocalPlayer() or -1
  pcall(function() turn = Game.GetCurrentGameTurn() or 0 end)
  local okCiv, civType = pcall(function() return pPlayer:GetCivilizationType() end)
  if okCiv and civType ~= nil then
    pcall(function()
      if GameInfo and GameInfo.Civilizations and GameInfo.Civilizations[civType] ~= nil then
        civName = tostring(GameInfo.Civilizations[civType].Name or "")
      end
    end)
  end
  local okCities, cList = pcall(function() return pPlayer:GetCities() end)
  if okCities and cList ~= nil then
    local okCnt, cnt = pcall(function() return cList:GetCount() end)
    if okCnt and cnt ~= nil then cities = cnt end
  end
  local okTechs, techs = pcall(function() return pPlayer:GetTechs() end)
  if okTechs and techs ~= nil then
    local okCur, cur = pcall(function() return techs:GetCurrentResearch() end)
    if okCur and cur ~= nil then
      pcall(function()
        if GameInfo and GameInfo.Technologies and GameInfo.Technologies[cur] ~= nil then
          techName = tostring(GameInfo.Technologies[cur].Name or "")
        end
      end)
    end
  end
end
print("OK|turn=" .. tostring(turn) .. "|p=" .. tostring(p) .. "|civ=" .. tostring(civName) .. "|cities=" .. tostring(cities) .. "|tech=" .. tostring(techName))
