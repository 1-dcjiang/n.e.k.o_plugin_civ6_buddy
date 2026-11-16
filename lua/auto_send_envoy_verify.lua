
local me = Game.GetLocalPlayer()
local pInfluence = Players[me]:GetInfluence()
local target = __civ6_envoy_target or -1
local beforeEnvoys = __civ6_envoy_before or -1
local beforeTokens = __civ6_envoy_tokens_before or -1
local afterEnvoys, afterTokens = -1, -1
local supported = target >= 0 and beforeEnvoys >= 0 and beforeTokens >= 0 and pInfluence ~= nil
if supported then
  local okEnvoys = pcall(function() afterEnvoys = pInfluence:GetEnvoys(target) end)
  local okTokens = pcall(function() afterTokens = pInfluence:GetTokensToGive() end)
  supported = okEnvoys and okTokens and afterEnvoys >= 0 and afterTokens >= 0
end
local matched = supported and afterEnvoys == beforeEnvoys + 1 and afterTokens == beforeTokens - 1
if matched then
  __civ6_envoy_target = nil
  __civ6_envoy_before = nil
  __civ6_envoy_tokens_before = nil
end
print("ENVOY_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|envoys_before=" .. tostring(beforeEnvoys) .. "|envoys_after=" .. tostring(afterEnvoys)
      .. "|tokens_before=" .. tostring(beforeTokens) .. "|tokens_after=" .. tostring(afterTokens))
