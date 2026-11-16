
local me = Game.GetLocalPlayer()
local recruited = ""
local supported = true
local ok = pcall(function()
  local gpMgr = Game.GetGreatPeople()
  if not gpMgr then supported = false; return end
  local list = gpMgr:GetUnclaimedGreatPeople() or {}
  for i = 1, #list do
    local person = list[i]
    local pts = Players[me]:GetGreatPeoplePoints(person:GetGreatPersonClass():GetIndex())
    local cost = person:GetCost()
    if cost >= 0 and pts >= cost then
      -- 先取名字，再发写请求：GetName 失败时不能出现“请求已发出但 Python 认为未发送”的矛盾。
      local name = person:GetName() or ("GP#" .. tostring(person:GetIndex()))
      local kParams = {}
      kParams[PlayerOperations.PARAM_GREAT_PERSON_INDIVIDUAL_TYPE] = person:GetIndex()
      __civ6_gp_expected = person:GetIndex()
      UI.RequestPlayerOperation(me, PlayerOperations.RECRUIT_GREAT_PERSON, kParams)
      recruited = name
      break
    end
  end
end)
if not ok or not supported then print("GP|UNSUPPORTED") return end
print("GP|" .. recruited)
