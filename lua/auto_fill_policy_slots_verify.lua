
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local targets = __civ6_policy_targets or {{}}
local want_count = {want_count}
local done, failed = 0, 0
for s = 0, pCulture:GetNumPolicySlots() - 1 do
  local want = targets[s]
  if want then
    if pCulture:GetSlotPolicy(s) == want then done = done + 1 else failed = failed + 1 end
  end
end
if done == want_count then __civ6_policy_targets = nil end
print("POLICY|done=" .. tostring(done) .. "|failed=" .. tostring(failed))
