"""FireTuner 线协议、握手及 Lua 帧收发。

本模块不承载运营决策；``civ6_tuner`` 重新导出这些接口以保持插件和测试兼容。
"""

from __future__ import annotations

import asyncio
import struct

HEADER_FMT = "<Ii"  # length(uint32 LE), tag(int32 LE)
HEADER_SIZE = struct.calcsize(HEADER_FMT)

TAG_HANDSHAKE = 4
TAG_COMMAND = 3

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4318


def _parse_lua_states(raw: list[str]) -> dict[int, str]:
    """LSQ 返回交错 [index, name] 列表 → {index: name}。

    示例：['0','Main State','1','DebugHotloadCache',...,'3','GameCore_Tuner',...]"""
    result: dict[int, str] = {}
    i = 0
    while i + 1 < len(raw):
        try:
            idx = int(raw[i].strip())
        except ValueError:
            i += 1
            continue
        result[idx] = raw[i + 1].strip()
        i += 2
    return result


def _gamecore_state_index(states: list[str], fallback: int = 0) -> int:
    """从 LSQ 原始列表解析出可执行 Lua 的 state 索引。

    优先 GameCore_Tuner（游戏核心状态：Players/Game 可用）；
    次选 InGame；都没有才回退 0。"""
    parsed = _parse_lua_states(states)
    for idx, name in parsed.items():
        if name == "GameCore_Tuner":
            return idx
    for idx, name in parsed.items():
        if name == "InGame":
            return idx
    return fallback


class TunerError(Exception):
    """FireTuner 连接/执行错误。"""


def _to_int(raw) -> int:
    """数值字段安全转 int（游戏返回可能带小数/空格/异常值，失败返回 0）。"""
    try:
        return int(float(str(raw or 0).strip()))
    except (TypeError, ValueError):
        return 0


def _to_float(raw) -> float:
    """数值字段安全转 float（失败返回 -1.0）。"""
    try:
        return float(str(raw or -1).strip())
    except (TypeError, ValueError):
        return -1.0


def _pack(tag: int, payload: str) -> bytes:
    data = payload.encode("utf-8") + b"\x00"
    return struct.pack(HEADER_FMT, len(data), tag) + data


def _unpack(buf: bytes) -> tuple[int, str]:
    if len(buf) < HEADER_SIZE:
        raise TunerError(f"帧过短：{len(buf)} 字节")
    length, tag = struct.unpack(HEADER_FMT, buf[:HEADER_SIZE])
    body = buf[HEADER_SIZE:HEADER_SIZE + length]
    if len(body) < length:
        raise TunerError(f"帧体不完整：需要 {length}，实际 {len(body)}")
    return tag, body.rstrip(b"\x00").decode("utf-8", errors="replace")


async def _send(writer: asyncio.StreamWriter, tag: int, payload: str) -> None:
    writer.write(_pack(tag, payload))
    await writer.drain()


async def _recv(reader: asyncio.StreamReader, timeout: float) -> tuple[int, str]:
    try:
        header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
    except asyncio.TimeoutError:
        raise TunerError("读取超时（游戏无响应，可尝试重启游戏）") from None
    length, tag = struct.unpack(HEADER_FMT, header)
    if length <= 0 or length > 10_000_000:
        raise TunerError(f"非法帧长度：{length}")
    body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    return tag, body.rstrip(b"\x00").decode("utf-8", errors="replace")


async def _drain(reader: asyncio.StreamReader, timeout: float = 0.2) -> None:
    """按完整帧清空残余输出，绝不能只丢包头而把包体留给下一次读取。"""
    while True:
        try:
            header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
        except asyncio.TimeoutError:
            return
        except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionAbortedError, OSError):
            # 服务端关闭连接/重置 = 没有更多数据，幂等返回。
            return
        length, _tag = struct.unpack(HEADER_FMT, header)
        if length <= 0 or length > 10_000_000:
            raise TunerError(f"清理残余输出时发现非法帧长度：{length}")
        try:
            await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
        except asyncio.TimeoutError:
            raise TunerError("清理残余输出时帧体超时，连接已不可安全复用") from None
        except asyncio.IncompleteReadError as exc:
            raise TunerError("清理残余输出时帧体不完整，连接已不可安全复用") from exc


async def _connect(host: str, port: int, timeout: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except OSError as exc:
        raise TunerError(f"无法连接 4318 端口：{exc}（游戏未运行或未启用 Tuner）") from None
    except asyncio.TimeoutError:
        raise TunerError("连接超时") from None


async def handshake(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 5.0,
) -> tuple[str, list[str], asyncio.StreamReader, asyncio.StreamWriter]:
    """连接 + 握手。返回 (app_identity, lua_states, reader, writer)。"""
    reader, writer = await _connect(host, port, timeout)
    try:
        await _drain(reader)
        await _send(writer, TAG_HANDSHAKE, "APP:")
        _, app_id = await _recv(reader, timeout)
        await _send(writer, TAG_HANDSHAKE, "LSQ:")
        _, raw = await _recv(reader, timeout)
        states = [s.strip() for s in raw.replace("\x00", "\n").split("\n") if s.strip()]
        return app_id, states, reader, writer
    except asyncio.CancelledError:
        writer.close()
        raise
    except TunerError:
        writer.close()
        raise
    except Exception as exc:
        writer.close()
        raise TunerError(f"握手失败：{exc}") from None


def _parse_output_payload(payload: str) -> str | None:
    """从 print 输出帧提取内容。

    FireTuner 的 print 输出帧格式：`O\x00<context_name>: <value>`
    （O + null + Lua state 名 + ': ' + 值）。非输出帧（空 ack 等）返回 None。"""
    if not payload.startswith("O"):
        return None
    sep = payload.find(": ", 2)
    if sep >= 0:
        return payload[sep + 2:]
    return payload.lstrip("O").lstrip("\x00").strip()


async def execute_lua(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    code: str,
    state_index: int = 0,
    timeout: float = 8.0,
) -> list[str]:
    """在指定 Lua state 执行脚本，**循环收集**全部 print 输出行。

    FireTuner 的 print 输出是多帧异步流（`O\\x00<state>: <value>`），
    单帧读取会丢行/错位。此处：执行前清陈旧帧 → 发 CMD → 循环读帧
    直到超时 → 解析输出行 → 再清尾部。"""
    await _drain(reader, timeout=0.1)
    await _send(writer, TAG_COMMAND, f"CMD:{state_index}:{code}")
    lines: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    got_any = False
    last_data = loop.time()
    while True:
        now = loop.time()
        remaining = deadline - now
        if remaining <= 0:
            break
        # 性能优化：脚本输出完成后 FireTuner 不再发帧，继续等待只会白耗 2 秒/步。
        # 收到首个输出行后改用短等待片（0.2s），连续 0.6s 无新帧判定流结束提前返回。
        wait = min(remaining, 2.0)
        if got_any:
            wait = min(wait, 0.2)
        try:
            _, payload = await _recv(reader, wait)
        except TunerError:
            break  # 读取超时/帧损坏 → 停止收集
        text = _parse_output_payload(payload)
        if text is not None and text.strip():
            lines.append(text.strip())
            got_any = True
            last_data = loop.time()
        elif got_any and (loop.time() - last_data) >= 0.6:
            break  # 输出后 0.6s 无新帧 → 脚本已执行完
    await _drain(reader, timeout=0.2)
    return lines


async def _handshake_retry(retries: int = 2, delay: float = 0.5):
    """握手带重试：游戏 Tuner 单连接限制下，紧接的连接可能被 RST（前连接未释放），
    短暂等待后重试即可成功。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await handshake()
        except TunerError as exc:
            last = exc
            if attempt < retries:
                await asyncio.sleep(delay)
    raise last if last else TunerError("握手失败")
