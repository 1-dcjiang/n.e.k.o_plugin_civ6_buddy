
local id = Game.GetLocalPlayer()
local pick = ""
pcall(function()
  for row in GameInfo.{gi_table}() do
    local p = Players[id]:{player_method}()
    if p and not p:{has_method}(row.Index) then
      local ok = false
      pcall(function() ok = p:CanResearch(row.Index) end)
      if ok then pick = row.{type_field}; break end
    end
  end
end)
print("PICK=" .. pick)
