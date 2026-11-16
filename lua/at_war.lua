
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local ok2, pDiplo = pcall(function() return pPlayer:GetDiplomacy() end)
if not ok2 or pDiplo == nil then print("ERR:NO_DIPLOMACY") return end
local count = 0
-- 遍历全部玩家槽（硬编码 0..62：GetNumPlayers 在该环境不可靠），逐人查 IsAtWarWith
for i = 0, 62 do
  local p = Players[i]
  if p ~= nil then
    local okAlive, alive = pcall(function() return p:IsAlive() end)
    if okAlive and alive then
      local okWar, atWar = pcall(function() return pDiplo:IsAtWarWith(i) end)
      if okWar and atWar then count = count + 1 end
    end
  end
end
print("OK|at_war=" .. tostring(count))
