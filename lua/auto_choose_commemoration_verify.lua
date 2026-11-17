
local me = Game.GetLocalPlayer()
local pEras = nil
local okEras = pcall(function() pEras = Game.GetEras() end)
local supported = okEras and pEras ~= nil
local allowed, still_available = -1, false
local before = __civ6_comm_before or -1
local choice = __civ6_comm_choice or -1
if supported then
  local okAllowed = pcall(function() allowed = pEras:GetPlayerNumAllowedCommemorations(me) or 0 end)
  local okChoices = pcall(function()
    local choices = pEras:GetPlayerCommemorateChoices(me)
    if choices then
      for _, idx in ipairs(choices) do
        if idx == choice then still_available = true end
      end
    end
  end)
  -- 三个 pcall 有任意一个失败，都必须报 supported=false：
  -- 失败时 allowed 是 -1、still_available 是 false，直接拿去判断会读成"选择已生效"。
  supported = okAllowed and okChoices and allowed >= 0
end
if supported and ((before >= 0 and allowed < before) or not still_available) then
  __civ6_comm_before = nil
  __civ6_comm_choice = nil
end
print("COMM|supported=" .. tostring(supported) .. "|allowed=" .. tostring(allowed)
      .. "|before=" .. tostring(before) .. "|still=" .. tostring(still_available))
