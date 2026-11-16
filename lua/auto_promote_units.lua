
local me = Game.GetLocalPlayer()
local prereqMap = {}
for row in GameInfo.UnitPromotionPrereqs() do
  local pt = row.UnitPromotion
  if not prereqMap[pt] then prereqMap[pt] = {} end
  table.insert(prereqMap[pt], row.PrereqUnitPromotion)
end
local results = {}
for _, unit in Players[me]:GetUnits():Members() do
  local exp = unit:GetExperience()
  if exp then
    local stored = 0
    pcall(function() stored = exp:GetStoredPromotions() end)
    if stored > 0 then
      local ui = GameInfo.Units[unit:GetType()]
      local promClass = ui and ui.PromotionClass or ""
      local chosen = nil
      for promo in GameInfo.UnitPromotions() do
        if promo.PromotionClass == promClass and not exp:HasPromotion(promo.Index) then
          local prereqs = prereqMap[promo.UnitPromotionType]
          local okPrereq = true
          if prereqs and #prereqs > 0 then
            okPrereq = false
            for _, reqType in ipairs(prereqs) do
              local reqInfo = GameInfo.UnitPromotions[reqType]
              if reqInfo and exp:HasPromotion(reqInfo.Index) then okPrereq = true break end
            end
          end
          if okPrereq then
            local can = false
            pcall(function() can = exp:CanPromote(promo.Index) end)
            if can then chosen = promo break end
          end
        end
      end
      if chosen then
        __civ6_promo_targets = __civ6_promo_targets or {}
        __civ6_promo_targets[#__civ6_promo_targets+1] = {unit=unit:GetID(), promo=chosen.Index}
        exp:SetPromotion(chosen.Index)
        local storedNow = 0
        pcall(function() storedNow = exp:GetStoredPromotions() end)
        if storedNow > 0 then pcall(function() exp:ChangeStoredPromotions(-storedNow) end) end
        local ut = ui and ui.UnitType or "?"
        results[#results+1] = ut .. "→" .. chosen.UnitPromotionType .. "|requested=true"
      else
        local ut = ui and ui.UnitType or "?"
        results[#results+1] = ut .. "→NONE"
      end
    end
  end
end
print("RESULT|" .. table.concat(results, ";;"))
