
local id = Game.GetLocalPlayer()
local idx = nil
for row in GameInfo.{p11}() do
    if row.{p10} == "{p9}" then idx = row.Index; break end
end
if idx == nil then print("ERR:NOT_FOUND|{p8}") return end
if Players[id]:{p7}():{p6}(idx) then
    print("ERR:ALREADY_COMPLETED|{p5}") return
end
local params = {{}}
params[PlayerOperations.{p4}] = idx
UI.RequestPlayerOperation(id, PlayerOperations.{p3}, params)
local cleared = 0
pcall(function()
  local list = NotificationManager.GetList(id)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(id, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt and bt == EndTurnBlockingTypes.{p2} then
          pcall(function() NotificationManager.SendActivated(id, nid) end)
          pcall(function() NotificationManager.Dismiss(id, nid) end)
          cleared = cleared + 1
        end
      end
    end
  end
end)
print("{p1}|{p0}|idx=" .. tostring(idx) .. "|cleared=" .. tostring(cleared))
