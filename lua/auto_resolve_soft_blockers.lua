
local me = Game.GetLocalPlayer()
local resolved = {}
local mark = function(name)
  local dup = false
  for _, v in ipairs(resolved) do if v == name then dup = true break end end
  if not dup then resolved[#resolved+1] = name end
end
-- 遍历通知，仅处理明确支持的软 blocker；宣战与间谍等普通/策略通知保持原样。
pcall(function()
  local list = NotificationManager.GetList(me)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(me, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt then
          if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_GOVERNOR_IDLE then
            pcall(function() NotificationManager.SendActivated(me, nid) end)
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("GOVERNOR_IDLE")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("WORLD_CONGRESS_LOOK")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("WORLD_CONGRESS_SPECIAL_SESSION")
          end
        end
      end
    end
  end
end)
-- 考虑更换政体（不依赖通知）
pcall(function()
  local p = Players[me]
  if p and p:GetCulture() and p:GetCulture():GetGovernmentChangeConsidered() == false then
    p:GetCulture():SetGovernmentChangeConsidered(true)
    mark("GOVERNMENT_CHANGE")
  end
end)
-- 世界议会"已查看"标记
pcall(function()
  UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {})
end)
print("RESOLVED=" .. table.concat(resolved, ","))
