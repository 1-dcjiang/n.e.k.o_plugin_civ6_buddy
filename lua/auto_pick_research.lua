
local id = Game.GetLocalPlayer()
local pick = ""
pcall(function()
  for row in GameInfo.{p3}() do
    local p = Players[id]:{p2}()
    if p and not p:{p1}(row.Index) then
      local ok = false
      pcall(function() ok = p:CanResearch(row.Index) end)
      if ok then pick = row.{p0}; break end
    end
  end
end)
print("PICK=" .. pick)
