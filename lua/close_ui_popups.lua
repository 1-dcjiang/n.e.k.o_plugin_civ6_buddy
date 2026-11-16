
local paths = {"/InGame/AdvisorPopup/AdvisorBase", "/InGame/AdvisorPopup",
               "/InGame/TechCivicCompletedPopup", "/InGame/NewTurnPanel",
               "/InGame/EraCompletePopup", "/InGame/GreatPeoplePopup",
               "/InGame/ChooseArtifact", "/InGame/UnitPromotionPopup",
               "/InGame/RazeCity", "/InGame/EspionagePopup", "/InGame/EventPopup",
               "/InGame/BoostUnlockedPopup"}
local closed = 0
for pass = 1, 3 do
  for _, p in ipairs(paths) do
    pcall(function()
      local c = ContextPtr:LookUpControl(p)
      if c then c:SetHide(true); closed = closed + 1 end
    end)
  end
end
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/InGameTopOptionsMenu")
  if c and not c:IsHidden() then
    LuaEvents.InGameTopOptionsMenu_Close()
    UIManager:DequeuePopup(c)
    c:SetHide(true)
    closed = closed + 1
  end
end)
for _, p in ipairs({"/InGame/Options", "/InGame/LoadGameMenu", "/InGame/SaveGameMenu",
                    "/InGame/InGameTopOptionsMenu/Options",
                    "/InGame/InGameTopOptionsMenu/LoadGameMenu",
                    "/InGame/InGameTopOptionsMenu/SaveGameMenu"}) do
  pcall(function()
    local c = ContextPtr:LookUpControl(p)
    if c and not c:IsHidden() then c:SetHide(true); closed = closed + 1 end
  end)
end
pcall(function() LuaEvents.NaturalWonderPopup_Closed() end)
pcall(function() Events.HideLeaderScreen() end)
local nwVisible, menuVisible = false, false
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/NaturalWonderPopup")
  if c then nwVisible = not c:IsHidden() end
end)
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/InGameTopOptionsMenu")
  if c then menuVisible = not c:IsHidden() end
end)
print("CLOSED=" .. tostring(closed) .. "&NW=" .. tostring(nwVisible) .. "&MENU=" .. tostring(menuVisible))
