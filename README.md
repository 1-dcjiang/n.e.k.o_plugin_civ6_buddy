# 文明6陪玩（civ6_buddy）

让 YUI 陪玩家打文明6：深度监控（解析 .Civ6Save 存档）获知当前局面，据此给出陪玩建议。

## 能力

| 工具 | 作用 |
|---|---|
| `civ6_list_saves` | 列出本机全部文明6存档（最新在前，含自动/手动标记） |
| `civ6_situation` | 自动读最新存档 → 局面摘要（回合/速度/地图/你的文明/对手/城邦/MOD/陪玩提示） |
| `civ6_parse_save` | 按名字/关键字解析指定存档 → 完整局面 |
| `civ6_civ_profile` | 查询文明/领袖特性（中英均可，如"刚果"/"Kongo"） |
| `civ6_advise` | 基于局面给分主题建议（开局/科技/建城/战争/胜利/外交） |

## 玩法

1. 玩家打开文明6开始一局（产生 AutoSave 自动存档）；
2. 对 YUI 说"看看我这局什么情况" → `civ6_situation` 读出局面；
3. 说"这局怎么玩/科技怎么点/我该不该开战" → `civ6_advise` 给建议；
4. 想了解某文明 → `civ6_civ_profile`。

## 技术说明

- 存档解析：插件内嵌 [pydt/civ6-save-parser](https://github.com/pydt/civ6-save-parser)（MIT），
  通过 `civ6_save_parser/wrapper.js` 以 JSON 输出，Python 侧整理为中文局面。
- 依赖：本机 node >= 16（`node -v` 可查）。
- 只读：不修改存档、不写任何游戏文件。
- 存档目录默认 `Documents\My Games\Sid Meier's Civilization VI\Saves`，
  可在运行配置 `[civ6_buddy] saves_dir` 覆盖。

## 版本

- v0.1.0：首版。5 个工具，局面解析 + 规则化建议模板 + 文明知识库（40+ 文明/领袖中英映射、40 城邦）。
