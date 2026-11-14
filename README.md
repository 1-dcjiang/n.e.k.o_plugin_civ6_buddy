# 文明6陪玩（civ6_buddy）

让 YUI 陪玩家打文明6：深度监控（解析 .Civ6Save 存档）获知当前局面，据此给出陪玩建议；
进阶版可运营托管（处理弹窗、点下一回合），底层通道（FireTuner）自动装好后可
真实读取城市数据并执行官方市民焦点分配。

## 能力

| 工具 | 作用 |
|---|---|
| `civ6_list_saves` | 列出本机全部文明6存档（最新在前，含自动/手动标记） |
| `civ6_situation` | 自动读最新存档 → 局面摘要（回合/速度/地图/你的文明/对手/城邦/MOD/陪玩提示） |
| `civ6_parse_save` | 按名字/关键字解析指定存档 → 完整局面 |
| `civ6_civ_profile` | 查询文明/领袖特性（中英均可，如"刚果"/"Kongo"） |
| `civ6_advise` | 基于局面给分主题建议（开局/科技/建城/战争/胜利/外交） |
| `civ6_ops_status` | 读取实时回合、研究、生产、结束回合 blocker、单位、经济与外交状态；读取失败会明确标为部分结果 |
| `civ6_ops_takeover` | 完成存档与战争前置检查并进入托管；准备成功后须立即调用 tick，不会自行推进首回合 |
| `civ6_ops_tick` | 执行一回合或连续运营：处理 blocker → 结束回合 → 验证真实回合推进 |
| `civ6_ops_set_production` | 通过 FireTuner 官方 API 设置并验证城市生产队列 |
| `civ6_ops_set_research` | 通过 FireTuner 官方 API 设置科技或市政研究 |
| `civ6_ops_peek` | 只读抓取游戏窗口画面，不点击、不切换前台窗口 |
| `civ6_ops_handoff` | 交还运营控制权并清理运营锁 |
| `civ6_ops_citizens` | 精细运营：读真实城市数据 → 按目标（balanced/growth/production）执行官方焦点分配 |
| `civ6_ops_focus` | 直接给全城设产出焦点（FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT） |
| `civ6_tuner_status` | 探测 FireTuner 底层通道（4318）是否可用 |
| `civ6_city_details` | FireTuner 读全部城市实时人口与六项产出 |
| `civ6_ensure_sdk` | **自动处理前置链路**：检测/触发 Steam 安装 SDK（appid 597260）+ 自动启用 Tuner 配置 + 探测 4318 |

## 玩法

1. 玩家打开文明6开始一局（产生 AutoSave 自动存档）；
2. 对 YUI 说"看看我这局什么情况" → `civ6_situation` 读出局面；
3. 说"这局怎么玩/科技怎么点/我该不该开战" → `civ6_advise` 给建议；
4. 想了解某文明 → `civ6_civ_profile`；
5. 想托管运营 → `civ6_ops_takeover` 准备成功后，同一任务立即执行 `civ6_ops_tick(loop_turns=20)` 连续运营；
6. 想精细运营（市民/地块分配）→ `civ6_ops_citizens`（需先 `civ6_ensure_sdk` 装好底层通道）。

接管工具有 25 秒前置检查预算；连续运营全部回合共享 870 秒预算，避免单步超时累积。
超时或用户取消时清理托管锁，并在下一次游戏输入前停止；单个正在执行的系统调用无法强制撤回。
超时可重新调用 tick 继续，战争、硬 blocker 或连续两次未推进会立即交还控制权。

## 底层通道（FireTuner）一键就绪

FireTuner 是游戏调试端口（默认 4318），可读真实城市数据、执行官方焦点分配。
需要：Steam 装免费工具 SDK（appid 597260）+ 游戏启用 Tuner + 重启游戏。

- `civ6_ensure_sdk(action='check')`：汇总链路状态与缺口；
- `civ6_ensure_sdk(action='install')`：自动触发 Steam 下载 SDK（玩家在 Steam 弹窗点确认）；
- `civ6_ensure_sdk(action='fix_tuner')`：经用户明确要求后备份并修改 `AppOptions.txt` 启用 Tuner；
  **启用 Tuner 会禁用成就**，修改后需重启游戏。
- 游戏和 SDK 可位于不同 Steam 库：除注册表和常见目录外，插件还解析各候选根目录
  `steamapps/libraryfolders.vdf` 中的现代 `path` 字段及旧版数字键路径。

## 技术说明

- 存档解析：插件内嵌 [pydt/civ6-save-parser](https://github.com/pydt/civ6-save-parser)（MIT），
  通过 `civ6_save_parser/parser.bundle.js` 以 JSON 输出，Python 侧整理为中文局面。
  bundle 已包含运行时依赖，**发布包无需 `node_modules` 或 `npm install`**；许可证集中在
  `civ6_save_parser/THIRD_PARTY_NOTICES.txt`。
- 依赖：本机 node >= 16（`node -v` 可查）；精细运营额外需要 numpy + Steam SDK。
- 重建 bundle（仅开发需要 Node >= 18）：在 `civ6_save_parser` 中运行 `npm ci` 和 `npm run build`，
  固定 esbuild 版本与 `package-lock.json` 保证可复现；`wrapper.js` 和 `index.js` 源文件保留。
- 模块边界：`civ6_tuner_protocol.py` 负责协议/握手/Lua 帧，`civ6_window.py` 负责窗口右键，
  `civ6_tuner.py` 保留运营逻辑并重新导出原接口。
- 默认不修改存档或游戏文件；唯一例外是用户明确执行 `fix_tuner` 时，插件备份并修改
  `AppOptions.txt`。生产、研究与焦点分配均通过游戏官方 API，不改存档。
- 存档目录默认 `Documents\My Games\Sid Meier's Civilization VI\Saves`，
  可在运行配置 `[civ6_buddy] saves_dir` 覆盖。

## 版本

- v0.6.7：当前发布版。校验 Lua GameInfo 参数；完整清理 Tuner 残帧；输入注入失败不再误报成功；
  解析超时终止并回收 Node 进程、清理临时文件；右键前验证游戏窗口已前台；
  阻塞磁盘/子进程操作移出事件循环；修复托管锁生命周期与 blocker 失败误报；
  接管、状态与连续运营均使用总预算，取消时清理锁和连接并停止后续屏幕输入；
  支持 Steam 多库；存档解析器改为带许可证的自包含 bundle；拆分 Tuner 协议与窗口模块；
  补齐发布证明权限与工具文档。
- v0.6.6：完善玩家视角说明与自动化能力。
- v0.4.0–v0.6.5：逐步加入 SDK 自动配置、真实局面运营、生产/科研/blocker 处理、
  FireTuner 回合管理与 Windows/Linux 测试兼容。
- v0.3.0：FireTuner 底层通道（读城市数据/官方焦点分配），AppOptions EnableTuner 自动配置。
- v0.2.0：屏幕自动化运营托管（处理弹窗/点下一回合/战争横幅检测交还），复用 steam_manager 键鼠。
- v0.1.0：首版。5 个工具，局面解析 + 规则化建议模板 + 文明知识库（40+ 文明/领袖中英映射、40 城邦）。
