
local me = Game.GetLocalPlayer()
local pEras = nil
pcall(function() pEras = Game.GetEras() end)
local allowed, before = -1, -1
if pEras then
  pcall(function() allowed = pEras:GetPlayerNumAllowedCommemorations(me) or 0 end)
end
before = __civ6_comm_before or -1
local choice = __civ6_comm_choice or -1
local still_available = false
if pEras then
  pcall(function()
    local choices = pEras:GetPlayerCommemorateChoices(me)
    if choices then
      for _, idx in ipairs(choices) do
        if idx == choice then still_available = true end
      end
    end
  end)
end
if (before >= 0 and allowed < before) or not still_available then
  __civ6_comm_before = nil
  __civ6_comm_choice = nil
end
print("COMM|allowed=" .. tostring(allowed) .. "|before=" .. tostring(before)
      .. "|still=" .. tostring(still_available))
