
local me = Game.GetLocalPlayer()
local pEras = nil
pcall(function() pEras = Game.GetEras() end)
if not pEras then print("RESULT|NONE|当前版本不支持纪念系统"); return end
local allowed = 0
pcall(function() allowed = pEras:GetPlayerNumAllowedCommemorations(me) or 0 end)
if allowed <= 0 then print("RESULT|NONE|当前没有可选纪念"); return end
local choices = pEras:GetPlayerCommemorateChoices(me)
local chosen = nil
if choices then
  for _, idx in ipairs(choices) do
    local row = GameInfo.CommemorationTypes[idx]
    if row then chosen = {idx=idx, type=row.CommemorationType} break end
  end
end
if not chosen then print("RESULT|NONE|没有可用纪念选项"); return end
local params = {}
params[PlayerOperations.PARAM_COMMEMORATION_TYPE] = chosen.idx
UI.RequestPlayerOperation(me, PlayerOperations.COMMEMORATE, params)
__civ6_comm_before = allowed
__civ6_comm_choice = chosen.idx
print("RESULT|" .. chosen.type)
