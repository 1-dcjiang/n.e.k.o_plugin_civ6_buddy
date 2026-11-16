
local me = Game.GetLocalPlayer()
local busy = {}
local idle = 0
local total = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetX() ~= -9999 then
      total = total + 1
      local m = u:GetMovesRemaining() or 0
      if m > 0 then
        local nm = ""
        pcall(function() nm = u:GetName() end)
        busy[#busy+1] = tostring(nm) .. ":" .. tostring(m)
      else
        idle = idle + 1
      end
    end
  end
end)
local parts = {}
parts[#parts+1] = "TOTAL=" .. tostring(total)
parts[#parts+1] = "IDLE=" .. tostring(idle)
parts[#parts+1] = "BUSY=" .. tostring(#busy)
parts[#parts+1] = "LIST=" .. table.concat(busy, ",")
print(table.concat(parts, "&"))
