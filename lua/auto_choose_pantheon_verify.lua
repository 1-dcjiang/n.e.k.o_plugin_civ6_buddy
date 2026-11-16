
local me = Game.GetLocalPlayer()
local expected = __civ6_pantheon_expected or -2
local actual = -1
local supported = expected >= 0
local ok = pcall(function() actual = Players[me]:GetReligion():GetPantheon() end)
supported = supported and ok
local matched = supported and actual == expected
if matched then __civ6_pantheon_expected = nil end
print("PANTHEON_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|actual=" .. tostring(actual))
