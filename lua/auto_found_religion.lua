
local me = Game.GetLocalPlayer()
local pRel = Players[me]:GetReligion()
local created = -1
pcall(function() created = pRel:GetReligionTypeCreated() end)
if created >= 0 then print("RELIGION_NONE|already_founded") return end
-- 只有真的拿到创教单位（大先知）才发请求；否则每回合都会被游戏拒绝，
-- 读回也无法匹配，白白占用 tick 预算。HasReligiousFoundingUnit 是宗教面板
-- 判断“是否还能创教”的同一接口，缺失时按不可创教处理。
local has_founder = false
-- pcall 的返回值必须看：API 缺失/改名时 has_founder 仍是 false，
-- 与"确实没有创教单位"混成同一句话会让上层误判成"这局不用创教"。
local has_founder_ok = pcall(function() has_founder = pRel:HasReligiousFoundingUnit() end)
if not has_founder_ok then print("RELIGION_UNSUPPORTED|has_religious_founding_unit") return end
if not has_founder then print("RELIGION_NONE|no_founding_unit") return end
local relType = nil
for row in GameInfo.Religions() do
  if row.Type ~= "RELIGION_PANTHEON" then relType = row.Type; break end
end
if not relType then print("RELIGION_NONE|no_religion_available") return end
local relRow = GameInfo.Religions[relType]
local taken_belief = {}
local function pickBelief(cls, pref1)
  local first = nil
  for row in GameInfo.Beliefs() do
    if row.BeliefClassType == cls and not taken_belief[row.Index] then
      if first == nil then first = row.Type end
      if pref1 and row.Type == pref1 then return row.Type end
    end
  end
  return first
end
local fb = pickBelief("BELIEF_CLASS_FOLLOWER", "BELIEF_RELIGIOUS_SETTLEMENTS")
local fdb = pickBelief("BELIEF_CLASS_FOUNDER", "BELIEF_CHURCH_PROPERTY")
local params = {}
params[PlayerOperations.PARAM_RELIGION_TYPE] = relRow.Hash
params[PlayerOperations.PARAM_INSERT_MODE] = PlayerOperations.VALUE_EXCLUSIVE
__civ6_religion_expected = relRow.Index
__civ6_religion_beliefs = {}
if fb then __civ6_religion_beliefs[#__civ6_religion_beliefs+1] = GameInfo.Beliefs[fb].Index end
if fdb then __civ6_religion_beliefs[#__civ6_religion_beliefs+1] = GameInfo.Beliefs[fdb].Index end
UI.RequestPlayerOperation(me, PlayerOperations.FOUND_RELIGION, params)
if fb then
  local p2 = {}
  p2[PlayerOperations.PARAM_BELIEF_TYPE] = GameInfo.Beliefs[fb].Hash
  p2[PlayerOperations.PARAM_INSERT_MODE] = PlayerOperations.VALUE_EXCLUSIVE
  UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p2)
end
if fdb then
  local p3 = {}
  p3[PlayerOperations.PARAM_BELIEF_TYPE] = GameInfo.Beliefs[fdb].Hash
  p3[PlayerOperations.PARAM_INSERT_MODE] = PlayerOperations.VALUE_EXCLUSIVE
  UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p3)
end
print("RELIGION_OK|" .. relType .. "|" .. tostring(fb) .. "|" .. tostring(fdb))
