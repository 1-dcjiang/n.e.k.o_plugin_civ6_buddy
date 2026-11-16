
local gpMgr = Game.GetGreatPeople()
local expected = __civ6_gp_expected or -1
local supported, stillUnclaimed = gpMgr ~= nil and expected >= 0, false
if supported then
  local ok = pcall(function()
    local list = gpMgr:GetUnclaimedGreatPeople() or {}
    for i = 1, #list do
      if list[i]:GetIndex() == expected then stillUnclaimed = true end
    end
  end)
  supported = ok
end
local matched = supported and not stillUnclaimed
if matched then __civ6_gp_expected = nil end
print("GP_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|expected=" .. tostring(expected))
