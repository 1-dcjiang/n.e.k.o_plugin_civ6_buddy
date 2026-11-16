
local s = {}
local okT, t = pcall(function() return Game.GetCurrentGameTurn() end)
s[#s+1] = "TURN=" .. tostring(t or -1)
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not okP or pPlayer == nil then
  s[#s+1] = "ERR=NO_PLAYER"
  print(table.concat(s, "&"))
  return
end
pcall(function()
  local idx = 0
  for i, c in pPlayer:GetCities():Members() do
    idx = idx + 1
    local q = c:GetBuildQueue()
    local sz = 0
    pcall(function() sz = q:GetSize() or 0 end)
    local curH = 0
    pcall(function() curH = q:GetCurrentProductionTypeHash() or 0 end)
    local tl = -1
    pcall(function() tl = q:GetTurnsLeft() or -1 end)
    s[#s+1] = string.format("C%d|sz=%d|h=%d|tl=%d", idx, sz, curH, tl)
  end
end)
print(table.concat(s, "&"))
