"""会话开关插件：用指令让机器人闭嘴 / 恢复说话，带白名单权限控制。

用法（默认唤醒前缀 /）：
    /end      让本会话闭嘴（机器人回一句「已关闭输出」，之后不再回复任何内容）
    /begin    恢复本会话说话（机器人回一句「已启用输出」）
    /switch   查看本会话当前状态

要点：
1. 开关是**按会话**独立的 —— 在哪个群/私聊发 /end，就只关那一个会话，其他会话照常。
2. 状态持久化在 AstrBot 的插件 KV 存储里，重启不会丢。
3. 白名单控制「谁有权用 /end 和 /begin」；不在名单里的人发指令只会收到一句拒绝提示（含自己的 UID，方便复制去配置）。
4. 闭嘴期间**所有指令仍然可用**（/help、/begin 等照常），只是不再触发模型回复。
5. 可选：连带拦截主动消息（主动消息类插件自己发的消息）。

版本兼容：AstrBot >= 4.16,<5。缺失的钩子在旧版本上会自动降级，不会导致插件加载失败。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star

__version__ = "1.1.0"

KV_KEY = "muted_sessions"
"""插件 KV 存储里的键名。"""

ALLOW_EXTRA = "_session_switch_allow"
"""事件标记：带这个标记的事件不拦截（用于放行 /end 自己的回执）。"""


def _hook(name: str):
    """取一个可选的事件钩子装饰器；当前 AstrBot 版本没有这个钩子时退化为空装饰器。

    这样插件在缺少某个钩子的旧版本上依然能加载，只是少一层拦截。
    """
    factory = getattr(filter, name, None)
    if callable(factory):
        try:
            return factory()
        except Exception:  # noqa: BLE001 - 钩子签名变化时同样降级
            pass

    def _noop(func):
        return func

    return _noop


def _result_is_model_output(result: Any) -> bool:
    """判断一条结果是不是模型产出。

    AstrBot 4.24.4 起有 ``is_model_result()``（含 AGENT_RUNNER_ERROR）；
    更早的版本只有 ``is_llm_result()``，退回用它。
    """
    for name in ("is_model_result", "is_llm_result"):
        checker = getattr(result, name, None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001
                return False
    return False


class SessionSwitchPlugin(Star):
    """按会话控制机器人是否说话。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config: dict[str, Any] = config or {}
        self._muted: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """插件加载时从 KV 存储恢复闭嘴状态。"""
        data: Any = {}
        try:
            data = await self.get_kv_data(KV_KEY, {})
        except Exception as e:  # noqa: BLE001 - 存储异常不应阻止插件加载
            logger.warning(f"[session_switch] 读取静默状态失败，按空状态启动: {e}")

        if isinstance(data, dict):
            self._muted = {
                str(umo): info
                for umo, info in data.items()
                if isinstance(info, dict)
            }
        else:
            self._muted = {}

        logger.info(
            f"[session_switch] v{__version__} 已加载 enabled={self._enabled} "
            f"静默会话数={len(self._muted)} 白名单数={len(self._whitelist)}"
        )

    async def terminate(self) -> None:
        """插件卸载/重载前落盘一次，避免丢状态。"""
        await self._save()

    # ------------------------------------------------------------------
    # 配置读取（配置项可能缺失，全部走兜底）
    # ------------------------------------------------------------------
    @property
    def _enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def _whitelist(self) -> set[str]:
        raw = self.config.get("whitelist") or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(x).strip() for x in raw if str(x).strip()}

    def _cfg_text(self, key: str, default: str) -> str:
        value = self.config.get(key)
        if isinstance(value, str) and value.strip():
            return value
        return default

    # ------------------------------------------------------------------
    # 权限判定
    # ------------------------------------------------------------------
    def _event_ids(self, event: AstrMessageEvent) -> set[str]:
        """收集这个事件可能被用来匹配白名单的所有标识。"""
        ids: set[str] = set()
        for getter in (
            lambda: str(event.get_sender_id()),
            lambda: str(event.unified_msg_origin),
            lambda: str(event.get_group_id() or ""),
            lambda: str(event.get_session_id()),
        ):
            try:
                value = getter().strip()
            except Exception:  # noqa: BLE001 - 不同平台字段缺失是常态
                continue
            if value:
                ids.add(value)
        return ids

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        """判断发起者是否有权使用开关指令。"""
        if self.config.get("allow_all", False):
            return True

        if self.config.get("allow_admin", True):
            try:
                if event.is_admin():
                    return True
            except Exception:  # noqa: BLE001
                pass

        whitelist = self._whitelist
        if not whitelist:
            # 名单为空：默认谁都不给用，除非显式放开
            return bool(self.config.get("empty_whitelist_allow", False))

        return bool(self._event_ids(event) & whitelist)

    def _deny_text(self, event: AstrMessageEvent) -> str:
        """没权限时的提示语，附带 UID 方便用户直接复制去配置。"""
        try:
            uid = str(event.get_sender_id())
        except Exception:  # noqa: BLE001
            uid = "未知"
        if not self._whitelist:
            return (
                "还没有配置白名单，谁都不能用这个指令。\n"
                f"把你的 UID「{uid}」加到插件配置的 whitelist 里就能用了。"
            )
        return f"你没有权限使用这个指令。\n你的 UID：「{uid}」"

    # ------------------------------------------------------------------
    # 状态存取
    # ------------------------------------------------------------------
    async def _save(self) -> None:
        try:
            await self.put_kv_data(KV_KEY, self._muted)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[session_switch] 保存静默状态失败: {e}")

    def _is_muted(self, event: AstrMessageEvent) -> bool:
        try:
            return event.unified_msg_origin in self._muted
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # 指令：/end  关闭输出
    # ------------------------------------------------------------------
    @filter.command("end", alias={"mute"})
    async def cmd_end(self, event: AstrMessageEvent) -> None:
        """让机器人闭嘴：关闭本会话的输出。"""
        if not self._enabled:
            event.set_result(MessageEventResult().message("会话开关插件已停用。"))
            return

        if not self._is_allowed(event):
            event.set_result(MessageEventResult().message(self._deny_text(event)))
            return

        umo = event.unified_msg_origin
        already = umo in self._muted
        self._muted[umo] = {
            "by": str(event.get_sender_id()),
            "at": int(time.time()),
        }
        await self._save()

        # 放行这条回执本身，否则会被下面的 on_decorating_result 吞掉
        event.set_extra(ALLOW_EXTRA, True)
        text = self._cfg_text("end_text", "已关闭输出")
        if already and self.config.get("end_repeat_hint", True):
            text += "（本来就是关着的）"
        event.set_result(MessageEventResult().message(text))
        logger.info(
            f"[session_switch] 关闭输出 umo={umo} by={event.get_sender_id()} "
            f"重复操作={already}"
        )

    # ------------------------------------------------------------------
    # 指令：/begin  启用输出
    # ------------------------------------------------------------------
    @filter.command("begin", alias={"unmute"})
    async def cmd_begin(self, event: AstrMessageEvent) -> None:
        """恢复机器人说话：启用本会话的输出。"""
        if not self._enabled:
            event.set_result(MessageEventResult().message("会话开关插件已停用。"))
            return

        if not self._is_allowed(event):
            event.set_result(MessageEventResult().message(self._deny_text(event)))
            return

        umo = event.unified_msg_origin
        was_muted = self._muted.pop(umo, None) is not None
        if was_muted:
            await self._save()

        event.set_extra(ALLOW_EXTRA, True)
        text = self._cfg_text("begin_text", "已启用输出")
        if not was_muted and self.config.get("begin_repeat_hint", True):
            text += "（本来就是开着的）"
        event.set_result(MessageEventResult().message(text))
        logger.info(
            f"[session_switch] 启用输出 umo={umo} by={event.get_sender_id()} "
            f"原状态={'关' if was_muted else '开'}"
        )

    # ------------------------------------------------------------------
    # 指令：/switch  查看状态
    # ------------------------------------------------------------------
    @filter.command("switch", alias={"mute_status"})
    async def cmd_status(self, event: AstrMessageEvent) -> None:
        """查看本会话当前的输出开关状态。"""
        if not self._enabled:
            event.set_result(MessageEventResult().message("会话开关插件已停用。"))
            return

        umo = event.unified_msg_origin
        info = self._muted.get(umo)
        if info:
            at = info.get("at")
            when = (
                time.strftime("%m-%d %H:%M", time.localtime(at))
                if isinstance(at, int)
                else "未知"
            )
            text = (
                f"本会话当前：已关闭输出\n"
                f"操作人：{info.get('by', '未知')}  时间：{when}\n"
                f"发送 /begin 可以恢复。"
            )
        else:
            text = "本会话当前：正常输出"
        event.set_result(MessageEventResult().message(text))

    # ------------------------------------------------------------------
    # 拦截 0：在「确定要调 LLM、但还没排队等锁」时掐断
    # ------------------------------------------------------------------
    @_hook("on_waiting_llm_request")
    async def block_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """最早的一道闸：连 provider 选择、上下文拼装都省掉。"""
        if not self._enabled:
            return
        if event.get_extra(ALLOW_EXTRA):
            return
        if not self._is_muted(event):
            return

        event.stop_event()
        logger.debug(
            f"[session_switch] 已拦截等待中的 LLM 请求 umo={event.unified_msg_origin}"
        )

    # ------------------------------------------------------------------
    # 拦截 1：阻止 LLM 请求（兜底，覆盖不走上面那条链路的场景）
    # ------------------------------------------------------------------
    @_hook("on_llm_request")
    async def block_llm_request(self, event: AstrMessageEvent, req: Any = None) -> None:
        """闭嘴期间直接掐掉 LLM 请求，既不回话也不花 token。"""
        if not self._enabled:
            return
        if event.get_extra(ALLOW_EXTRA):
            return
        if not self._is_muted(event):
            return

        event.stop_event()
        logger.debug(
            f"[session_switch] 已拦截 LLM 请求 umo={event.unified_msg_origin}"
        )

    # ------------------------------------------------------------------
    # 拦截 2：阻止消息发出（挡住主动消息等绕过 LLM 链路的输出）
    # ------------------------------------------------------------------
    @_hook("on_decorating_result")
    async def block_decorating_result(self, event: AstrMessageEvent) -> None:
        """闭嘴期间拦掉模型产出的消息链。

        只拦「模型结果」，这样 /help 这类指令回执在闭嘴期间仍然正常返回。
        主动消息插件会主动派发本钩子，因此这里也能一并挡住它。
        """
        if not self._enabled:
            return
        if not self.config.get("mute_block_proactive", True):
            return
        if event.get_extra(ALLOW_EXTRA):
            return
        if not self._is_muted(event):
            return

        result = event.get_result()
        if result is None:
            return
        if not _result_is_model_output(result):
            return

        event.stop_event()
        logger.debug(
            f"[session_switch] 已拦截消息发送 umo={event.unified_msg_origin}"
        )
