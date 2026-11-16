
local me = Game.GetLocalPlayer()
local targets = __civ6_promo_targets or {}
local supported = #targets > 0
local done, missing = 0, 0
for _, target in ipairs(targets) do
  local found = false
  for _, unit in Players[me]:GetUnits():Members() do
    if unit:GetID() == target.unit then
      found = true
      local ok = pcall(function()
        local exp = unit:GetExperience()
        if exp and exp:HasPromotion(target.promo) and exp:GetStoredPromotions() == 0 then
          done = done + 1
        end
      end)
      if not ok then supported = false end
      break
    end
  end
  -- 单位死亡/消失是“未达成”，不是 API 不可用；否则会误报 VERIFY_UNSUPPORTED。
  if not found then missing = missing + 1 end
end
local matched = supported and missing == 0 and done == #targets
if matched then __civ6_promo_targets = nil end
print("PROMOTION_VERIFY|supported=" .. tostring(supported) .. "|matched=" .. tostring(matched)
      .. "|done=" .. tostring(done) .. "|expected=" .. tostring(#targets)
      .. "|missing=" .. tostring(missing))
