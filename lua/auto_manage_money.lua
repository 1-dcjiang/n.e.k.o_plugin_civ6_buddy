
local me = Game.GetLocalPlayer()
local gold, builders = -1, 0
local okGold = pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
local okBuilders = pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_BUILDER" then builders = builders + 1 end
  end
end)
local action = ""
if not okGold or not okBuilders then
  action = "UNSUPPORTED"
elseif builders < 2 and gold >= 150 then
  local item = GameInfo.Units["UNIT_BUILDER"]
  local pCity = item and CityManager.GetCity(me, 0) or nil
  if pCity then
    local tParams = {}
    tParams[CityOperationTypes.PARAM_UNIT_TYPE] = item.Hash
    tParams[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
    local ok, canBuy = pcall(function()
      return CityManager.CanStartOperation(pCity, CityOperationTypes.PURCHASE, tParams, true)
    end)
    if ok and canBuy then
      __civ6_money_before_gold = gold
      __civ6_money_before_builders = builders
      CityManager.RequestOperation(pCity, CityOperationTypes.PURCHASE, tParams)
      action = "REQUESTED_BUILDER"
    else
      action = "CANNOT_BUY_BUILDER"
    end
  end
end
print("MONEY|gold=" .. tostring(gold) .. "|builders=" .. tostring(builders) .. "|" .. action)
