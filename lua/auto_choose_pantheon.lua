
local me = Game.GetLocalPlayer()
local pReligion = Players[me]:GetReligion()
local pan = -1
local okPan = pcall(function() pan = pReligion:GetPantheon() end)
if not okPan then print("RESULT|UNSUPPORTED|无法读取万神殿"); return end
if pan >= 0 then print("RESULT|ALREADY|已有万神殿"); return end
-- 首都位置
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 扫描首都 3 圈地形计数
local terrain = {desert=0, forest=0, tundra=0, river=0, coast=0, marsh=0}
pcall(function()
  for dx = -3, 3 do for dy = -3, 3 do
    if dx*dx + dy*dy <= 9 then
      local p = Map.GetPlot(capX + dx, capY + dy)
      if p then
        local t = GameInfo.Terrains[p:GetTerrainType()]
        local tt = t and t.TerrainType or ""
        if tt:find("DESERT") then terrain.desert = terrain.desert + 1 end
        if tt:find("TUNDRA") then terrain.tundra = terrain.tundra + 1 end
        if tt:find("SNOW") then terrain.tundra = terrain.tundra + 1 end
        if tt:find("COAST") or tt:find("OCEAN") then terrain.coast = terrain.coast + 1 end
        if tt:find("MARSH") or tt:find("FLOODPLAINS") then terrain.marsh = terrain.marsh + 1 end
        local feats = p:GetFeatureType()
        local feat = feats and GameInfo.Features[feats]
        local ft = feat and feat.FeatureType or ""
        if ft:find("FOREST") or ft:find("RAINFOREST") then terrain.forest = terrain.forest + 1 end
        if p:IsRiver() then terrain.river = terrain.river + 1 end
      end
    end
  end
end)
-- 计算可选万神殿（未被其他玩家取走）
local taken = {}
pcall(function()
  for i = 0, 63 do
    if Players[i] and Players[i]:IsAlive() then
      local pr = Players[i]:GetReligion()
      if pr and pr:GetPantheon() >= 0 then taken[pr:GetPantheon()] = true end
    end
  end
end)
-- 局面匹配优先级
local pick, reason = nil, ""
local order = {}
if terrain.desert >= 4 then table.insert(order, {"BELIEF_DESERT_FOLKLORE", "首都周边沙漠多，选沙漠传说（沙漠格+信仰）"}) end
if terrain.forest >= 6 then table.insert(order, {"BELIEF_SACRED_PATH", "首都周边森林多，选神圣之路（森林格+文化）"}) end
if terrain.tundra >= 4 then table.insert(order, {"BELIEF_DANCE_OF_THE_AURORA", "首都周边冻土多，选极光之舞（冻土格+信仰）"}) end
if terrain.river >= 3 then table.insert(order, {"BELIEF_RIVER_GODDESS", "首都临河，选河神（河岸格+住房与信仰）"}) end
if terrain.coast >= 4 then table.insert(order, {"BELIEF_GOD_OF_THE_SEA", "首都沿海，选海神（海岸格+产能）"}) end
table.insert(order, {"BELIEF_DIVINE_SPARK", "无强地形匹配，选神启（+25% 大预言家点，冲宗教通用）"})
for _, cand in ipairs(order) do
  local b = GameInfo.Beliefs[cand[1]]
  if b and not taken[b.Index] then pick = cand[1]; reason = cand[2]; break end
end
if pick == nil then print("RESULT|NONE|没有可用万神殿信仰"); return end
local params = {}
params[PlayerOperations.PARAM_BELIEF_TYPE] = GameInfo.Beliefs[pick].Hash
__civ6_pantheon_expected = GameInfo.Beliefs[pick].Index
UI.RequestPlayerOperation(me, PlayerOperations.FOUND_PANTHEON, params)
print("RESULT|" .. pick .. "|" .. reason)
