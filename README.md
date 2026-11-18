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
tick 互斥执行；交还会立即发出停止信号并取消当前等待，屏幕线程在下一次输入前停止。
完整交还共享 15 秒总预算，tick 锁与屏幕线程等待各最多 3 秒，清理只使用剩余时间，
相对 20 秒工具超时保留 5 秒余量。单个正在执行的系统调用无法强制撤回。
交还、战争或异常停止还会注销世界议会常驻处理器。超时可重新接管后继续，
战争、硬 blocker 或未确认推进会交还控制权；结束回合只发一次并轮询真实回合号。

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
  `civ6_tuner_auto.py` 负责自动决策与写操作，`civ6_tuner.py` 通过静态导入保留兼容接口。
- `civ6_lua_resources.py` 从插件内 `lua/` 目录按受校验资源名加载并缓存脚本；动态参数仍通过
  预定义占位符展开，不把 Lua 逻辑拼接进 Python 业务代码。
- 万神殿、使节、伟人、商队、宗教、购买建造者、单位升级七类操作均只发一次写请求，
  最多进行三次只读验证；读回失败、不匹配或 API 不支持时明确返回失败或不确定并提示人工核实，
  不盲目重写。购买建造者与商队另有分档：金币下降且有新建造者才算成功；金币没降先重试读回
  两次、第三次仍没降才判请求被拒；金币已扣但单位未现只记滞后并在下一回合只读复核；
  商队路线数要给路途结算留 6 个账龄（约 5 个后续回合），连续超龄才交还（见下方版本记录）。
- 单位晋升在 GameCore 侧直接调用 `exp:SetPromotion`，不是通过网络同步接口下发；托管按单机
  场景设计，多人联机时不保证其他玩家客户端同步，联机局面请先退出托管。
- 默认不修改存档或游戏文件；唯一例外是用户明确执行 `fix_tuner` 时，插件备份并修改
  `AppOptions.txt`。生产、研究与焦点分配均通过游戏官方 API，不改存档。
- 存档目录默认 `Documents\My Games\Sid Meier's Civilization VI\Saves`，
  可在运行配置 `[civ6_buddy] saves_dir` 覆盖。
- 运行配置 `[civ6_buddy] auto_irreversible = false` 可关闭花钱、创教、招伟人和派商队这四类
  不可逆/消耗型自动操作；生产、研究、政策、侦察与战争响应仍保留。默认 `true`，与旧版行为一致。

## 实机验证

Lua API（`CanProduce`、`CanStartOperation`、`PARAM_*` 键名、`SetHide` 对弹窗的副作用）无法用单元测试替代。
发布前按 [docs/lua-smoke-checklist.md](docs/lua-smoke-checklist.md) 在真实游戏中逐项记录；
未打勾的项目不能声称已在实机验证。

## 版本

- v0.6.16：在 0.6.15 基础上继续 A796 增量深审修复。购买建造者金币未下降不再首次读回就判失败，
  前两次返回 `None` 等待扣款落地、第三次才报 `VERIFY_MISMATCH`；商队复核账龄从 2 放宽到 6（约 5 个
  后续回合），避免正常长途路线被当成失败交还；待复核写操作移到 blocker/建造者操作之前，
  避免旧建造者用完消失后把已到的新单位读成未到；接管与交还/停止时清空 `_pending_writes`，
  防止旧账拿新存档比对；购买/商队 `VERIFY_UNSUPPORTED` 文案补 `auto_irreversible=false`
  指引。离线回归覆盖新增，实机 Lua 行为仍按冒烟清单待验。
- v0.6.15：A711 增量深审（a4ef58a → 4df6adf）收口。分级口径重写：0.6.13/0.6.14 把「读回不确定」降级为只记日志，
  M1 指出其中 `WRITE_UNCERTAIN`（请求阶段就没回执/通信失败）也被一起放过，等于把"可能根本没发出去"当成了
  "等下回合就好了"。现在只有 `VERIFY_LAGGING` 才顺延，且顺延项写入 `_pending_writes`，下一回合只重跑读回脚本
  复核（不重发写请求），连续两个回合仍不到位才交还；其余错误码一律交还。
  购买建造者读回三分档：金币下降且建造者 ≥ 请求前 +1 为成功（`==` 放松为 `>=`，避免别城同回合完成建造者误判）；
  金币没下降＝请求被静默拒绝，报 `VERIFY_MISMATCH`；金币已扣但单位未现＝`VERIFY_LAGGING`。
  商队读回不再把"同回合路线数没涨"当成功或静默，一律先记滞后、下回合复核。
  创教 `VERIFY_UNSUPPORTED` 文案补 `auto_irreversible=false` 配置指引；时代纪念读回脚本新增 `supported`
  汇总（`GetEras` 或任一 pcall 失败不再被读成"选择已生效"，遗留 #6）；`set_research_verify` /
  `auto_pick_research` / `auto_fill_policy_slots_verify` 三份 Lua 的 `p0..p3` 占位符改为语义名；
  四个模块的等待统一走模块内 `_sleep`，测试不再替换进程级 `asyncio.sleep`（M5）。
  278 项测试通过，Ruff 通过。Lua 实机行为仍需按冒烟清单复核（尤其购买三档与商队下回合复核）。
- v0.6.14：收 0.6.13 的一个真 bug。A697 的 N2 把「购买/商队的读回滞后」降级为只记日志，但分级用的错误码集合
  写成了 `WRITE_UNCERTAIN / VERIFY_MISMATCH / TIMEOUT / VERIFY_TIMEOUT`，而 `_write_and_verify` 在读回不匹配或
  超预算时实际返回的是 `VERIFY_UNCERTAIN`（`VERIFY_MISMATCH` 由 `_verify_mismatch()` 产生、基本没被调用）。
  结果是 0.6.13 的放宽对购买与商队根本没生效，等于白改。本版把 `VERIFY_UNCERTAIN` 补进集合，并按真实链路的
  四个码做参数化回归；同时把一条 0.6.12 时期的老测试（原本用 `VERIFY_UNCERTAIN` 断言「经济失败要交还」）
  改用 `VERIFY_UNSUPPORTED`，让它守「明确失败仍交还」这一档，不再与 N2 的放宽冲突。
  252 项测试通过，Ruff 通过。
- v0.6.13：A697 增量深审修复——创立宗教时 `HasReligiousFoundingUnit` 的 `pcall` 返回值现在会被检查，
  调用失败（API 缺失或改名）报 `RELIGION_UNSUPPORTED` 而不是混成“没有创教单位”，不再把读不出来当成
  这局不用创教；购买建造者与商队的读回滞后（新单位/路线要等下一回合结算才出现）只记日志、不再交还，
  创教与招伟人读不回仍交还，API 不可用等明确失败也仍然交还；伟人列表读不到时交还文案带上
  `auto_irreversible=false` 的配置指引；`set_city_production_request`/`set_city_production_verify`/
  `set_research` 三份 Lua 模板的倒序占位符 `p0..p12` 改为语义名（`item_table`/`item_name`/`param`/`x`/`y`
  等），模板与调用两侧同步；`_auto_irreversible` 改由类属性给出默认值，去掉 `getattr` 兜底，
  不跑构造的调用方也能读到真实默认。
  246 项测试通过，Ruff 通过。Lua 侧的实机行为仍需按冒烟清单复核（尤其创教前置判断与商队读回）。
- v0.6.12：A594 增量深审修复——创立宗教先检查 `HasReligiousFoundingUnit`，避免每回合向游戏发无效请求；
  晋升目标每次请求重置，死亡单位按“未达成”处理而不是 `VERIFY_UNSUPPORTED`；政策写请求用 `Hash`、
  读回比对改用槽位 `Policy.Index`；招募伟人先取名字再发请求；`RazeCity/ChooseArtifact/EventPopup/EspionagePopup`
  不再被直接 `SetHide`，改为探测并交还；购买/宗教/伟人/商队读回不确定时在结束回合前交还；
  `AppOptions` 读写异常改为结构化错误；`LOCALAPPDATA` 为空时锁文件回退到用户目录。
  新增 Lua 块配平、模板占位符展开与上述业务回归测试，236 项测试通过，Ruff 通过。
  Lua 写路径与七类写后读回仍按实机冒烟清单待验。
- v0.6.11：真实 FireTuner 只读 smoke 修复——城市产出六项解析、文明/当前科技、当前市政、
  信仰余额、城邦距离全部改为实机存在且返回真实值的 API；状态读取在总预算耗尽后停止后续读取。
  本机实机只读验证通过（刚果，回合 415），166 项测试通过、6 项因无游戏/环境跳过，Ruff 通过。
  写操作与 Lua 弹窗行为仍需按实机冒烟清单复核。
- v0.6.10：补齐万神殿、使节、伟人、商队、宗教、购买建造者与单位升级的
  单次写入和写后读回；交还全程共享 15 秒预算，握手与退避共享 deadline。
  修复 AppOptions 多编码解码与损坏 UTF-16 的失败路径，解除 Tuner 模块初始化环并改为静态导入；
  大块 Lua 拆入独立资源目录。官方宿主中 170 项测试通过，Ruff 与差异格式检查通过。
  Lua 行为仍需按实机冒烟清单复核。
- v0.6.9：交还清理串行化并在 `GAME_TUNER_OFF` 时退避重试一次，
  交还先等 tick 锁与屏幕线程退出；屏幕线程独立跟踪，未退出时拒绝重接管与新 tick。
  自动生产配额随城市数扩展，研究/政策/总督/纪念写后读回，政策读回改为校验槽位内容。
  自动决策/写操作拆到 `civ6_tuner_auto.py`，`civ6_tuner.py` 保留兼容门面；AppOptions
  非 UTF-8 时按原编码写回。Lua 行为仍需按实机冒烟清单复核。
- v0.6.8：修复 tick 并发与交还停止、协议异常分类、单位分支、敌我识别和稀疏政策槽；
  生产支持四类参数、多城与区域显式选址，只发一次写请求并按哈希读回；结束回合只发一次；
  注销世界议会常驻处理器，不批量吞噬通知，移除右键/亮度盲点自愈；AppOptions 在真正的 Debug 节原子写入，
  保留 CRLF/BOM；动态解析 InGame 索引，并补齐托管主链路回归测试。Lua 行为仍需在真实游戏中复核。
- v0.6.7：校验 Lua GameInfo 参数；完整清理 Tuner 残帧；输入注入失败不再误报成功；
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
