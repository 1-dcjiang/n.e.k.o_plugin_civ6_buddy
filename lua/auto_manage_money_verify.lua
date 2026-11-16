
local me = Game.GetLocalPlayer()
local gold, builders = -1, 0
local okGold = pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
local okBuilders = pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_BUILDER" then builders = builders + 1 end
  end
end)
local beforeGold = __civ6_money_before_gold or -1
local beforeBuilders = __civ6_money_before_builders or -1
local supported = okGold and okBuilders and beforeGold >= 0 and beforeBuilders >= 0
local matched = supported and builders == beforeBuilders + 1 and gold < beforeGold
if matched then
  __civ6_money_before_gold = nil
  __civ6_money_before_builders = nil
end
print("MONEY_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|gold=" .. tostring(gold) .. "|builders=" .. tostring(builders))
