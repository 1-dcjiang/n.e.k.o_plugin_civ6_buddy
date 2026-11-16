
local me = Game.GetLocalPlayer()
local pTrade = Players[me]:GetTrade()
local before = __civ6_trade_before or -1
local expected = __civ6_trade_expected or -1
local after = -1
local supported = pTrade ~= nil and before >= 0 and expected > 0
if supported then
  local ok = pcall(function() after = pTrade:GetNumOutgoingRoutes() end)
  supported = ok and after >= 0
end
local matched = supported and after >= before + expected
if matched then
  __civ6_trade_before = nil
  __civ6_trade_expected = nil
end
print("TRADE_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|before=" .. tostring(before) .. "|after=" .. tostring(after))
