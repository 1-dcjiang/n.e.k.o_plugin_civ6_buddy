"""文明6陪玩插件（civ6_buddy）— 让 YUI 陪玩家打文明6。

深度监控：解析 .Civ6Save 存档，获知当前局面（回合/速度/地图/玩家文明/
敌对文明/城邦/MOD），基于局面给陪玩建议（文明特性 + 对手 + 早期策略模板）。
运行依赖：本机 node（>=16）+ 插件内嵌 civ6_save_parser（已随插件打包）。

只读操作：不修改存档、不写游戏文件。存档目录可在 config 覆盖。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from plugin.sdk.plugin import NekoPluginBase, lifecycle, llm_tool, neko_plugin, plugin_entry

from . import civ6_advice, civ6_data, civ6_ops, civ6_parser, civ6_sdk, civ6_tuner

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_buddy")


# ── 运营锁（跨插件优先级） ──
# 运营托管期间写锁文件 `%LOCALAPPDATA%\N.E.K.O\config\civ6_ops_active`（带时间戳），
# steam_manager 等插件见新鲜锁（<5 分钟）即让位，避免抢占 Neko 运营注意力。
# 锁带时间戳：civ6_buddy 每回合刷新；崩溃/被杀导致残留锁在 5 分钟后自动失效，不误伤。
def _ops_lock_path() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "N.E.K.O", "config", "civ6_ops_active"
    )
_OPS_LOCK_TTL = 300.0


def _ops_lock_set() -> None:
    """写/刷新运营锁（内容=写入时间戳）。"""
    p = _ops_lock_path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _ops_lock_clear() -> None:
    p = _ops_lock_path()
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def ops_lock_active(ttl: float = _OPS_LOCK_TTL) -> bool:
    """锁存在且新鲜（mtime 距今 < ttl）→ 运营托管中。供其他插件（steam_manager）查询。"""
    p = _ops_lock_path()
    if not p or not os.path.isfile(p):
        return False
    try:
        return (time.time() - os.path.getmtime(p)) < ttl
    except OSError:
        return False

_ERR_HINTS: dict[str, str] = {
    "SAVES_DIR_NOT_FOUND": "存档目录不存在：请先打开文明6新建一局并自动存档，或在本插件 config 中配置 saves_dir",
    "NO_SAVES": "尚无任何存档：开局后等自动存档生成（AutoSave_0001.Civ6Save）再问",
    "SAVE_NOT_FOUND": "存档名没对上：可先用 civ6_list_saves 看有哪些存档，再按名字查",
    "PARSER_MISSING": "插件内嵌解析器缺失：请重装插件",
    "NODE_MISSING": "本机没有 node：安装 Node.js（https://nodejs.org）后重启插件",
    "PARSE_TIMEOUT": "解析超时：存档可能损坏或过大，换个存档试",
    "PARSE_FAILED": "解析失败：存档可能损坏或为不兼容版本，如实上报，不要编造局面",
    "PARSE_OUTPUT_INVALID": "解析器输出异常：重试一次，仍失败则上报插件问题",
}


def _wrap_err(exc: civ6_parser.Civ6Error) -> dict:
    out: dict = {"output": str(exc), "is_error": True, "error": exc.code}
    hint = _ERR_HINTS.get(exc.code)
    if hint:
        out["hint"] = hint
    return out


def _tool_params(_: dict) -> dict:
    """宿主 llm_tool 传参防御：parameters 偶发被传成标量（int/str），非 dict 降级空字典。"""
    p = _.get("parameters")
    return p if isinstance(p, dict) else {}


@neko_plugin
class Civ6BuddyPlugin(NekoPluginBase):
    """N.E.K.O. 插件：文明6陪玩（解析存档获知局面 + 陪玩建议）。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._saves_dir = ""
        self._ops_buttons: dict = {}
        self._cfg_loaded = False
        self._ops_active = False

    def _load_config(self) -> None:
        """读取运行配置 plugin.toml（civ6_buddy.saves_dir / ops_buttons），失败静默用默认。"""
        if self._cfg_loaded:
            return
        self._cfg_loaded = True
        try:
            cfg_path = Path(self.plugin_dir) / "config" / "plugin.toml"
            if cfg_path.is_file():
                import tomllib
                data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
                sec = data.get("civ6_buddy", {})
                if isinstance(sec, dict):
                    if sec.get("saves_dir"):
                        self._saves_dir = str(sec["saves_dir"])
                    buttons = sec.get("ops_buttons")
                    if isinstance(buttons, dict):
                        cleaned = {}
                        for k in ("next_turn", "policy_ok", "production_yes"):
                            v = buttons.get(k)
                            if (isinstance(v, (list, tuple)) and len(v) == 2
                                    and all(isinstance(n, (int, float)) for n in v)):
                                cleaned[k] = (float(v[0]), float(v[1]))
                        self._ops_buttons = cleaned
        except Exception:  # 配置异常不影响插件工作
            _logger.warning("civ6_buddy config read failed, use defaults", exc_info=True)

    # ══════════ 工具 ══════════

    @llm_tool(
        name="civ6_list_saves",
        description=(
            "列出本机文明6存档。何时用：用户说'看我有哪些文明6存档/最近玩了什么'或"
            "civ6_parse_save 报存档找不到时。参数无。"
            "返回按时间倒序的存档列表（相对路径/时间/大小/自动或手动）。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=20.0,
    )
    async def civ6_list_saves(self, **_):
        self._load_config()
        try:
            saves = civ6_parser.list_saves(self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        if not saves:
            return {"output": "未找到任何存档。请先打开文明6开始一局（产生自动存档）。", "is_error": False, "saves": []}
        lines = [f"共 {len(saves)} 个存档（最新在前）："]
        for s in saves:
            lines.append(f"- {s['rel']}（{s['mtime_text']}，{s['size']//1024}KB，{'自动' if s['kind']=='auto' else '手动'}）")
        return {"output": "\n".join(lines), "is_error": False, "saves": saves}

    @llm_tool(
        name="civ6_parse_save",
        description=(
            "解析指定文明6存档并输出完整局面。何时用：用户指定某个存档（如'看AutoSave_0002'）或想对比时。"
            "参数 save：存档相对路径/文件名/关键字，留空=最新存档。"
            "返回回合、速度、地图、玩家文明、敌对文明、城邦、MOD 及陪玩提示。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "save": {"type": "string", "description": "存档相对路径/文件名/关键字（留空=最新）"},
            },
            "required": [],
        },
        timeout=60.0,
    )
    async def civ6_parse_save(self, *, save: str = "", **_):
        params = _tool_params(_)
        save = str(save or params.get("save") or "").strip()
        self._load_config()
        try:
            sit = civ6_parser.parse_save(save, self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        return {
            "output": civ6_parser.format_situation(sit),
            "is_error": False,
            "situation": sit,
        }

    @llm_tool(
        name="civ6_situation",
        description=(
            "获取文明6当前局面（自动读最新存档）。何时用：用户说'看看我这局/现在什么情况/"
            "帮我分析这局'等陪玩提问主入口。参数无。"
            "返回回合、速度、地图、你的文明与特性、敌对文明、城邦、MOD 与陪玩提示。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=60.0,
    )
    async def civ6_situation(self, **_):
        self._load_config()
        # 优先 FireTuner 实时读取（游戏开着且启用 Tuner 时数据即时准确）
        try:
            live = await civ6_tuner.read_situation(timeout=8.0)
            if live.get("ok"):
                sit = civ6_parser.situation_from_live(live)
                lines = civ6_parser.format_situation(sit)
                if sit.get("cities"):
                    lines += f"\n🏙️ 城市数：{sit['cities']}（实时）"
                if sit.get("current_research"):
                    lines += f"\n🔬 当前研究：{sit['current_research']}（实时）"
                return {
                    "output": lines,
                    "is_error": False,
                    "situation": sit,
                    "via": "tuner",
                }
        except Exception:
            pass  # Tuner 不可用/异常 → 回退存档解析
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        return {
            "output": civ6_parser.format_situation(sit),
            "is_error": False,
            "situation": sit,
        }

    @llm_tool(
        name="civ6_civ_profile",
        description=(
            "查询文明6文明/领袖背景与特性。何时用：用户问'刚果强吗/这个文明怎么玩/"
            "秦始皇特性'等文明知识问题，或陪玩时需要了解某文明的玩法倾向。"
            "参数 civ：文明名（中英文均可，如'刚果'/'Kongo'/'德国'）。"
            "返回中文名、领袖、文明特性一句话与简单玩法倾向。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "civ": {"type": "string", "description": "文明名（中文或英文 id，如 刚果/Kongo）"},
            },
            "required": ["civ"],
        },
        timeout=15.0,
    )
    async def civ6_civ_profile(self, *, civ: str = "", **_):
        params = _tool_params(_)
        civ = str(civ or params.get("civ") or "").strip()
        if not civ:
            return {"output": "请提供文明名，例如：刚果 / Kongo / 德国。", "is_error": True, "error": "NO_CIV"}
        # 先按英文 id 精确，再按中文名反查，再按不区分大小写关键字
        match = None
        q = civ.strip()
        if q in civ6_data.CIVILIZATIONS:
            match = q
        else:
            for cid, entry in civ6_data.CIVILIZATIONS.items():
                if entry["name"] == q or q.lower() in cid.lower():
                    match = cid
                    break
        if not match:
            if q in civ6_data.CITY_STATES:
                return {
                    "output": f"🏙️ {q}：城邦。{civ6_data.CITY_STATES[q]}。城邦可以通过使者结盟获得其加成。",
                    "is_error": False,
                }
            known = "、".join(f"{v['name']}（{k}）" for k, v in civ6_data.CIVILIZATIONS.items())
            return {"output": f"未找到文明「{civ}」。已知文明：{known}", "is_error": True, "error": "CIV_UNKNOWN"}
        entry = civ6_data.CIVILIZATIONS[match]
        leader = entry.get("leader", "").replace(":", "（") + "）" if entry.get("leader") else ""
        ua = entry.get("ua", "")
        return {
            "output": f"🏛️ {entry['name']}（{match}）\n领袖：{leader}\n特性：{ua}",
            "is_error": False,
            "profile": {"id": match, "name": entry["name"], "leader": leader, "ua": ua},
        }

    @llm_tool(
        name="civ6_advise",
        description=(
            "基于当前局面给文明6陪玩建议。何时用：用户问'这局怎么玩/我该干嘛/"
            "开局策略/科技怎么点'等。参数 topic：建议主题（可选，如 开局/科技/建城/"
            "战争/胜利路线），留空=综合开局建议。"
            "返回基于玩家文明特性、对手构成、城邦与回合阶段的规则化建议要点。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "建议主题：开局/科技/建城/战争/胜利/外交（留空=综合）"},
            },
            "required": [],
        },
        timeout=60.0,
    )
    async def civ6_advise(self, *, topic: str = "", **_):
        params = _tool_params(_)
        topic = str(topic or params.get("topic") or "").strip()
        self._load_config()
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        lines = [civ6_parser.format_situation(sit), "", "💡 陪玩建议："]
        advice = civ6_advice.advice_lines(sit, topic)
        lines += advice
        return {"output": "\n".join(lines), "is_error": False, "situation": sit, "advice": advice}

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """启动钩子：仅做配置预加载与自检，不启动任何后台任务（纯只读插件）。"""
        try:
            self._load_config()
            civ6_parser.self_check(self._saves_dir)
        except Exception as exc:  # 自检失败不影响宿主启动
            _logger.warning("civ6_buddy startup self-check failed: %s", exc)
        return {"status": "ready", "plugin": "civ6_buddy"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """关闭钩子：无后台资源需释放。"""
        return {"status": "stopped", "plugin": "civ6_buddy"}

    # ══════════ 前置链路：SDK 自动安装与 Tuner 配置（v0.4.0） ══════════

    @llm_tool(
        name="civ6_ensure_sdk",
        description=(
            "自动处理文明6底层通道前置：Steam SDK（FireTuner 服务端）检测/安装 + 游戏启用 Tuner + "
            "探测 4318 端口。何时用：用户说'把底层通道装好/自动装 SDK/为什么读不到城市数据'，"
            "或 civ6_tuner_status/civ6_city_details 报 GAME_TUNER_OFF 时。"
            "参数 action：check（默认，汇总状态）/install（SDK 缺失时触发 Steam 自动安装，"
            "appid 597260）/fix_tuner（自动写 AppOptions.txt 启用 Tuner，含备份）。"
            "返回链路各环节状态与下一步动作。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "check/install/fix_tuner，默认 check"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    async def civ6_ensure_sdk(self, *, action: str = "check", **_):
        params = _tool_params(_)
        action = str(action or params.get("action") or "check").strip().lower()
        if action not in ("check", "install", "fix_tuner"):
            return {"output": "未知 action：可用 check / install / fix_tuner。", "is_error": True,
                    "error": "BAD_ACTION"}
        status = civ6_sdk.sdk_status()
        sdk_ok = status["sdk_installed"]
        tuner_ok = status["tuner_enabled"]
        port_ok = status["port_4318_listening"]

        if action == "fix_tuner":
            r = civ6_sdk.ensure_tuner_enabled()
            if not r["changed"]:
                return {"output": f"Tuner 配置无需修改：{r['detail']}。", "is_error": False,
                        "channel": "sdk", "tuner": r}
            return {
                "output": (f"已启用 Tuner：{r['detail']}。修改文件：{r['path']}。"
                           "请重启游戏生效（同屏只能一个 Tuner 连接，先关掉 SDK 自带的 FireTuner.exe）。"),
                "is_error": False, "channel": "sdk", "tuner": r,
            }

        if action == "install":
            if sdk_ok:
                return {"output": f"SDK 已安装（{status['sdk_dir']}），无需重复下载。"
                                  f"Tuner 配置：{('已启用' if tuner_ok else '未启用')}；"
                                  f"端口 4318：{('监听中' if port_ok else '未监听（需重启游戏）')}。",
                        "is_error": False, "channel": "sdk", "status": status}
            trig = civ6_sdk.trigger_steam_install()
            if not trig["ok"]:
                return {"output": trig["detail"], "is_error": True, "error": "STEAM_TRIGGER_FAILED",
                        "channel": "sdk"}
            return {"output": trig["detail"], "is_error": False,
                    "channel": "sdk", "status": status, "action": "install"}

        # check：汇总
        lines = ["文明6 底层通道（FireTuner/SDK）状态："]
        lines.append(f"- SDK 安装：{'✅ ' + status['sdk_dir'] if sdk_ok else '❌ 未安装（免费 Steam 工具，appid 597260）'}")
        lines.append(f"- 游戏目录：{'✅ ' + status['game_dir'] if status['game_dir'] else '⚠ 未找到（检查 Steam 库路径）'}")
        lines.append(f"- Tuner 配置（{status['app_options_path']}）：{'✅ 已启用' if tuner_ok else '❌ 未启用'}")
        lines.append(f"- 端口 4318：{'✅ 监听中（游戏已开 Tuner）' if port_ok else '❌ 未监听（SDK 未装或游戏未重启）'}")
        lines.append(f"- 游戏进程：{'✅ 运行中' if status['game_running'] else '未运行'}")
        missing = []
        if not sdk_ok:
            missing.append("install（自动触发 Steam 下载 SDK）")
        if not tuner_ok:
            missing.append("fix_tuner（自动启用 Tuner 配置）")
        if not port_ok and sdk_ok:
            missing.append("重启游戏后再探测（SDK 装好、Tuner 开好后需重启游戏）")
        if missing:
            lines.append("下一步：请执行 " + "、".join(f"civ6_ensure_sdk(action='{m.split('（')[0]}')" for m in missing))
        else:
            lines.append("✅ 链路就绪：SDK 已装、Tuner 已开、端口在听，可读真实城市数据。")
        return {"output": "\n".join(lines), "is_error": False, "channel": "sdk", "status": status}

    # ══════════ 进阶版：屏幕自动化运营（推荐制） ══════════

    @llm_tool(
        name="civ6_ops_peek",
        description=(
            "确认文明6 游戏窗口与画面（只读：不点击、不切窗口、不抢焦点）。"
            "用 PrintWindow 直接抓取游戏画面（即使被其他窗口遮挡也能拿到游戏本体）并返回截图。"
            "何时用：用户说'你看看游戏/确认下窗口/截个图看看游戏'。"
            "重要：确认游戏窗口不要用系统截屏——那会截到最上层的聊天/终端窗口；本工具才能拿到真正的游戏画面。"
            "运营执行（点下一回合等）请用 civ6_ops_takeover / civ6_ops_tick，它们会自动定位并前置游戏窗口。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_ops_peek(self, **_):
        try:
            r = civ6_ops.peek_window()
        except Exception as exc:
            return _wrap_err(exc)
        return r

    @llm_tool(
        name="civ6_ops_takeover",
        description=(
            "进入'运营托管'：玩家只负责打仗，YUI 接管本回合运营（推荐制：处理弹窗、选科技、"
            "点下一回合）。何时用：用户说'接管运营/你替我玩/你来全盘操作/运营交给你'。"
            "【硬流程·必须执行】接管成功后，立即（同一次任务内）调用 civ6_ops_tick 且 "
            "loop_turns=20~50 自动连续运营，直到交还/战争/卡住为止；"
            "【严禁调用 steam_manager】运营托管期间禁止调用 steam_manager 任何工具"
            "（含 status/list/capture/run 等一切入口）——游戏局面由 civ6_buddy 提供，"
            "Steam 管理器无需介入；确需 Steam 操作必须先 civ6_ops_handoff 交还。"
            "严禁只接管不运营，严禁回复'你说继续我就点下一回合'后停手等待——用户说'全盘操作'"
            "就是授权你连续运营，不需要每回合都等'继续'。"
            "运营执行会自动定位并前置游戏窗口（PrintWindow 拿游戏画面），不需要玩家手动切到游戏窗口。"
            "参数无。只读+点击游戏内按钮，不改存档。用户/战争打断用 civ6_ops_handoff 交还。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_ops_takeover(self, **_):
        self._load_config()
        self._ops_active = True
        _ops_lock_set()  # 运营锁：steam_manager 等插件见锁让位
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        turn = sit.get("game_turn", 0)
        return {
            "output": (
                f"已接管运营（第 {turn} 回合）。分工规则：运营归我（处理弹窗、选科技、点下一回合），"
                "打仗归你。立即开始连续运营：调用 civ6_ops_tick 带 loop_turns=20 自动推进，"
                "每回合用真实状态汇报在干什么；战争状态或你说'我来打'就立即交还控制权。"
                "严禁调用 steam_manager（运营期间它已让位，调用只会产生无意义回执打断运营）——"
                "游戏局面由 civ6_buddy 的 situation/tuner/tick 提供。"
            ),
            "is_error": False, "active": True, "turn": turn,
            "next_action": (
                "civ6_ops_tick(loop_turns=20)：立即自动连续运营 20 回合，不要等待用户再次喊'继续'；"
                "每回合用 turn_summaries 汇报当前研究/城市/人口状态。"
                "严禁调用 steam_manager 任何工具（其回执会打断运营节奏）；"
                "如确需 Steam 操作，先 civ6_ops_handoff 交还后再调用。"
            ),
        }

    @llm_tool(
        name="civ6_ops_tick",
        description=(
            "运营托管中执行本回合（或连续多回合）运营操作：定位游戏窗口→战争检测"
            "（Tuner 官方 API 优先、像素横幅兜底）→处理弹窗/开局选科技→点下一回合→"
            "Tuner 实时验证回合推进。返回 turn_summaries（每回合真实状态：当前研究科技、"
            "城市人口），汇报时必须引用这些真实状态说明自己在干什么、为什么，"
            "不得只说'点了下一回合'。"
            "何时用：运营托管中，用户说'继续/执行本回合/点下一回合/你一直玩别停/我吃饭你玩/"
            "你全盘操作'。用户明确'全盘操作/一直玩/托管'时必须 loop_turns=20~50 连续运营，"
            "不得只执行 1 回合就停。"
            "参数 loop_turns：连续运营回合数，默认 1；loop_turns>1 时自动循环执行 N 回合，"
            "战争/弹窗无法清除/连续 2 次回合未推进时立即停止并交还控制权。"
            "检测到战争立即停止并交还；每回合用 FireTuner 实时回合数验证确实 +1，"
            "未推进则如实报告并交还。不改存档、不点单位、不碰战斗。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "loop_turns": {
                    "type": "integer",
                    "description": "连续运营回合数（1-50，默认 1；>1 时自动循环，遇战争/弹窗/未推进提前停止）",
                },
            },
            "required": [],
        },
        timeout=300.0,
    )
    async def civ6_ops_tick(self, **kw):
        self._load_config()
        from . import civ6_tuner

        loop_turns = int(kw.get("loop_turns", 1) or 1)
        if loop_turns < 1:
            loop_turns = 1
        if loop_turns > 50:
            loop_turns = 50

        async def _turn_async() -> int | None:
            """实时回合数：Tuner 优先（游戏运行时准确），失败回退存档（可能滞后）。"""
            try:
                r = await civ6_tuner.read_situation(timeout=8.0)
                if r.get("ok"):
                    return int(r.get("turn", 0) or 0)
            except Exception:
                pass
            try:
                return int(civ6_parser.parse_save("", self._saves_dir).get("game_turn", 0))
            except Exception:
                return None

        async def _semantic_line(turn: int) -> str:
            """从 Tuner 读真实游戏状态，拼一句'我现在在干什么'的语义摘要，
            让托管方（Neko）能读懂并汇报，而不是只知道坐标/日志。"""
            bits = [f"回合{turn}"]
            try:
                r = await civ6_tuner.read_situation(timeout=6.0)
                if r.get("ok"):
                    civ = str(r.get("civ") or "")
                    tech = str(r.get("tech") or "")
                    cities = int(r.get("cities", 0) or 0)
                    if civ:
                        bits.append(f"文明{civ}")
                    if tech:
                        bits.append(f"研究《{tech}》")
                    bits.append(f"城市{cities}座")
            except Exception:
                pass
            try:
                c = await civ6_tuner.list_cities(timeout=6.0)
                if c.get("ok"):
                    pops = [
                        f"{x.get('name', '?')}人口{x.get('population', '?')}"
                        for x in c.get("cities", [])[:3]
                    ]
                    if pops:
                        bits.append("，".join(pops))
            except Exception:
                pass
            return "；".join(bits)

        # 战争预检（Tuner 官方 API）
        war_pre = None
        try:
            wr = await civ6_tuner.at_war()
            if wr.get("ok"):
                war_pre = wr.get("at_war", 0) > 0
        except Exception:
            war_pre = None

        t0 = await _turn_async()
        if t0 is None:
            t0 = 0
        logs: list = []
        turns_done = 0
        no_advance = 0
        turn_summaries: list = []
        for i in range(loop_turns):
            try:
                # 同步点击段丢到线程执行：内部含阻塞 sleep/截图/点击，
                # 直接 await 会卡死事件循环（实测 sleep(1s) 被拖到 5.7s）→ 运营"半天不动"。
                result = await asyncio.to_thread(
                    civ6_ops.run_one_turn_ops,
                    {"saves_dir": self._saves_dir, "buttons": self._ops_buttons},
                    war_state=war_pre if i == 0 else None,
                )
            except civ6_ops.OpsError as exc:
                self._ops_active = False
                _ops_lock_clear()
                out = _wrap_err(exc)
                out["handoff"] = True
                out["turns_done"] = turns_done
                return out
            logs += result.get("log", [])
            if result.get("handoff"):
                self._ops_active = False
                _ops_lock_clear()
                return {
                    "output": f"已执行 {turns_done} 回合后停止：{result.get('output', '运营停止')}",
                    "is_error": not result.get("ok", False),
                    "log": logs, "handoff": True,
                    "reason": result.get("reason", ""), "turns_done": turns_done,
                    "turn_summaries": turn_summaries,
                }
            # 回合推进验证：Tuner 实时回合数
            t1 = await _turn_async()
            if t1 is None:
                t1 = t0
            if t1 <= t0:
                no_advance += 1
                if no_advance >= 2:
                    self._ops_active = False
                    _ops_lock_clear()
                    return {
                        "output": (
                            f"已执行 {turns_done} 回合后停止：连续 2 次点击后回合数未推进"
                            f"（{t0} → {t1}）。可能游戏停在等待确认/结算，或按钮坐标失效。"
                            "请看一眼游戏窗口：若画面正常可再说'继续'重试；有弹窗请手动处理。控制权暂交还。"
                        ),
                        "is_error": True, "error": "TURN_NOT_ADVANCED",
                        "log": logs, "handoff": True, "turn_before": t0, "turn_after": t1,
                        "turns_done": turns_done,
                        "turn_summaries": turn_summaries,
                    }
            else:
                no_advance = 0
                turns_done += 1
                t0 = t1
                _ops_lock_set()  # 刷新运营锁保鲜（防 5 分钟 TTL 失效）
                # 语义摘要：让托管方知道每个回合'在干什么'（研究/城市/人口真实状态）
                try:
                    sem = await _semantic_line(t0)
                    if sem:
                        turn_summaries.append(sem)
                        logs.append(f"运营摘要：{sem}")
                except Exception:
                    pass
            # 每回合结算后战争复检（Tuner；战争即交还）
            try:
                wr2 = await civ6_tuner.at_war()
                if wr2.get("ok") and wr2.get("at_war", 0) > 0:
                    self._ops_active = False
                    _ops_lock_clear()
                    return {
                        "output": f"已执行 {turns_done} 回合，Tuner 检测到战争状态，控制权交还给你。",
                        "is_error": False, "handoff": True,
                        "log": logs + [f"结算后 Tuner 战争检测：{wr2.get('at_war')} 个敌对对象"],
                        "turns_done": turns_done,
                        "turn_summaries": turn_summaries,
                    }
            except Exception:
                pass
            if i < loop_turns - 1:
                await asyncio.sleep(0.5)
        self._ops_active = False
        _ops_lock_clear()
        return {
            "output": f"连续运营完成：共执行 {turns_done}/{loop_turns} 回合（回合推进至 {t0}）。"
                      + ("" if turns_done == loop_turns else "部分回合未推进已提前停止。"),
            "is_error": False, "log": logs, "handoff": False,
            "turn_after": t0, "turns_done": turns_done, "turns_target": loop_turns,
            "turn_summaries": turn_summaries,
        }

    @llm_tool(
        name="civ6_ops_citizens",
        description=(
            "精细运营：城市市民/地块分配。何时用：运营托管中，用户说'优化城市人口分配/"
            "让市民去高锤地块/按增长分配'等。参数 target：balanced（综合）/growth（食物优先）/"
            "production（锤子优先），默认 balanced。"
            "执行方式（v0.4.0）：FireTuner 可用时直接读取全部城市真实人口与产出，"
            "然后按目标执行游戏官方焦点分配（市民自动倾斜到目标产出地块，官方 API 级操作，"
            "不点像素、不改存档），并报告执行结果。Tuner 不可用时如实说明并引导装 SDK/开 Tuner。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "description": "分配目标：balanced/growth/production，默认 balanced"},
            },
            "required": [],
        },
        timeout=60.0,
    )
    async def civ6_ops_citizens(self, *, target: str = "balanced", **_):
        params = _tool_params(_)
        target = str(target or params.get("target") or "balanced").strip().lower()
        if target not in ("balanced", "growth", "production"):
            target = "balanced"
        from . import civ6_tuner
        # 1) Tuner 是否可用
        status = await civ6_tuner.tuner_status()
        if not status["ok"]:
            return {
                "output": (
                    f"精细运营（市民/地块分配）需要底层通道：当前 FireTuner 不可用"
                    f"（{status.get('detail', '')}）。请先执行 civ6_ensure_sdk(action='check') 看缺口，"
                    "再按提示 install（自动装 SDK）/ fix_tuner（自动开 Tuner），重启游戏后重试。"
                ),
                "is_error": True, "error": status.get("error", "GAME_TUNER_OFF"),
                "channel": "firetuner", "requires_sdk": True,
            }
        # 2) 读真实城市数据
        cities = await civ6_tuner.list_cities()
        if not cities.get("ok"):
            return {"output": f"读取城市数据失败：{cities.get('detail', '')}",
                    "is_error": True, "error": cities.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner"}
        if not cities["cities"]:
            return {"output": "Tuner 已连接但未读到城市（可能还没进局/本地玩家不存在）。",
                    "is_error": False, "channel": "firetuner"}
        # 3) 映射 target → 官方焦点
        focus_map = {"balanced": "DEFAULT", "growth": "FOOD", "production": "PRODUCTION"}
        focus = focus_map[target]
        r = await civ6_tuner.set_focus(focus)
        if not r["ok"]:
            return {"output": f"设置焦点失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner"}
        applied = {x["city"]: x["focus"] for x in r.get("results", [])}
        lines = ["精细运营执行完成（通道：FireTuner 官方焦点机制）："]
        for c in cities["cities"]:
            y = c["yields"]
            eff = "已应用" if c["name"] in applied else "未收到回执"
            lines.append(
                f"- {c['name']}：人口 {c['population']}，食物 {y.get('food', '?')}，"
                f"生产 {y.get('prod', '?')}，金币 {y.get('gold', '?')}，"
                f"科技 {y.get('science', '?')}，文化 {y.get('culture', '?')}，"
                f"信仰 {y.get('faith', '?')} —— 目标「{target}」（焦点 {focus}）{eff}"
            )
        if target == "production":
            lines.append("市民已自动向高生产地块倾斜；如需恢复均衡执行 target=balanced。")
        elif target == "growth":
            lines.append("市民已自动向高食物地块倾斜（加速人口增长）；如需恢复均衡执行 target=balanced。")
        else:
            lines.append("焦点已清空，恢复系统默认均衡分配。")
        return {"output": "\n".join(lines), "is_error": False,
                "channel": "firetuner", "focus": focus, "applied": applied,
                "cities": cities["cities"]}

    @llm_tool(
        name="civ6_tuner_status",
        description=(
            "检查文明6 FireTuner 底层通道是否可用。何时用：用户问'底层通道/能直接读游戏数据吗'"
            "或精细运营/城市数据读取前先探测。参数无。"
            "需要 Steam 已装 Civ6 SDK（appid 597260）且游戏内启用 Tuner（禁成就），"
            "游戏重启后监听 4318 端口。返回游戏身份与 Lua states 列表或明确错误码。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=20.0,
    )
    async def civ6_tuner_status(self, **_):
        from . import civ6_tuner
        status = await civ6_tuner.tuner_status()
        if not status["ok"]:
            return {
                "output": ("FireTuner 底层通道不可用：请确认 ① Steam 已装『Sid Meier's Civilization "
                           "VI Development Assets』（Tools，appid 597260）；② 游戏内选项开启 "
                           "『Tuner (disables achievements)』（会禁用成就）；③ 重启游戏后端口 4318 "
                           "监听。当前详情：" + str(status.get("detail", ""))),
                "is_error": True, "error": status.get("error", "GAME_TUNER_OFF"),
                "channel": "firetuner",
            }
        return {"output": status["detail"], "is_error": False,
                "app": status.get("app", ""), "states": status.get("states", []),
                "channel": "firetuner"}

    @llm_tool(
        name="civ6_city_details",
        description=(
            "通过 FireTuner 底层通道读取本城全部城市的实时数据（人口 + 食物/生产/金币/科技/文化/信仰）。"
            "何时用：用户问'我的城市现在什么产出/人口多少'，或精细运营前需要真实数据。参数无。"
            "需要 Tuner 可用（见 civ6_tuner_status）；不可用时返回明确错误码。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_city_details(self, **_):
        from . import civ6_tuner
        r = await civ6_tuner.list_cities()
        if not r["ok"]:
            return {"output": f"读取城市数据失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner", "cities": []}
        if not r["cities"]:
            return {"output": "Tuner 已连接但未读到城市（可能还没进局/本地玩家不存在）。",
                    "is_error": False, "channel": "firetuner", "cities": []}
        lines = [f"共 {len(r['cities'])} 座城市："]
        for c in r["cities"]:
            y = c["yields"]
            lines.append(
                f"- {c['name']}：人口 {c['population']}，食物 {y.get('food', '?')}，"
                f"生产 {y.get('prod', '?')}，金币 {y.get('gold', '?')}，"
                f"科技 {y.get('science', '?')}，文化 {y.get('culture', '?')}，信仰 {y.get('faith', '?')}"
            )
        return {"output": "\n".join(lines), "is_error": False,
                "channel": "firetuner", "cities": r["cities"]}

    @llm_tool(
        name="civ6_ops_focus",
        description=(
            "通过 FireTuner 为本城全部城市设置产出焦点（游戏官方市民分配机制：市民自动倾向焦点产出）。"
            "何时用：精细运营中，用户说'城市优先生产/优先增长/把人口往高锤地块放'等。"
            "参数 focus：FOOD（增长）/PRODUCTION（生产）/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT（清空）。"
            "需要 Tuner 可用；这是官方 API 级操作，不点像素、不碰战斗。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus": {"type": "string",
                          "description": "FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT，默认 FOOD"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    async def civ6_ops_focus(self, *, focus: str = "FOOD", **_):
        params = _tool_params(_)
        focus = str(focus or params.get("focus") or "FOOD").strip()
        from . import civ6_tuner
        r = await civ6_tuner.set_focus(focus)
        if not r["ok"]:
            return {"output": f"设置焦点失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner"}
        if not r["results"]:
            return {"output": "焦点已设置但未收到城市回执（可能无本地城市）。",
                    "is_error": False, "channel": "firetuner", "focus": r.get("focus", "")}
        names = "、".join(x["city"] for x in r["results"])
        return {"output": f"已为 {len(r['results'])} 座城市（{names}）设置焦点：{r['focus']}。"
                          "市民会自动向该产出倾斜分配（游戏官方机制）。",
                "is_error": False, "channel": "firetuner",
                "focus": r.get("focus", ""), "results": r["results"]}

    @llm_tool(
        name="civ6_ops_handoff",
        description=(
            "交还运营控制权：退出运营托管（战争开始/用户要自己操作时）。"
            "何时用：用户说'我来打/我自己来/停止托管/交还控制'，或战争检测后。"
            "参数无。只置状态，不操作游戏。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=15.0,
    )
    async def civ6_ops_handoff(self, **_):
        self._ops_active = False
        _ops_lock_clear()
        return {"output": "运营托管已结束，控制权交还给你。需要再托管时说'接管运营'。",
                "is_error": False, "active": False}


@plugin_entry()
def create_plugin(ctx):
    return Civ6BuddyPlugin(ctx)
