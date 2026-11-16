
local me = Game.GetLocalPlayer()
local function isHostile(owner)
  if owner == nil or owner < 0 or owner == me then return false end
  local other = Players[owner]
  if other == nil then return false end
  local barbarian = false
  pcall(function() barbarian = other:IsBarbarian() end)
  if barbarian then return true end
  local atWar = false
  pcall(function() atWar = Players[me]:GetDiplomacy():IsAtWarWith(owner) end)
  return atWar
end
local rows = {}
for _, u in Players[me]:GetUnits():Members() do
  local x, y = u:GetX(), u:GetY()
  if x ~= -9999 then
    local info = GameInfo.Units[u:GetType()]
    local ut = info and info.UnitType or "?"
    local str = 0
    if info and info.Combat then str = info.Combat end
    local moves = 0
    pcall(function() moves = u:GetMovesRemaining() or 0 end)
    local tStr, tCount, tDist = -1, 0, 99
    for dx = -2, 2 do for dy = -2, 2 do
      if dx ~= 0 or dy ~= 0 then
        local nx, ny = x + dx, y + dy
        if Map.GetPlot(nx, ny) then
          pcall(function()
            local us = Map.GetUnitsAt(nx, ny)
            if us then
              for o in us:Units() do
                -- 敌我由单位所有者和外交战争状态决定，与地块归属无关；平民不构成战斗威胁。
                local ei = GameInfo.Units[o:GetType()]
                local cs = ei and ei.Combat or 0
                if isHostile(o:GetOwner()) and cs > 0 then
                  tCount = tCount + 1
                  if cs > tStr then
                    tStr = cs
                    tDist = math.max(math.abs(dx), math.abs(dy))
                  end
                end
              end
            end
          end)
        end
      end
    end end
    rows[#rows+1] = string.format("%d|%s|%d,%d|%d|%d|%d|%d|%d",
        u:GetID(), ut, x, y, str, moves, tStr, tCount, tDist)
  end
end
print("THREATS|" .. table.concat(rows, ";;"))
