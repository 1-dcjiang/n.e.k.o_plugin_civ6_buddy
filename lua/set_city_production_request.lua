
local me = Game.GetLocalPlayer()
local item = GameInfo.{p12}["{p11}"]
if item == nil then print("ERR:ITEM_NOT_FOUND|{p10}") return end
local targets = {{}}
local rejected = {{}}
local occupied = 0
local function consider(pCity)
  if pCity == nil then return end
  local id = pCity:GetID()
  local bq = pCity:GetBuildQueue()
  local size = bq:GetSize() or 0
  if {p9} and size > 0 then occupied = occupied + 1; return end
  local params = {{}}
  params[CityOperationTypes.{p8}] = item.Hash
  params[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
  if "{p7}" == "district" then
    params[CityOperationTypes.PARAM_X] = {p6}
    params[CityOperationTypes.PARAM_Y] = {p5}
    if Map.GetPlot({p4}, {p3}) == nil then rejected[#rejected+1] = id .. ":PLOT"; return end
  end
  local canProduce, canStart = false, false
  pcall(function() canProduce = bq:CanProduce(item.Hash, true) end)
  pcall(function() canStart = CityManager.CanStartOperation(pCity, CityOperationTypes.BUILD, params, true) end)
  if not canProduce or not canStart then rejected[#rejected+1] = id .. ":UNAVAILABLE"; return end
  CityManager.RequestOperation(pCity, CityOperationTypes.BUILD, params)
  targets[#targets+1] = id
end
if {p2} >= 0 then
  local pCity = CityManager.GetCity(me, {p1})
  if pCity == nil then print("ERR:CITY_NOT_FOUND|{p0}") return end
  consider(pCity)
else
  for pCity in Players[me]:GetCities():Members() do consider(pCity) end
end
__civ6_prod_targets = targets
print("REQUESTED=" .. tostring(#targets) .. "|OCCUPIED=" .. tostring(occupied)
      .. "|REJECTED=" .. table.concat(rejected, ","))
