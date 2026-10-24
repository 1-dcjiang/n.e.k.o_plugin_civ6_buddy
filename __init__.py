"""文明6陪玩插件（civ6_buddy）— 让 YUI 陪玩家打文明6。

深度监控：解析 .Civ6Save 存档，获知当前局面（回合/速度/地图/玩家文明/
敌对文明/城邦/MOD），基于局面给陪玩建议（文明特性 + 对手 + 早期策略模板）。
运行依赖：本机 node（>=16）+ 插件内嵌 civ6_save_parser（已随插件打包）。

只读操作：不修改存档、不写游戏文件。存档目录可在 config 覆盖。
"""

from __future__ import annotations

import logging
from pathlib import Path

from plugin.sdk.plugin import NekoPluginBase, lifecycle, llm_tool, neko_plugin, plugin_entry

from . import civ6_data, civ6_ops, civ6_parser

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_buddy")

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
        lines += _advice_lines(sit, topic)
        return {"output": "\n".join(lines), "is_error": False, "situation": sit, "advice": _advice_lines(sit, topic)}

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

    # ══════════ 进阶版：屏幕自动化运营（推荐制） ══════════

    @llm_tool(
        name="civ6_ops_takeover",
        description=(
            "进入'运营托管'：玩家只负责打仗，YUI 接管本回合运营（推荐制：处理弹窗、点下一回合）。"
            "何时用：用户说'接管运营/你替我点下一回合/运营交给你'。"
            "参数无。只读+点击游戏内按钮，不改存档。之后每回合用 civ6_ops_tick 执行，"
            "用户/战争打断用 civ6_ops_handoff 交还。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_ops_takeover(self, **_):
        self._load_config()
        self._ops_active = True
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        turn = sit.get("game_turn", 0)
        return {
            "output": (
                f"已接管运营（第 {turn} 回合）。分工规则：运营归我（处理弹窗、点下一回合、"
                "生产建议），打仗归你。每回合你说'继续'我就执行本回合；战争状态或你说"
                "'我来打'就立即交还控制权。"
            ),
            "is_error": False, "active": True, "turn": turn,
        }

    @llm_tool(
        name="civ6_ops_tick",
        description=(
            "执行一回合推荐制运营：定位游戏窗口→处理弹窗→点下一回合→验证结算与回合推进。"
            "何时用：运营托管中，用户说'继续/执行本回合/点下一回合'。"
            "参数无。检测到战争横幅立即停止并交还控制权；点完下一回合会解析存档验证回合数"
            "确实 +1，未推进则如实报告并交还。不改存档、不点单位、不碰战斗。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=120.0,
    )
    async def civ6_ops_tick(self, **_):
        self._load_config()
        # 回合推进验证：执行前回合数
        try:
            t0 = int(civ6_parser.parse_save("", self._saves_dir).get("game_turn", 0))
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        try:
            result = civ6_ops.run_one_turn_ops(
                {"saves_dir": self._saves_dir, "buttons": self._ops_buttons})
        except civ6_ops.OpsError as exc:
            if self._ops_active:
                self._ops_active = False
            out = _wrap_err(exc)
            out["handoff"] = True
            return out
        if result.get("handoff"):
            self._ops_active = False
            return {"output": result.get("output", "运营停止"), "is_error": not result.get("ok", False),
                    "log": result.get("log", []), "handoff": True}
        # 验证回合推进：执行后回合数
        try:
            t1 = int(civ6_parser.parse_save("", self._saves_dir).get("game_turn", 0))
        except civ6_parser.Civ6Error:
            t1 = t0
        if t1 <= t0:
            self._ops_active = False
            return {
                "output": (
                    f"已点击下一回合并等待结算，但存档回合数未推进（{t0} → {t1}）。"
                    "可能游戏停在等待确认或结算未完成。请看一眼游戏窗口：若画面正常可再说'继续'重试；"
                    "若有弹窗请手动处理。控制权暂交还给你。"
                ),
                "is_error": True, "error": "TURN_NOT_ADVANCED",
                "log": result.get("log", []), "handoff": True, "turn_before": t0, "turn_after": t1,
            }
        return {"output": f"本回合运营完成（回合 {t0} → {t1}）：{result.get('output', '')}",
                "is_error": False, "log": result.get("log", []), "handoff": False,
                "turn_before": t0, "turn_after": t1}

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
        return {"output": "运营托管已结束，控制权交还给你。需要再托管时说'接管运营'。",
                "is_error": False, "active": False}


def _advice_lines(sit: dict, topic: str) -> list[str]:
    """局面 → 规则化建议。基于玩家文明特性/对手/城邦/回合阶段，模板式输出。"""
    out: list[str] = []
    humans = sit.get("humans") or []
    ais = sit.get("ais") or []
    css = sit.get("city_states") or []
    turn = sit.get("game_turn") or 0
    t = (topic or "").strip()

    my = humans[0] if humans else None
    ua = civ6_data.civ_ua(my["id"]) if my else ""

    phase = "远古-古典（前50回合）" if turn <= 50 else ("中世纪（50-100）" if turn <= 100 else "后期")

    if t and t not in ("开局", "科技", "建城", "战争", "胜利", "外交", "综合"):
        out.append(f"主题「{t}」未收录，以下为综合建议：")

    # 通用框架
    out.append(f"【阶段】{phase}，第 {turn} 回合。")
    if my:
        out.append(f"【你的文明】{my['name']}（{my['leader']}）：{ua or '特性资料待补充'}")

    if "战争" in t or "军事" in t:
        if my and "征服" in ua or (my and "军事" in (ua or "")):
            out.append("· 你的文明适合军事扩张：优先造兵、发展军事区，考虑早战宣战最近的邻国。")
        else:
            out.append("· 当前以防御为主：每城至少留 1-2 单位驻防，遇到军力强的邻居保持友好。")
        out.append(f"· 已知对手 {len(ais)} 个：{'、'.join(a['name'] for a in ais)}。若邻居武力强（如祖鲁/斯基泰），别主动挑衅。")
    elif "科技" in t or "科技" in str(topic):
        out.append("· 科技线参考：远古优先 采矿→陶器→写作（学院前置）；古典补 青铜器→建筑学。")
        out.append("· 有学院加成倾向（如韩国/巴比伦）的文明优先铺学院；否则按胜利路线定科技。")
    elif "建城" in t or "铺城" in t:
        out.append("· 铺城参考：前 3 城优先抢占奢侈资源/河流/丘陵产能；城距 4-6 格避免互相挤占。")
        out.append("· 抢地盘要快：AI 也会扩张，看到好位置别犹豫。")
    elif "胜利" in t or "胜利路线" in t:
        if my and "文化" in ua:
            out.append("· 你的文明倾向文化胜利：多建剧院广场、抢巨作、后期补旅游业绩。")
        elif my and "宗教" in ua:
            out.append("· 你的文明倾向宗教胜利：抢万神殿、建圣坛传教、防止敌对宗教入侵。")
        else:
            out.append("· 参考三条路线：科技（学院+后期太空）/文化（剧院+巨作）/征服（军事滚雪球）。当前回合选一条主线走。")
    elif "外交" in t:
        out.append(f"· 城邦（{len(css)} 个）是外交资源：早期派使者，优先结盟对本文明加成最大的类型。")
        out.append("· AI 性格各异，战争狂邻居（如希腊戈尔戈）注意军备威慑，和平型可发展贸易。")
    elif "开局" in t or not t:
        out.append("· 开局三件事：探图（侦察兵/勇士向外探视野）、抢好地块（奢侈品+河流）、攒产能铺首都。")
        out.append(f"· 城邦有 {len(css)} 个：派使者结盟最有利的（参考各城邦类型）。")
        out.append("· 蛮族营地会刷：看到营地就清，别让蛮族骚扰商路。")

    out.append("· （以上为规则化参考，具体请结合 YUI 与你的实际情况一起判断。）")
    return out


@plugin_entry()
def create_plugin(ctx):
    return Civ6BuddyPlugin(ctx)
