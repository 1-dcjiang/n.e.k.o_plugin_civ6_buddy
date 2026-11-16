
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local numSlots = pCulture:GetNumPolicySlots()
local slotTypeMap = {SLOT_ECONOMIC=0, SLOT_MILITARY=1, SLOT_DIPLOMATIC=2, SLOT_WILDCARD=3, SLOT_GREAT_PERSON=4}
local slotNames = {[0]="SLOT_ECONOMIC", [1]="SLOT_MILITARY", [2]="SLOT_DIPLOMATIC", [3]="SLOT_WILDCARD", [4]="SLOT_WILDCARD"}
-- 局面读数
local pop, gold, scouts, warriors = 0, -1, 0, 0
pcall(function()
  for _, c in Players[me]:GetCities():Members() do pop = pop + c:GetPopulation() end
end)
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info then
      local ut = info.UnitType
      if ut == "UNIT_SCOUT" then scouts = scouts + 1
      elseif ut == "UNIT_WARRIOR" then warriors = warriors + 1 end
    end
  end
end)
-- 政策评分表（按局面加权）
local function score(policy)
  local pt = policy.PolicyType or ""
  if pt == "POLICY_URBAN_PLANNING" then return 100, "城市规划：+1产能（首都发展核心）" end
  if pt == "POLICY_ILKUM" then return 92, "伊利库姆：工人+1移动（改良加速）" end
  if pt == "POLICY_GOD_KING" then return (gold >= 0 and gold < 25) and 90 or 72, "神王：+1金币" end
  if pt == "POLICY_SURVEY" then return (scouts > 0) and 88 or 45, "勘察：侦察兵+1移动（探图更快）" end
  if pt == "POLICY_AGRICULTURE" then return (pop < 5) and 78 or 50, "农业：+1粮食（人口少时增长快）" end
  if pt == "POLICY_DISCIPLINE" then return (warriors > 0) and 68 or 35, "纪律：+15近战战斗力" end
  if pt == "POLICY_OVERSEAS_APPOINTMENTS" then return 62, "海外任命：每城+1金币（多城时强）" end
  if pt == "POLICY_CHARTERS" then return 84, "特许状：国际商路金币+2（外交槽核心，配合商队）" end
  if pt == "POLICY_DIPLOMATIC_LEAGUE" then return 66, "外交联盟：外交支持+2/回合（议会筹码）" end
  return 30
end
-- 预收集可用政策（UNLOCK 前 CanSlotPolicy 可靠）
local usable = {}
for policy in GameInfo.Policies() do
  if pCulture:IsPolicyUnlocked(policy.Index) then
    for s = 0, numSlots - 1 do
      local ok = false
      pcall(function() ok = pCulture:CanSlotPolicy(policy.Index, s) end)
      if ok then
        local st = pCulture:GetSlotType(s)
        local pst = policy.GovernmentSlotType or "SLOT_WILDCARD"
        if st < 3 or pst == "SLOT_WILDCARD" or pst == slotNames[st] then
          local sc = score(policy)
          usable[#usable+1] = {policy=policy, slot=s, sc=sc}
          break
        end
      end
    end
  end
end
UI.RequestPlayerOperation(me, PlayerOperations.UNLOCK_POLICIES, {})
local clearList = {}
local addList = {}
local policyTargets = {}
local picked = {}
local taken = {}
for s = 0, numSlots - 1 do
  if pCulture:GetSlotPolicy(s) < 0 then
    local best, bestSc = nil, -1
    for _, u in ipairs(usable) do
      if u.slot == s and not taken[u.policy.Index] and u.sc > bestSc then
        best, bestSc = u.policy, u.sc
      end
    end
    if best then
      taken[best.Index] = true
      addList[s] = best.Hash       -- RequestPolicyChanges 要 Hash
      policyTargets[s] = best.Index -- GetSlotPolicy 读回是索引，读回比对用索引
      clearList[#clearList+1] = s
      picked[#picked+1] = best.PolicyType
    end
  end
end
if #picked > 0 then
  -- addList 以槽位 0 起始，是稀疏表；picked 才是可安全取长度的密集列表。
  pcall(function() pCulture:RequestPolicyChanges(clearList, addList) end)
end
__civ6_policy_targets = policyTargets
print("REQUESTED=" .. tostring(#picked) .. "&SLOTS=" .. table.concat(picked, "|"))
