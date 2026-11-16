
local me = Game.GetLocalPlayer()
local pGovs = nil
pcall(function() pGovs = Players[me]:GetGovernors() end)
if not pGovs then print("RESULT|NO_POINT|当前版本不支持总督系统（无 Rise&Fall/GS）"); return end
local canAppoint = false
pcall(function() canAppoint = pGovs:CanAppoint() end)
if not canAppoint then print("RESULT|NO_POINT|没有可用总督任命点数"); return end
local appointed = {}
pcall(function()
  local list = pGovs:GetGovernorsAppointed()
  if list then
    for _, idx in ipairs(list) do appointed[idx] = true end
  end
end)
local chosen = nil
for gov in GameInfo.Governors() do
  if not appointed[gov.Index] then chosen = gov break end
end
if not chosen then print("RESULT|ALL_APPOINTED|所有总督都已任命"); return end
local params = {}
params[PlayerOperations.PARAM_GOVERNOR_TYPE] = chosen.Index
UI.RequestPlayerOperation(me, PlayerOperations.APPOINT_GOVERNOR, params)
__civ6_gov_expected = chosen.Index
local cityName = "?"
pcall(function()
  local city = Players[me]:GetCities():GetCapitalCity()
  if city then
    cityName = Locale.Lookup(city:GetName())
    local params2 = {}
    params2[PlayerOperations.PARAM_GOVERNOR_TYPE] = chosen.Index
    params2[PlayerOperations.PARAM_CITY_DEST] = city:GetID()
    params2[PlayerOperations.PARAM_PLAYER_ONE] = me
    UI.RequestPlayerOperation(me, PlayerOperations.ASSIGN_GOVERNOR, params2)
  end
end)
print("RESULT|" .. chosen.GovernorType .. "|" .. (Locale.Lookup(chosen.Name) or "?") .. "|" .. cityName)
