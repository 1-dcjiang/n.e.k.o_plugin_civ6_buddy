
local id = Game.GetLocalPlayer()
local idx = nil
for row in GameInfo.{gi_table}() do
    if row.{type_field} == "{item_name}" then idx = row.Index; break end
end
if idx == nil then print("ERR:NOT_FOUND|{item_name}") return end
if Players[id]:{player_method}():{has_method}(idx) then
    print("ERR:ALREADY_COMPLETED|{item_name}") return
end
local params = {{}}
params[PlayerOperations.{param}] = idx
UI.RequestPlayerOperation(id, PlayerOperations.{operation}, params)
local cleared = 0
pcall(function()
  local list = NotificationManager.GetList(id)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(id, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt and bt == EndTurnBlockingTypes.{blocking} then
          pcall(function() NotificationManager.SendActivated(id, nid) end)
          pcall(function() NotificationManager.Dismiss(id, nid) end)
          cleared = cleared + 1
        end
      end
    end
  end
end)
print("{ok_prefix}|{item_name}|idx=" .. tostring(idx) .. "|cleared=" .. tostring(cleared))
