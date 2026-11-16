
local id = Game.GetLocalPlayer()
local current = -1
pcall(function() current = Players[id]:{p2}():{p1}() end)
print("VERIFY|current=" .. tostring(current) .. "|want={p0}")
