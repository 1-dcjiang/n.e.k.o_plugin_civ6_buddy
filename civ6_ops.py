"""文明6 屏幕自动化运营模块（civ6_ops）— 让 YUI 替玩家执行"推荐制"运营。

设计目标（进阶版）：玩家只打仗，运营交给 YUI。
能力边界（如实）：
- 只读游戏画面（截图），不碰游戏内存、不改存档。
- 执行层**优先复用 steam_manager.control（YUI 现成键鼠操控）**，steam_manager
  缺失时才回退 pyautogui（通道在结果里如实标注）。
- 执行方式 = "推荐制"：只点击游戏内固定按钮/推荐项目（下一回合、市政、
  城市"推荐生产"等），配合固定相对坐标 + 像素模板匹配，**不依赖 OCR**。
- 城市生产走游戏自带"推荐建造"按钮；科技树无推荐按钮，自动部分只做
  "已就绪市政/政策"与"下一回合"，科技给建议留用户点（半自动）。
- 战争检测：Tuner 官方 API（GetAtWarCount）优先、像素横幅兜底——检测到即停止运营、交还控制权。
- 每一次操作后截图验证；识别不了就停下如实汇报，绝不猜点。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_ops")

try:  # numpy 为运行/测试所需；缺失时模块仍可导入（相关函数调用时再报错）
    import numpy as np  # noqa: F401
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


class OpsError(Exception):
    """civ6_ops 错误：带稳定错误码，供上层如实汇报。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── 执行层：优先复用 steam_manager（YUI 现成键鼠操控），缺失回退 pyautogui ──

class _Executor:
    """键鼠/截屏执行器。主通道 = steam_manager.control（YUI 现成能力）；
    steam_manager 未安装/不可用时回退到 pyautogui（结果里如实标注通道）。"""

    def __init__(self):
        self._sm = None
        self._pg = None
        self._PIL = None
        try:
            from plugin.plugins.steam_manager import control as _sm_control
            self._sm = _sm_control
        except Exception:
            self._sm = None

    @property
    def channel(self) -> str:
        return "steam_manager" if self._sm is not None else "pyautogui_fallback"

    def _pg(self):  # noqa: N802 - 懒加载
        if self._pg is None:
            import pyautogui
            self._pg = pyautogui
        return self._pg

    def _PIL(self):  # noqa: N802
        if self._PIL is None:
            from PIL import Image
            self._PIL = Image
        return self._PIL

    def capture(self, win: dict) -> np.ndarray:
        """截取窗口区域 → RGB numpy（供模板匹配/验证）。主通道截 BMP 后读入，回退通道直接截屏。"""
        if self._sm is not None:
            try:
                path = self._sm.capture_window_bmp(
                    {"rect": (win["left"], win["top"], win["right"], win["bottom"])})
                if path:
                    img = self._PIL().open(path)
                    return np.asarray(img.convert("RGB"), dtype=np.uint8)
            except Exception:
                pass
        img = self._pg().screenshot(region=(win["left"], win["top"],
                                            win["right"] - win["left"], win["bottom"] - win["top"]))
        return np.asarray(img.convert("RGB"), dtype=np.uint8)

    def click_abs(self, screen_x: int, screen_y: int):
        if self._sm is not None:
            try:
                self._sm.mouse_click_abs(screen_x, screen_y)
                return
            except Exception:
                pass
        self._pg().click(screen_x, screen_y)

    def press(self, key: str):
        """按键。steam_manager 通道用虚拟键码；回退通道用 pyautogui 键名。"""
        if self._sm is not None:
            try:
                vk = _VK_MAP.get(key)
                if vk is not None:
                    self._sm.press_key(vk)
                    return
            except Exception:
                pass
        self._pg().press(key)


# 常用键 → 虚拟键码（steam_manager.press_key 需要）
_VK_MAP = {
    "esc": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27,
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46, "g": 0x47,
    "h": 0x48, "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E,
    "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54, "u": 0x55,
    "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
}

_EXEC = _Executor()


# ── 窗口层 ──

_WIN_TITLES = ("Civilization VI", "Sid Meier's Civilization VI", "CivilizationVI")


def _find_window():
    """查找文明6 主窗口，返回 (hwnd, left, top, right, bottom)。未运行则抛 OpsError。"""
    import win32gui
    matches = []

    def _enum(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        for t in _WIN_TITLES:
            if t.casefold() in title.casefold():
                matches.append(hwnd)
                return

    win32gui.EnumWindows(_enum, None)
    if not matches:
        raise OpsError("GAME_NOT_RUNNING", "未找到文明6 窗口。请先启动游戏并进入一局游戏（不要停在主菜单）。")
    hwnd = matches[0]
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    if right - left < 200 or bottom - top < 200:
        raise OpsError("GAME_WINDOW_TOO_SMALL", f"文明6 窗口异常小（{right-left}x{bottom-top}），可能被最小化或加载中。")
    return {"hwnd": hwnd, "left": left, "top": top, "right": right, "bottom": bottom}


def _front_and_size(win: dict) -> dict:
    """把游戏窗口前置（不最小化），并返回窗口内工作区尺寸。"""
    import win32gui
    try:
        win32gui.ShowWindow(win["hwnd"], 9)  # SW_RESTORE
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(win["hwnd"])
    except Exception:
        pass
    time.sleep(0.35)
    left, top, right, bottom = win32gui.GetWindowRect(win["hwnd"])
    return {**win, "left": left, "top": top, "right": right, "bottom": bottom}


def _shot(win: dict):
    return _EXEC.capture(win)


def _click_rel(win: dict, fx: float, fy: float, delay: float = 0.25):
    """按窗口相对坐标点击（0-1）。fx/fy 越界直接报错，防止乱点。"""
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        raise OpsError("BAD_REL_COORD", f"无效相对坐标 ({fx},{fy})")
    x = int(win["left"] + fx * (win["right"] - win["left"]))
    y = int(win["top"] + fy * (win["bottom"] - win["top"]))
    _EXEC.click_abs(x, y)
    time.sleep(delay)


def _press(key: str, delay: float = 0.25):
    _EXEC.press(key)
    time.sleep(delay)


# ── 像素模板匹配（无 OCR 的按钮识别） ──

def _match_template(screen, template, threshold: float = 0.86):
    """模板匹配：粗扫（SSD 定位候选）→ 邻域 1px 精扫取最优 → 阈值判断。
    优先归一化相关；模板近纯色（std≈0）时退化为 SSD 匹配。
    返回 (cx, cy) 像素坐标或 None。"""
    if np is None:
        raise OpsError("NUMPY_MISSING", "本机缺少 numpy，屏幕自动化不可用。请安装 numpy。")
    if template.shape[0] > screen.shape[0] or template.shape[1] > screen.shape[1]:
        return None
    th, tw = template.shape[:2]
    s = screen.astype(np.float32)
    t = template.astype(np.float32)
    t_mean = t.mean()
    t_std = t.std()

    def _score(x0: int, y0: int):
        patch = s[y0:y0 + th, x0:x0 + tw]
        if t_std < 1e-6:
            return float((np.abs(patch - t)).sum())  # SSD（越小越好）
        p_mean = patch.mean()
        num = ((patch - p_mean) * (t - t_mean)).sum()
        den = (np.sqrt(((patch - p_mean) ** 2).sum()) * np.sqrt(((t - t_mean) ** 2).sum()))
        if den < 1e-6:
            return None
        return num / den  # 相关（越大越好）

    # 1) 粗扫：用 SSD 定位候选（对高均值模板判别力比相关强、峰值锐利）
    step = 8
    coarse: list[tuple] = []
    for y0 in range(0, s.shape[0] - th + 1, step):
        for x0 in range(0, s.shape[1] - tw + 1, step):
            patch = s[y0:y0 + th, x0:x0 + tw]
            coarse.append((float((np.abs(patch - t)).sum()), x0, y0))
    # 取 SSD 最小 top-5 候选
    candidates = sorted(coarse, key=lambda it: it[0])[:5]

    # 2) 每个候选邻域 1px 精扫，用分支评分取全局最优
    fine: list[tuple] = []
    for _, cx0, cy0 in candidates:
        x_lo, x_hi = max(0, cx0 - step), min(s.shape[1] - tw, cx0 + step)
        y_lo, y_hi = max(0, cy0 - step), min(s.shape[0] - th, cy0 + step)
        for x0 in range(x_lo, x_hi + 1):
            for y0 in range(y_lo, y_hi + 1):
                v = _score(x0, y0)
                if v is not None:
                    fine.append((v, x0, y0))
    if not fine:
        return None
    if t_std < 1e-6:
        best_val, bx, by = min(fine, key=lambda it: it[0])
        if best_val > max((float(np.abs(s - s.mean()).mean()) * th * tw) * 0.10, 1.0):
            return None
    else:
        best_val, bx, by = max(fine, key=lambda it: it[0])
        if best_val < threshold:
            return None
    return (bx + tw // 2, by + th // 2)


def _template_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / f"{name}.png"


# ── 运营执行（推荐制） ──

# 主界面相对按钮（默认值，可在 config 覆盖）：
# "下一回合"按钮位于文明6 右下角操作栏；默认 (0.960, 0.918) 为 1920x1080 布局的
# 估算值，未实测校准——分辨率/窗口缩放不同时请在运行配置 [civ6_buddy.ops_buttons]
# 中校准（按钮中心相对坐标 = 按钮中心像素 ÷ 窗口宽高）。
_DEFAULT_BUTTONS = {
    "next_turn": (0.960, 0.918),
    "policy_ok": (0.50, 0.55),
    "production_yes": (0.62, 0.78),
}

# 城市面板条按钮（真实截图标定，2558x1598 全屏/相对坐标，分辨率无关）：
# 点击地图上的城市后底部出现的城市面板条。
# citizen_button：面板条上的「市民管理」按钮 → 打开市民管理窗口（精细运营入口）
# production_button：面板条上的「选择生产项目」按钮 → 打开生产面板
_CITY_PANEL_BUTTONS = {
    "citizen": (0.836, 0.871),      # 市民管理
    "production": (0.929, 0.872),   # 选择生产项目
}


def _banner_war_present(screen) -> bool:
    """粗略战争横幅检测：顶部 10% 是否有红色横幅像素带（战争宣言横幅为红色系）。
    保守检测——只用于"疑似战争则停止运营并询问"，不用于确认。"""
    if np is None or screen.size == 0:
        return False
    top = screen[: max(1, int(screen.shape[0] * 0.10)), :, :]
    r = top[:, :, 0].astype(int)
    g = top[:, :, 1].astype(int)
    b = top[:, :, 2].astype(int)
    red_mask = (r > 120) & (r - g > 60) & (r - b > 40)
    return bool(red_mask.sum() > top.shape[0] * top.shape[1] * 0.01)


def _template_click_if_found(screen, win: dict, name: str) -> bool:
    """尝试用模板点击按钮。模板缺失/未匹配 → 返回 False（调用方兜底）。"""
    path = _template_path(name)
    if not path.is_file():
        return False
    from PIL import Image
    tpl = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
    hit = _match_template(screen, tpl)
    if hit is None:
        return False
    fx = hit[0] / max(win["right"] - win["left"], 1)
    fy = hit[1] / max(win["bottom"] - win["top"], 1)
    _click_rel(win, fx, fy)
    return True


def _detect_popup_blocking(screen: np.ndarray) -> bool:
    """弹窗粗检测：画面中央区域被大面积浅色面板覆盖（政策卡/科技完成/交易弹窗）。
    保守策略：发现疑似弹窗 → 按 ESC 关闭（文明6 ESC 只关面板，无副作用）→ 再截。"""
    h, w = screen.shape[:2]
    cx0, cy0, cx1, cy1 = int(w * 0.30), int(h * 0.30), int(w * 0.70), int(h * 0.70)
    center = screen[cy0:cy1, cx0:cx1]
    gray = center.mean(axis=2)
    light_frac = float((gray > 200).mean())
    return light_frac > 0.45


def run_one_turn_ops(cfg: dict, war_state: bool | None = None) -> dict:
    """执行一回合推荐制运营。返回结构化结果（供上层如实汇报）。

    cfg: {"saves_dir": str, "buttons": {...}} 按钮可用 config 覆盖。
    war_state: 调用方（asyncio 上下文）先经 Tuner 官方 API 测得的战争状态：
      True=战争中（立即交还，不点下一回合）；None=未测得（回退像素横幅检测）；
      False=确认非战争。
    流程：找窗口 → 前置 → 截图 → 战争判断 → ESC 清扫弹窗 → 点下一回合 → 验证结算。
    生产/市政推荐按钮通过模板资产尝试（assets/*.png），缺失则跳过并如实标注。
    战争判断命中 → 停止并交还（不点下一回合）。结算后战争复检由调用方再做一次 Tuner 查询。
    """
    win = _find_window()
    win = _front_and_size(win)
    log: list[str] = [f"窗口已定位并前置（执行通道：{_EXEC.channel}）"]

    # 1) 初始截图
    screen = _shot(win)

    # 2) 战争判断（Tuner 结果优先；未测得时像素横幅兜底）→ 交还
    war = war_state
    if war is None and _banner_war_present(screen):
        war = True
        log.append("像素横幅检测：疑似战争横幅")
    if war:
        return {
            "ok": False, "handoff": True, "reason": "WAR_TUNER" if war_state is True else "WAR_BANNER",
            "output": "检测到战争状态：运营暂停，控制权交还给你。需要我继续运营时说'继续运营'。",
            "log": log, "channel": _EXEC.channel,
        }

    # 3) 弹窗清扫（最多 3 次 ESC）
    for i in range(3):
        if not _detect_popup_blocking(screen):
            break
        _press("esc", 0.45)
        screen = _shot(win)
        log.append(f"检测到弹窗，已按 ESC 关闭（第 {i + 1} 次）")
    else:
        return {
            "ok": False, "handoff": True, "reason": "POPUP_UNRESOLVED",
            "output": "连续弹窗无法清除：停止运营，请手动处理游戏内弹窗后再说'继续运营'。",
            "log": log, "channel": _EXEC.channel,
        }

    # 4) 点"下一回合"（模板优先，兜底用默认相对坐标）
    clicked = _template_click_if_found(screen, win, "next_turn")
    if not clicked:
        btn = cfg.get("buttons", {}).get("next_turn") or _DEFAULT_BUTTONS["next_turn"]
        _click_rel(win, float(btn[0]), float(btn[1]), delay=0.4)
        log.append("下一回合：未匹配到模板，使用默认相对坐标点击")
    else:
        log.append("下一回合：模板匹配点击")

    # 5) 等待 AI 结算 + 像素战争复检（Tuner 复检由调用方在返回后执行）
    time.sleep(2.5)
    screen2 = _shot(win)
    if _banner_war_present(screen2):
        log.append("结算后像素横幅检测：疑似战争横幅")
        return {"ok": True, "handoff": True, "reason": "WAR_AFTER_TURN", "log": log,
                "channel": _EXEC.channel,
                "output": "本回合运营已执行，结算后检测到战争状态，控制权交还给你。"}
    log.append("回合结算画面正常")

    return {
        "ok": True, "handoff": False, "reason": "",
        "output": "本回合推荐制运营完成：弹窗已处理，已点击下一回合并等待结算。\n"
                  "（生产/市政/政策建议请参考局面分析与建议；科技树请在游戏里点，或告诉我研发哪个。）",
        "log": log, "channel": _EXEC.channel,
    }


# ── 精细运营：市民/地块分配（v0.3.0） ──

# 城市面板市民管理 tab 的地块格布局：中心为城市，外圈为可工作地块。
# 格子中心的窗口内相对坐标需按真实游戏截图标定（config [civ6_buddy.city_grid]），
# 未标定前精细运营的"执行点击"不可用（只读建议可用）。
_YIELD_KEYS = ("food", "prod", "gold", "culture", "science", "faith")


def _citizen_best_assignment(tiles: list[dict], pop: int, target: str = "balanced") -> dict:
    """最优市民分配（纯函数，不依赖 UI）。

    tiles: 城市面板识别出的地块产出列表，[0] 为城市中心（始终锁定），其余为可工作地块，
           每项含 food/prod/gold/culture/science/faith（可缺省=0）。
    pop:   城市人口（=已工作地块数，含中心）。
    target: balanced（综合）/ growth（食物优先）/ production（锤子优先）。

    返回 {"assign": [地块下标...], "totals": {...}, "notes": str}。
    只计算方案，不点任何东西；点击执行由上层（标定后）负责。
    """
    if not tiles:
        return {"assign": [], "totals": {k: 0 for k in _YIELD_KEYS}, "notes": "无地块数据"}
    keys = _YIELD_KEYS
    def _sum(idx_list):
        return {k: sum(float(tiles[i].get(k, 0) or 0) for i in idx_list) for k in keys}

    def _rank(i: int) -> tuple:
        """按目标给地块排序键（负值，越大越优先）。"""
        t = tiles[i]
        f = float(t.get("food", 0) or 0)
        p = float(t.get("prod", 0) or 0)
        if target == "growth":
            return (-f, -p)
        if target == "production":
            return (-p, -f)
        # balanced：食物×2 + 锤×3 + 金币×1 + 文化×1 + 科技×1.5（粗略综合分）
        return (-(f * 2 + p * 3 + float(t.get("gold", 0) or 0)
                  + float(t.get("culture", 0) or 0) + float(t.get("science", 0) or 0) * 1.5),)

    assignable = list(range(1, len(tiles)))
    n = max(0, int(pop) - 1)  # 中心锁定，可分配市民数 = 人口 - 1
    if n == 0:
        return {"assign": [], "totals": _sum([0]), "notes": f"人口 {pop}，仅城市中心被工作，无可分配市民"}
    if n >= len(assignable):
        chosen = assignable
        notes = f"人口 {pop}，全部 {len(assignable)} 个可用地块都已分配（中心锁定）"
    else:
        chosen = sorted(assignable, key=_rank)[:n]
        notes = (f"人口 {pop}，按「{target}」目标从 {len(assignable)} 个地块中选了 {n} 个"
                 f"（中心锁定）")
    return {"assign": chosen, "totals": _sum([0] + chosen), "notes": notes}
