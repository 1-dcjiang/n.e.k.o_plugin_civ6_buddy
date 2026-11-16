
local me = Game.GetLocalPlayer()
local pInfluence = Players[me]:GetInfluence()
if pInfluence:GetTokensToGive() <= 0 then print("RESULT|NO_TOKENS|没有可用使节"); return end
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 收集可派城邦（按距离升序）
local cands = {}
for i = 0, 63 do
  if Players[i] and i ~= me and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local isMajor, isBarb = true, false
    pcall(function() isMajor = Players[i]:IsMajor() end)
    pcall(function() isBarb = Players[i]:IsBarbarian() end)
    if not isMajor and not isBarb then
      local can = false
      pcall(function() can = pInfluence:CanGiveTokensToPlayer(i) end)
      if can then
        local csx, csy = -1, -1
        pcall(function()
          for _, c in Players[i]:GetCities():Members() do csx, csy = c:GetX(), c:GetY(); break end
        end)
        local dist = -1
        pcall(function() dist = Map.GetPlotsDistance(capX, capY, csx, csy) end)
        local name = cfg and Locale.Lookup(cfg:GetPlayerName()) or "?"
        cands[#cands+1] = {id=i, dist=dist, name=name}
      end
    end
  end
end
table.sort(cands, function(a, b) return a.dist < b.dist end)
if #cands == 0 then print("RESULT|NONE|没有可派的城邦（未发现或都不可派）"); return end
local target = cands[1]
local beforeEnvoys = -1
local okRead = pcall(function() beforeEnvoys = pInfluence:GetEnvoys(target.id) end)
if not okRead or beforeEnvoys < 0 then print("RESULT|UNSUPPORTED|无法读取目标使节数"); return end
__civ6_envoy_target = target.id
__civ6_envoy_before = beforeEnvoys
__civ6_envoy_tokens_before = pInfluence:GetTokensToGive()
local params = {}
params[PlayerOperations.PARAM_PLAYER_ONE] = target.id
params[PlayerOperations.PARAM_FLAGS] = 0
UI.RequestPlayerOperation(me, PlayerOperations.GIVE_INFLUENCE_TOKEN, params)
print("RESULT|" .. target.name .. "|dist=" .. tostring(target.dist))
