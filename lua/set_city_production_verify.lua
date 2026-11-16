
local me = Game.GetLocalPlayer()
local item = GameInfo.{p2}["{p1}"]
local targets = __civ6_prod_targets or {{}}
local done, failed, queueSize = 0, 0, 0
local failedIds = {{}}
for _, id in ipairs(targets) do
  local city = CityManager.GetCity(me, id)
  local queue = city and city:GetBuildQueue() or nil
  local size, current = 0, 0
  if queue then
    pcall(function() size = queue:GetSize() or 0 end)
    pcall(function() current = queue:GetCurrentProductionTypeHash() or 0 end)
  end
  queueSize = queueSize + size
  if size >= 1 and current == item.Hash then done = done + 1
  else failed = failed + 1; failedIds[#failedIds+1] = id end
end
if done == #targets or {p0} then __civ6_prod_targets = nil end
print("SET|done=" .. tostring(done) .. "|failed=" .. tostring(failed)
      .. "|size=" .. tostring(queueSize) .. "|ids=" .. table.concat(failedIds, ","))
