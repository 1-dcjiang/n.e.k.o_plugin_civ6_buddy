# 文明6 Lua API 实机冒烟清单

本清单用于发布前在真实游戏中复核插件用到的 FireTuner Lua API。
**当前状态：以下项目尚未在实机逐项签字，不能把单元测试当作实机验证。**

## 准备

1. 安装 Civilization VI Development Assets（Steam appid 597260），关闭 SDK 自带 FireTuner.exe。
2. 在 `AppOptions.txt` 的 `[Debug]` 节写 `EnableTuner 1`，重启游戏，确认 4318 端口监听。
3. 进入一局至少 50 回合的存档，记录回合号、当前科技/市政、城市数与生产队列。
4. 插件侧先运行 `civ6_ops_status`，保存一份真实局面作为对照基线。

## 0. 只读基线（2026-11-16，v0.6.11 已实测）

本机 `CivilizationVI_DX12` + 4318 直连，载入自动存档后执行插件自身只读接口，结果作为后续写操作对照：

| 接口 | 实机结果 |
|---|---|
| `tuner_status` | `ok=True`，101 个 Lua state，`GameCore=3`，APP 字段已去掉 NUL |
| `read_situation` | 回合 415，玩家 0，文明 `刚果`，城市 9，当前科技 `合成材料` |
| `list_cities` | 9 城；首城人口 13，产出 食物 27 / 产能 61 / 金币 113 / 科技 45 / 文化 25 / 信仰 12 |
| `read_production` | 回合 415，8 个有队列城市，`turns_left` 有值；`current_type` 仍由调用方映射 |
| `read_blockers` | 本次无 blocker |
| `units_blocker_status` | 35 个单位，27 个有剩余移动力，1 个 idle；只读探测不执行 skip |
| `read_threats` | 36 个单位，0 个受威胁 |
| `read_economy` | 金币 25429.7，信仰 8996.4，人口 76，城市 9，区域 33，万神殿 19 |
| `read_diplomacy` | 使节 0；6 家文明关系；9 个城邦，距离 9–40（不再是 -1） |
| `read_culture` | 文化 106.2/回合，旅游业绩 97，巨作 5，市政 `冷战` |

只读接口已实测；下面的写操作、`CanProduce`/`SetHide`/`PARAM_*` 与交还流程仍未签字，不能因为本节通过就当作写路径已验证。

## 0.5 写后读回（v0.6.10+ 统一框架）

七类写操作走 `civ6_tuner_auto.py` 的 `_write_and_verify`：写请求只发一次，随后只读回最多 3 次，读回失败或不确定不重发写请求。

| 操作 | 读回字段 | 单测覆盖 | 实机结果 |
|---|---|---|---|
| 万神殿 | 信仰索引 `GetPantheonBelief` | 成功/不匹配/不支持/协议失败 | 待验 |
| 派遣使节 | 目标城邦使节数与令牌数 | 同上 | 待验 |
| 招募伟人 | 未招募列表移除目标 | 同上 | 待验 |
| 商队路线 | `GetNumOutgoingRoutes` 增量 | 同上 | 待验 |
| 创立宗教 | 宗教类型 + 已选信条 | 同上 | 待验 |
| 购买建造者 | 建造者数 +1、金币下降 | 同上 | 待验 |
| 单位晋升 | 逐单位 `HasPromotion` + 剩余升级点 | 同上 | 待验 |

注意：购买单位/商队路线是否立即反映在 API 上，报告标注为“可能未实机核实”；若游戏实际延迟到下一回合，读回会判为不确定并交还，而不是误报成功。该行为需要在实机用一局存档确认。

## 1. 生产参数与 `CanProduce`

| 检查项 | 操作 | 期望 | 实机结果 |
|---|---|---|---|
| `CityOperationTypes.PARAM_UNIT_TYPE` 能选中单位 | 对空队列城市调用 `set_city_production`，`kind=unit` | 队列真实出现该单位，读回哈希匹配 | 待验 |
| `CityOperationTypes.PARAM_BUILDING_TYPE` 能选中建筑 | 同上，`kind=building` | 不报空参数、队列出现建筑 | 待验 |
| 区域必须带坐标 | `kind=district` 不带坐标 | 返回 `DISTRICT_LOCATION_REQUIRED`，不写入 | 待验 |
| 区域带坐标 | `kind=district` 带合法 `city_id/plot_x/plot_y` | `CityManager.CanStartOperation` 为真，队列出现区域 | 待验 |
| 不可生产项 | 请求当前时代不可生产的单位/建筑 | `CanProduce` 为假时返回 `CANNOT_PRODUCE`，不静默成功 | 待验 |

重点：确认 `CanProduce` 的调用对象是城市（`city:CanProduce(item)`）还是玩家/生产队列，
不同游戏版本可能不同；若报 nil，改按真实 API 调整，不能在 Lua 里包 `pcall` 后当成功。

## 2. 生产操作 `CanStartOperation`

| 检查项 | 操作 | 期望 | 实机结果 |
|---|---|---|---|
| 空队列可开工 | 对空队列城市调用 `CityManager.RequestOperation` | `CanStartOperation` 为真，返回 `OK` | 待验 |
| 队列已占用 | 对已有生产城市重复设置 | 返回 `OCCUPIED`，不覆盖原队列 | 待验 |
| 非法参数 | 请求不存在的地块/区域 | 返回 `REJECTED`，不显示成功 | 待验 |
| 多城批量 | 两座以上空队列城市同时设置 | 每城独立读回，不把一次请求数当成功数 | 待验 |

## 3. `PARAM_*` 键名

逐项在真实 `PlayerOperations` 表里打印键是否存在，并记录实际键名：

- 生产：`CityOperationTypes.PARAM_UNIT_TYPE`、`PARAM_BUILDING_TYPE`、`PARAM_DISTRICT_TYPE`、`PARAM_PROJECT_TYPE`、`PARAM_X`、`PARAM_Y`、`PARAM_INSERT_MODE`
- 科技/市政：`PlayerOperations.PARAM_TECH_TYPE`、`PARAM_CIVIC_TYPE`
- 总督：`PlayerOperations.PARAM_GOVERNOR_TYPE`、`PARAM_CITY_DEST`、`PARAM_PLAYER_ONE`
- 纪念：`PlayerOperations.PARAM_COMMEMORATION_TYPE`
- 政策：确认 `RequestPolicyChanges(clearList, addList)` 的槽位索引从 0 开始，`addList` 是稀疏表且值为 `Policy.Hash`；`GetSlotPolicy(s)` 读回是 `Policy.Index`，不能拿 Hash 直接比较

检查方式：在 InGame 状态执行只读 Lua，输出 `PlayerOperations.PARAM_*` 的 `tostring` 结果；
任何 nil 键都必须改成实机存在的键，不能靠 `pcall` 吞掉。

## 4. `SetHide` 与弹窗上下文

| 弹窗/上下文 | 操作 | 期望 | 实机结果 |
|---|---|---|---|
| 顾问提示 | `close_ui_popups` 后结束回合 | 弹窗消失，回合能推进 | 待验 |
| 科技/市政完成 | 同上 | 不误关通知中心，不吞掉可操作通知 | 待验 |
| 自然奇观/发现 | 同上 | 只关可安全关闭的上下文，无法确认时交还 | 待验 |
| 世界议会 | 注册投票处理器后交还 | 处理器注销，后续议会不自动投票 | 待验 |
| 政策/总督/纪念选择 | 自动处理后读回 | 选择真实生效，读回值与请求一致 | 待验 |

重点：`SetHide` 对不同弹窗的副作用不同，不能批量 Dismiss 通知；任何无法确认的上下文都应交还，
而不是盲点。

## 5. 回合与战争

- `Game.GetCurrentGameTurn()` 返回真实回合号；结束回合后轮询能观察到 +1。
- `ACTION_ENDTURN` 只发一次；结算慢时不得右键或重复发命令“自愈”。
- `IsAtWarWith` / `IsBarbarian` 对当前玩家与敌对单位返回预期布尔值。
- 战争预检为真时 tick 不执行任何单位移动，直接交还。

## 6. 记录

每项通过后填写“实机结果”列，并记录游戏版本、DLC、存档回合号与截图路径。
未通过项必须回到代码修，不能只改本文档。
