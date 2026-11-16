"""civ6_tuner_auto.py — 文明6 自动决策与写操作（从 civ6_tuner 拆出）。

本模块只负责“按真实局面选择并执行不需要玩家战略决策的自动操作”：
生产配额、世界议会处理器、经济/宗教/伟人/商队/间谍/战争响应、万神殿、
使节、单位升级、总督、纪念与政策槽。

底层连接、读局面、写生产/研究、结束回合和协议解析仍在 civ6_tuner.py；
本模块通过 `_t` 引用原门面，测试仍可 monkeypatch `civ6_tuner.execute_lua`
等公共入口，不会形成第二套连接逻辑。
"""

from __future__ import annotations

import asyncio

try:
    from .civ6_lua_resources import load_lua as _lua
except ImportError:  # 包外直接加载（测试/独立脚本）
    from civ6_lua_resources import load_lua as _lua  # type: ignore

class _TunerFacade:
    """延迟解析兼容门面，打断 tuner ↔ auto 的模块初始化环。

    属性仅在函数实际调用时解析；测试对 ``civ6_tuner`` 公共入口的 monkeypatch
    因而仍然生效。直接优先导入本模块也不会触发部分初始化对象。
    """

    def __getattr__(self, name: str):
        if __package__:
            from . import civ6_tuner
        else:  # 包外直接加载（测试/独立脚本）
            import civ6_tuner  # type: ignore
        return getattr(civ6_tuner, name)


_t = _TunerFacade()


__all__ = [
    "auto_decide_production",
    "register_wc_voter",
    "unregister_wc_voter",
    "auto_manage_money",
    "auto_found_religion",
    "auto_recruit_great_person",
    "auto_trade_route",
    "auto_manage_spy",
    "auto_war_response",
    "auto_choose_pantheon",
    "auto_send_envoy",
    "auto_promote_units",
    "auto_appoint_governor",
    "auto_choose_commemoration",
    "auto_fill_policy_slots",
]


_VERIFY_MANUAL = "写请求仅发送一次；结果不确定，可能已生效或消耗资源，请人工核实游戏状态，未自动重试。"


def _verify_unsupported(detail: str) -> dict:
    return {"ok": False, "error": "VERIFY_UNSUPPORTED", "verified": False,
            "detail": f"{detail}；读回 API 不可用。{_VERIFY_MANUAL}"}


def _verify_mismatch(detail: str) -> dict:
    return {"ok": False, "error": "VERIFY_MISMATCH", "verified": False,
            "detail": f"{detail}。{_VERIFY_MANUAL}"}


async def _write_and_verify(
    *,
    request_lua: str,
    verify_lua: str,
    parse_request,
    parse_verify,
    timeout: float,
    operation: str,
    gamecore: bool = False,
) -> dict:
    """写请求只发一次，随后在同一总预算内最多做三次只读验证。

    请求回执只用于取得读回目标，绝不能直接映射为操作成功。读回异常、超时、
    API 不支持或状态不匹配都返回失败/不确定；此函数不会重发写请求。
    """
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    state_index = _t._gamecore_state_index(states) if gamecore else _t._ingame_state_index(states)
    try:
        try:
            requested = await _t.execute_lua(
                reader, writer, request_lua, state_index=state_index, timeout=max(0.1, deadline - loop.time()),
            )
        except _t.TunerError as exc:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "verified": False,
                    "detail": f"{operation}请求通信失败：{exc}。{_VERIFY_MANUAL}"}
        pending = parse_request(requested, app_id)
        if not pending.pop("_verify", False):
            return pending
        for _attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.35, remaining))
            try:
                verified = await _t.execute_lua(
                    reader, writer, verify_lua, state_index=state_index,
                    timeout=max(0.1, deadline - loop.time()),
                )
            except _t.TunerError as exc:
                return {"ok": False, "error": "VERIFY_UNCERTAIN", "verified": False,
                        "detail": f"{operation}读回失败：{exc}。{_VERIFY_MANUAL}"}
            result = parse_verify(verified, pending)
            if result is not None:
                return result
        return {"ok": False, "error": "VERIFY_UNCERTAIN", "verified": False,
                "detail": f"{operation}读回后状态仍不匹配或超过总预算。{_VERIFY_MANUAL}"}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def auto_decide_production(timeout: float = 10.0) -> dict:
    """按真实单位构成选择无需选址的有限配额生产；配额随城市数扩展。

    不自动选择需要城市与地块坐标的区域。早期配额不是"每类各 2 个就停"：
    侦察兵、建造者、勇士的目标随城市数增长（有上限），让连续 20~50 回合运营
    不会在几座城市出完第一批单位后就立刻把生产 blocker 交还。
    返回 {"ok", "kind", "item", "reason", "eco"} 或 NO_REASONABLE_PRODUCTION。
    """
    eco = await _t.read_economy(timeout=timeout)
    if not eco.get("ok"):
        return {"ok": False, "error": eco.get("error", "ECO_FAIL"),
                "detail": eco.get("detail", "读经济失败")}
    pop = int(eco.get("population") or 0)
    cities = max(1, int(eco.get("cities") or 0))
    scouts = int(eco.get("scouts") or 0)
    builders = int(eco.get("builders") or 0)
    warriors = int(eco.get("warriors") or 0)
    # 目标随城市数扩展并设上限：2~4 侦察、2~6 建造者、1~4 勇士。
    scout_target = min(4, max(2, cities + 1))
    builder_target = min(6, max(2, cities * 2))
    warrior_target = min(4, max(1, cities))
    if scouts < scout_target:
        return {"ok": True, "kind": "unit", "item": "UNIT_SCOUT",
                "reason": f"侦察兵{scouts}/{scout_target} → 补侦察探路", "eco": eco}
    if builders < builder_target:
        return {"ok": True, "kind": "unit", "item": "UNIT_BUILDER",
                "reason": f"建造者{builders}/{builder_target} → 补工人改良地块", "eco": eco}
    if pop >= 2 and warriors < warrior_target:
        return {"ok": True, "kind": "unit", "item": "UNIT_WARRIOR",
                "reason": f"勇士{warriors}/{warrior_target} → 补城防", "eco": eco}
    return {"ok": False, "error": "NO_REASONABLE_PRODUCTION", "eco": eco,
            "detail": "侦察/建造者/勇士已按城市规模补足；区域与建筑涉及选址/路线决策，交还玩家选择"}


async def register_wc_voter(timeout: float = 10.0) -> dict:
    """注册世界议会自动投票事件处理器（InGame，Events.WorldCongressStage1）。

    官方机制：世界议会进入投票阶段时（回合推进中触发 WorldCongressStage1 事件），
    处理器自动为当前决议投票并提交（UI.RequestPlayerOperation），不再卡回合。
    投票策略（保守可靠）：全部外交支持票投给决议 A 选项、目标 0（中立偏好）；
    若已有处理器则不重复注册。幂等：无议会/已注册时安全返回。
    返回 {"ok", "registered", "detail"}。
    """
    lua = _lua("register_wc_voter")
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("WC_ALREADY|"):
            return {"ok": True, "app": app_id, "registered": False, "via": "tuner",
                    "detail": "世界议会投票处理器已注册（不重复）"}
        if line.startswith("WC_REGISTERED|"):
            return {"ok": True, "app": app_id, "registered": True, "via": "tuner",
                    "detail": "世界议会自动投票处理器已注册：会议阶段自动投 A 项全部票并提交"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "世界议会注册无回执（Tuner 输出丢失），请重试"}


async def unregister_wc_voter(timeout: float = 3.0) -> dict:
    """注销常驻处理器；每轮最多两次握手，握手、退避、执行共享总预算。"""
    lua = _lua("unregister_wc_voter")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.001, timeout)
    try:
        app_id, states, reader, writer = await _t._handshake_retry(
            retries=1, timeout=max(0.001, deadline - loop.time()),
        )
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return {"ok": False, "error": "HANDOFF_CLEANUP_TIMEOUT", "detail": "注销总预算已在握手阶段耗尽"}
        out = await _t.execute_lua(
            reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=remaining,
        )
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("WC_UNREGISTERED|"):
            return {"ok": True, "app": app_id, "registered": False, "via": "tuner",
                    "detail": "世界议会自动投票处理器已注销" if line.endswith("|ok") else "世界议会处理器本未注册"}
        if line.startswith("WC_UNREGISTER_ERR|"):
            return {"ok": False, "error": "WC_UNREGISTER_FAILED", "detail": line}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "世界议会注销无回执，不能确认已清理"}


async def auto_manage_money(timeout: float = 15.0) -> dict:
    """尝试购买一个建造者；成交必须由单位数与金币余额共同读回确认。"""
    def parse_request(lines, app_id):
        line = next((item.strip() for item in lines if item.strip().startswith("MONEY|")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"购买建造者无请求回执。{_VERIFY_MANUAL}"}
        payload = line[len("MONEY|"):]
        parts = dict(item.split("=", 1) for item in payload.split("|") if "=" in item)
        gold = round(_t._to_float(parts.get("gold", "-1")), 1)
        builders = _t._to_int(parts.get("builders", "0"))
        base = {"app": app_id, "gold": gold, "builders": builders, "via": "tuner"}
        if "UNSUPPORTED" in payload:
            return {**base, "ok": False, "error": "VERIFY_UNSUPPORTED",
                    "detail": "购买前无法读取金币或建造者数，未发送购买请求"}
        if "REQUESTED_BUILDER" not in payload:
            action = "建造者不可购买" if "CANNOT_BUY_BUILDER" in payload else "金币不足或无需购买"
            return {**base, "ok": True, "action": "none", "detail": f"经济自动：{action}，本次未花钱"}
        return {**base, "ok": False, "action": "purchase_pending", "_verify": True,
                "detail": "购买请求已发送，等待读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("MONEY_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("MONEY_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("购买建造者后无法读回金币或单位数")
        if values.get("matched") != "true":
            return None
        gold = round(_t._to_float(values.get("gold", "-1")), 1)
        builders = _t._to_int(values.get("builders", "0"))
        return {**pending, "ok": True, "verified": True, "action": "bought_builder",
                "gold": gold, "builders": builders,
                "detail": f"已读回验证购买建造者：建造者 {pending['builders']} → {builders}，"
                          f"金币 {pending['gold']} → {gold}"}

    return await _write_and_verify(
        request_lua=_lua("auto_manage_money"), verify_lua=_lua("auto_manage_money_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="购买建造者",
    )


async def auto_found_religion(timeout: float = 15.0) -> dict:
    """创立宗教并选择信条；宗教类型与信条列表读回匹配后才报成功。"""
    def parse_request(lines, app_id):
        none = next((item.strip() for item in lines if item.strip().startswith("RELIGION_NONE|")), "")
        if none:
            return {"ok": True, "app": app_id, "founded": False, "via": "tuner",
                    "detail": "已有宗教或当前无可用宗教，本次未创教"}
        line = next((item.strip() for item in lines if item.strip().startswith("RELIGION_OK|")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"创教无请求回执。{_VERIFY_MANUAL}"}
        parts = line[len("RELIGION_OK|"):].split("|")
        return {"ok": False, "app": app_id, "founded": False, "religion": parts[0],
                "beliefs": parts[1:3], "via": "tuner", "_verify": True, "detail": "创教请求已发送，等待读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("RELIGION_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("RELIGION_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("创教后无法读回宗教类型或信条列表")
        if values.get("matched") != "true":
            return None
        return {**pending, "ok": True, "founded": True, "verified": True,
                "detail": f"已读回验证宗教 {pending['religion']} 与信条 "
                          f"{' / '.join(pending['beliefs']) or '默认'}"}

    return await _write_and_verify(
        request_lua=_lua("auto_found_religion"), verify_lua=_lua("auto_found_religion_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="创立宗教",
    )


async def auto_recruit_great_person(timeout: float = 15.0) -> dict:
    """招募伟人；目标从未招募列表消失且读回可用时才确认成功。"""
    def parse_request(lines, app_id):
        line = next((item.strip() for item in lines if item.strip().startswith("GP|")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"伟人招募无请求回执。{_VERIFY_MANUAL}"}
        name = line[len("GP|"):]
        if not name:
            return {"ok": True, "app": app_id, "recruited": False, "name": None,
                    "detail": "无可用伟人，本次未招募", "via": "tuner"}
        if name == "UNSUPPORTED":
            return {"ok": False, "error": "VERIFY_UNSUPPORTED", "detail": "无法读取伟人列表，未发送招募请求"}
        return {"ok": False, "app": app_id, "recruited": False, "name": name,
                "via": "tuner", "_verify": True, "detail": "伟人招募请求已发送，等待读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("GP_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("GP_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("招募伟人后无法读回未招募列表")
        if values.get("matched") != "true":
            return None
        return {**pending, "ok": True, "recruited": True, "verified": True,
                "detail": f"已读回验证伟人招募：{pending['name']}（目标已离开未招募列表）"}

    return await _write_and_verify(
        request_lua=_lua("auto_recruit_great_person"), verify_lua=_lua("auto_recruit_great_person_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="招募伟人",
    )


async def auto_trade_route(timeout: float = 15.0) -> dict:
    """为闲置商队请求贸易路线；以真实出境路线数增量读回确认。"""
    def parse_request(lines, app_id):
        unsupported = next((item for item in lines if item.strip().startswith("TRADE_UNSUPPORTED")), "")
        if unsupported:
            return {"ok": False, "error": "VERIFY_UNSUPPORTED",
                    "detail": "无法读取贸易路线数，未发送商队路线请求"}
        line = next((item.strip() for item in lines if item.strip().startswith("TRADE=")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"商队路线无请求回执。{_VERIFY_MANUAL}"}
        created = [item for item in line[len("TRADE="):].split("|") if item]
        if not created:
            return {"ok": True, "app": app_id, "routes": [], "detail": "无闲置商队或无可达目标，本次未开路线",
                    "via": "tuner"}
        return {"ok": False, "app": app_id, "routes": created, "via": "tuner", "_verify": True,
                "detail": "商队路线请求已发送，等待读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("TRADE_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("TRADE_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("商队请求后无法读回出境路线数")
        if values.get("matched") != "true":
            return None
        return {**pending, "ok": True, "verified": True,
                "before": _t._to_int(values.get("before")), "after": _t._to_int(values.get("after")),
                "detail": f"已读回验证 {len(pending['routes'])} 条贸易路线："
                          f"出境路线数 {values.get('before')} → {values.get('after')}"}

    return await _write_and_verify(
        request_lua=_lua("auto_trade_route"), verify_lua=_lua("auto_trade_route_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="建立贸易路线",
    )


async def auto_manage_spy(timeout: float = 15.0) -> dict:
    """只扫描并报告间谍单位；不派遣任务、不选择逃跑路线、不删除间谍通知。"""
    lua = _lua("auto_manage_spy")
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    if not any(line.strip().startswith("SPY_SCAN|") for line in out):
        return {"ok": False, "error": "NO_RESPONSE", "detail": "间谍扫描无回执，不能确认是否存在间谍"}
    has_spy = any(line.strip().startswith("SPY_FOUND|") for line in out)
    return {"ok": True, "app": app_id, "has_spy": has_spy,
            "detail": f"间谍扫描：{'检测到间谍；仅报告，任务与逃跑路线由玩家决定' if has_spy else '未检测到间谍'}",
            "via": "tuner"}


async def auto_war_response(timeout: float = 15.0) -> dict:
    """战争响应（InGame）——被宣战时评估并报告，不主动宣战。

    边界说明（重要）：主动宣战会永久破坏外交关系、可能引发全面战争，属不可逆决策。
    本函数只读战争与威胁状态并报告，不声称已经替玩家完成防御或移动；由上层立即交还。
    返回 {"ok", "at_war", "threats", "advice", "detail"}。
    """
    war = await _t.at_war(timeout=timeout)
    thr = await _t.read_threats(timeout=timeout)
    if war.get("ok") and war.get("at_war"):
        adv = "检测到战争：停止运营并交还玩家，进攻、防御与议和均由玩家决定。"
        detail = f"检测到战争！{adv}"
        return {"ok": True, "at_war": True,
                "threats": thr.get("units", []) if thr.get("ok") else [],
                "advice": adv, "detail": detail, "via": "tuner"}
    return {"ok": True, "at_war": False,
            "threats": thr.get("units", []) if thr.get("ok") else [],
            "advice": "和平状态，托管继续运营。",
            "detail": f"战争检查：未在战争。{thr.get('detail', '')}", "via": "tuner"}

async def auto_choose_pantheon(timeout: float = 12.0) -> dict:
    """按首都周边地形请求万神殿；以宗教对象中的真实万神殿哈希读回。"""
    def parse_request(lines, app_id):
        line = next((item.strip() for item in lines if item.strip().startswith("RESULT|")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"选择万神殿无请求回执。{_VERIFY_MANUAL}"}
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "ALREADY":
            return {"ok": False, "error": "ALREADY_HAS_PANTHEON", "detail": "已拥有万神殿，本次未重复选择"}
        if tag == "NONE":
            return {"ok": False, "error": "NO_AVAILABLE", "detail": "没有可用万神殿信仰"}
        if tag == "UNSUPPORTED":
            return {"ok": False, "error": "VERIFY_UNSUPPORTED", "detail": "无法读取当前万神殿，未发送选择请求"}
        reason = parts[1] if len(parts) > 1 else ""
        return {"ok": False, "app": app_id, "chosen": tag, "reason": reason, "via": "tuner",
                "_verify": True, "detail": "万神殿请求已发送，等待读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("PANTHEON_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("PANTHEON_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("选择万神殿后无法读回宗教对象")
        if values.get("matched") != "true":
            return None
        return {**pending, "ok": True, "verified": True,
                "detail": f"已读回验证万神殿：{pending['chosen']}（{pending['reason']}）"}

    return await _write_and_verify(
        request_lua=_lua("auto_choose_pantheon"), verify_lua=_lua("auto_choose_pantheon_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="选择万神殿",
    )


async def auto_send_envoy(timeout: float = 12.0) -> dict:
    """向最近城邦请求派遣使节；读回目标城邦使节数与可用令牌消耗。"""
    def parse_request(lines, app_id):
        line = next((item.strip() for item in lines if item.strip().startswith("RESULT|")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"派遣使节无请求回执。{_VERIFY_MANUAL}"}
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NO_TOKENS":
            return {"ok": False, "error": "NO_TOKENS", "detail": "没有可用使节，本次未派遣"}
        if tag == "NONE":
            return {"ok": False, "error": "NO_CANDIDATE", "detail": "没有可派的城邦"}
        if tag == "UNSUPPORTED":
            return {"ok": False, "error": "VERIFY_UNSUPPORTED", "detail": "无法读回城邦使节数，未发送派遣请求"}
        detail = parts[1] if len(parts) > 1 else ""
        return {"ok": False, "app": app_id, "sent_to": tag, "reason": detail,
                "via": "tuner", "_verify": True, "detail": "使节派遣请求已发送，等待读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("ENVOY_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("ENVOY_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("派遣使节后无法读回目标城邦或令牌数")
        if values.get("matched") != "true":
            return None
        return {**pending, "ok": True, "verified": True,
                "detail": f"已读回验证向 {pending['sent_to']} 派遣使节："
                          f"目标使节 {values.get('envoys_before')} → {values.get('envoys_after')}，"
                          f"可用令牌 {values.get('tokens_before')} → {values.get('tokens_after')}"}

    return await _write_and_verify(
        request_lua=_lua("auto_send_envoy"), verify_lua=_lua("auto_send_envoy_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="派遣使节",
    )


async def auto_promote_units(timeout: float = 15.0) -> dict:
    """请求单位升级；逐单位读回晋升与剩余升级点，失败不忽略。"""
    def parse_request(lines, app_id):
        line = next((item.strip() for item in lines if item.strip().startswith("RESULT|")), "")
        if not line:
            return {"ok": False, "error": "WRITE_UNCERTAIN", "detail": f"单位升级无请求回执。{_VERIFY_MANUAL}"}
        payload = line[len("RESULT|"):]
        if not payload.strip():
            return {"ok": True, "app": app_id, "promoted": [], "detail": "没有需要升级的单位", "via": "tuner"}
        items = [item for item in payload.split(";;") if item.strip()]
        requested = [item.split("|", 1)[0] for item in items if "|requested=true" in item]
        skipped = [item for item in items if item.endswith("→NONE")]
        if not requested:
            return {"ok": False, "error": "NO_AVAILABLE", "promoted": [], "skipped": skipped,
                    "detail": "有升级点但没有可用升级，未宣称升级成功"}
        return {"ok": False, "app": app_id, "promoted": requested, "skipped": skipped,
                "via": "tuner", "_verify": True, "detail": "单位升级请求已发送，等待逐单位读回"}

    def parse_verify(lines, pending):
        line = next((item.strip() for item in lines if item.strip().startswith("PROMOTION_VERIFY|")), "")
        if not line:
            return None
        values = dict(item.split("=", 1) for item in line[len("PROMOTION_VERIFY|"):].split("|") if "=" in item)
        if values.get("supported") != "true":
            return _verify_unsupported("单位升级后无法读回晋升或升级点")
        if values.get("matched") != "true":
            return None
        if pending.get("skipped"):
            return _verify_mismatch(f"部分单位仍无可确认升级：{'、'.join(pending['skipped'])}")
        return {**pending, "ok": True, "verified": True,
                "detail": f"已逐单位读回验证 {len(pending['promoted'])} 个升级："
                          f"{'、'.join(pending['promoted'])}；剩余升级点已核对"}

    return await _write_and_verify(
        request_lua=_lua("auto_promote_units"), verify_lua=_lua("auto_promote_units_verify"),
        parse_request=parse_request, parse_verify=parse_verify, timeout=timeout, operation="单位升级", gamecore=True,
    )


async def auto_appoint_governor(timeout: float = 12.0) -> dict:
    """自动任命并分配总督（InGame APPOINT_GOVERNOR + ASSIGN_GOVERNOR）。

    用于 ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT（"可以任命一位总督"）。
    选择第一个尚未任命的总督类型，任命后自动分配到首都（收益确定性最高）。
    总督是对内治理（产能/科技/忠诚加成），不改变玩家战略方向，自动处理安全。
    返回 {"ok", "governor", "city"}。
    """
    lua = _lua("auto_appoint_governor")
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
        result_line = next((line.strip() for line in out if line.strip().startswith("RESULT|")), "")
        if not result_line:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "任命总督无回执（Tuner 输出丢失），请重试"}
        parts = result_line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NO_POINT":
            return {"ok": False, "error": "NO_POINT", "detail": "没有可用总督任命点数（可能刚用过）"}
        if tag == "ALL_APPOINTED":
            return {"ok": False, "error": "ALL_APPOINTED", "detail": "所有总督都已任命"}
        detail = parts[1] if len(parts) > 1 else ""
        verify_lua = _lua("auto_appoint_governor_verify")
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.4, remaining))
            verified = await _t.execute_lua(
                reader, writer, verify_lua, state_index=_t._ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("GOV|")), "")
            if "found=true" in line:
                return {"ok": True, "app": app_id, "governor": tag, "verified": True,
                        "detail": f"已读回验证总督任命：{detail}", "via": "tuner"}
        return {"ok": False, "error": "SILENT_FAILURE",
                "detail": "任命总督后未读回任命列表，未宣称成功"}
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def auto_choose_commemoration(timeout: float = 12.0) -> dict:
    """自动选择时代纪念（黄金时代致敬）（InGame COMMEMORATE）。

    用于 ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE（"可以选择一个纪念"）。
    选择第一个可用的纪念（GetPlayerCommemorateChoices），纪念是时代增益，
    不改变战略方向，自动处理安全。返回 {"ok", "commemoration"}。
    """
    lua = _lua("auto_choose_commemoration")
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
        result_line = next((line.strip() for line in out if line.strip().startswith("RESULT|")), "")
        if not result_line:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "选择纪念无回执（Tuner 输出丢失），请重试"}
        parts = result_line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NONE":
            return {"ok": False, "error": "NONE", "detail": "当前没有可选纪念"}
        verify_lua = _lua("auto_choose_commemoration_verify")
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.4, remaining))
            verified = await _t.execute_lua(
                reader, writer, verify_lua, state_index=_t._ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("COMM|")), "")
            values = {part.split("=")[0]: part.split("=", 1)[1]
                      for part in line[len("COMM|"):].split("|") if "=" in part} if line else {}
            allowed, before = _t._to_int(values.get("allowed", "-1")), _t._to_int(values.get("before", "-1"))
            if (before >= 0 and allowed < before) or values.get("still") == "false":
                return {"ok": True, "app": app_id, "commemoration": tag, "verified": True,
                        "detail": f"已读回验证纪念选择：{tag}", "via": "tuner"}
        return {"ok": False, "error": "SILENT_FAILURE",
                "detail": "选择纪念后未读回次数/可选列表变化，未宣称成功"}
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def auto_fill_policy_slots(timeout: float = 12.0) -> dict:
    """自动填满空政策槽位（InGame，官方 UNLOCK_POLICIES + RequestPolicyChanges 三步）——局面感知。

    用于 ENDTURN_BLOCKING_FILL_CIVIC_SLOT（"填补政策槽位"）：市政完成/政府变更后
    出现空槽会挡住结束回合。**选择逻辑按局面打分**（不是机械填第一个）：
    读人口/金币/侦察兵/勇士数 → PolicyType 白名单权重（城市规划+1产=最优先；
    神王+1金=金币紧张时高；勘察+1移动=有侦察兵时高；纪律+15战力=有勇士时中；
    农业+1粮=人口少时高）→ 每空槽取"槽型匹配且分数最高"的卡。
    返回 {"ok", "filled", "slots", "picked"}（picked=["POLICY_X=理由", ...]）。
    """
    lua = _lua("auto_fill_policy_slots")
    try:
        app_id, states, reader, writer = await _t._handshake_retry()
    except _t.TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout)
    try:
        out = await _t.execute_lua(reader, writer, lua, state_index=_t._ingame_state_index(states), timeout=timeout)
        request_line = next((line for line in out if line.strip().startswith("REQUESTED=")), "")
        if not request_line:
            return {"ok": False, "error": "NO_RESPONSE", "detail": "填政策槽无回执（Tuner 输出丢失），请重试"}
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in request_line.strip().split("&") if "=" in p}
        picked = kv.get("SLOTS", "").split("|") if kv.get("SLOTS") else []
        requested = _t._to_int(kv.get("REQUESTED", "0"))
        if requested == 0:
            return {"ok": True, "app": app_id, "filled": 0, "requested": 0, "picked": [],
                    "detail": "没有可填的空政策槽（或没有可用政策）", "via": "tuner"}

        # 写请求只发一次；后续只读回槽位内容，避免把“请求数”当成已填数。
        verify_lua = _lua("auto_fill_policy_slots_verify").format(p0=requested)
        last_detail = "政策槽设置后未读回目标政策"
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))
            verified = await _t.execute_lua(
                reader, writer, verify_lua, state_index=_t._ingame_state_index(states),
                timeout=max(0.1, deadline - loop.time()),
            )
            line = next((entry for entry in verified if entry.startswith("POLICY|")), "")
            values = {part.split("=")[0]: part.split("=", 1)[1]
                      for part in line[len("POLICY|"):].split("|") if "=" in part} if line else {}
            done, failed = _t._to_int(values.get("done")), _t._to_int(values.get("failed"))
            if done == requested and failed == 0:
                return {"ok": True, "app": app_id, "filled": done, "requested": requested,
                        "picked": picked, "verified": True,
                        "detail": f"已读回验证 {done} 个政策槽（{'、'.join(picked) or '无'}）",
                        "via": "tuner"}
            last_detail = f"第 {attempt + 1} 次读回未匹配：成功 {done}，失败 {failed}"
        return {"ok": False, "error": "SILENT_FAILURE", "detail": last_detail + "；未宣称填写成功"}
    except _t.TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
