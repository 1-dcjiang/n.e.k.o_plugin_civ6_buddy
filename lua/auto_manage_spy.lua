
local me = Game.GetLocalPlayer()
local found = false
for _, u in Players[me]:GetUnits():Members() do
  local info = GameInfo.Units[u:GetType()]
  if info and info.UnitType == "UNIT_SPY" then
    found = true
    print("SPY_FOUND|" .. u:GetID())
  end
end
print("SPY_SCAN|" .. tostring(found))
