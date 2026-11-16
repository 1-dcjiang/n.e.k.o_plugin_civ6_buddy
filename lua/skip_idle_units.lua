
local me = Game.GetLocalPlayer()
local skipped = 0
local left = 0
local forced = {forced}
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetX() ~= -9999 then
      local m = u:GetMovesRemaining() or 0
      if forced or m <= 0 then
        pcall(function() UnitManager.FinishMoves(u) end)
        skipped = skipped + 1
      else
        left = left + 1
      end
    end
  end
end)
print("SKIPPED=" .. tostring(skipped) .. "&LEFT=" .. tostring(left))
