
local id = Game.GetLocalPlayer()
local current = -1
pcall(function() current = Players[id]:{player_method}():{getter}() end)
print("VERIFY|current=" .. tostring(current) .. "|want={wanted}")
