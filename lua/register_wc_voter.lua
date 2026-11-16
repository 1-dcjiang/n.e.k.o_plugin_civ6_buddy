
local me = Game.GetLocalPlayer()
if __civ6_wc_handler then
  print("WC_ALREADY|registered")
  return
end
local function handler()
  local me = Game.GetLocalPlayer()
  local wc = Game.GetWorldCongress()
  if not wc or not wc:IsInSession() then return end
  local favor = Players[me]:GetFavor()
  local costs = wc:GetVotesandFavorCost()
  local maxV = costs.MaxVotes or 5
  local ress = wc:GetResolutions()
  if not ress or #ress == 0 then
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
    __civ6_wc_voted = true
    return
  end
  for _, res in ipairs(ress) do
    local kParams = {}
    kParams[PlayerOperations.PARAM_RESOLUTION_TYPE] = res.Type
    kParams[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = maxV
    kParams[PlayerOperations.PARAM_RESOLUTION_OPTION] = 1
    kParams[PlayerOperations.PARAM_RESOLUTION_SELECTION] = 0
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, kParams)
  end
  UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
  __civ6_wc_voted = true
end
Events.WorldCongressStage1.Add(handler)
__civ6_wc_handler = handler
print("WC_REGISTERED|ok")
