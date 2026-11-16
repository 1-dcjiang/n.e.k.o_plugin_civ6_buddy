
local me = Game.GetLocalPlayer()
local pRel = Players[me]:GetReligion()
local created, actualBeliefs = -1, {}
local okCreated = pcall(function() created = pRel:GetReligionTypeCreated() end)
local okBeliefs = pcall(function()
  local list = pRel:GetBeliefs()
  if list then
    for _, index in ipairs(list) do actualBeliefs[index] = true end
  end
end)
local expected = __civ6_religion_expected or -2
local wanted = __civ6_religion_beliefs or {}
local supported = okCreated and okBeliefs and expected >= 0
local matched = supported and created == expected
if matched then
  for _, index in ipairs(wanted) do
    if not actualBeliefs[index] then matched = false end
  end
end
if matched then
  __civ6_religion_expected = nil
  __civ6_religion_beliefs = nil
end
print("RELIGION_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|created=" .. tostring(created))
