
local me = Game.GetLocalPlayer()
local pGovs = nil
pcall(function() pGovs = Players[me]:GetGovernors() end)
local expected = __civ6_gov_expected or -1
local found = false
if pGovs then
  pcall(function()
    local list = pGovs:GetGovernorsAppointed()
    if list then
      for _, idx in ipairs(list) do
        if idx == expected then found = true end
      end
    end
  end)
end
if found then __civ6_gov_expected = nil end
print("GOV|found=" .. tostring(found))
