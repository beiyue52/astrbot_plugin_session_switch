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
6. 三个指令的名字都能改（配置项 end_command / begin_command / status_command）。

版本兼容：AstrBot >= 4.16,<5。缺失的钩子在旧版本上会自动降级，不会导致插件加载失败。
"""

from __future__ import annotations

import re
import time
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star

__version__ = "1.2.0"

KV_KEY = "muted_sessions"
"""插件 KV 存储里的键名。"""

ALLOW_EXTRA = "_session_switch_allow"
"""事件标记：带这个标记的事件不拦截（用于放行 /end 自己的回执）。"""

DEFAULT_COMMANDS = {
    "end": "end",
    "begin": "begin",
    "switch": "switch",
}
"""三个指令的默认名字，也是配置项 end_command / begin_command / status_command 的兜底值。"""

DEFAULT_ALIASES = {
    "end": {"mute"},
    "begin": {"unmute"},
    "switch": {"mute_status"},
}
"""三个指令的固定别名。别名不可配置，但改名时要把它们算进冲突检测。"""


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


def _normalize_command_name(raw: Any, default: str) -> str:
    """把配置里的指令名洗干净：去掉前缀斜杠、空白和全角空格。

    用户可能填 ``/闭嘴``、`` 闭嘴 ``、``／闭嘴`` 之类的写法，统一收敛成 ``闭嘴``。
    清洗后为空或含空格/换行（AstrBot 不支持多词指令名）时退回默认值。
    """
    if not isinstance(raw, str):
        return default
    name = raw.strip().lstrip("/!！／").strip()
    if not name or re.search(r"\s", name):
        return default
    return name


def _locate_command_filter(handler: Any) -> Any:
    """从 handler 的 event_filters 里找出主 CommandFilter。

    用鸭子类型判断（有 ``command_name`` 和 ``alias`` 就是指令过滤器），
    这样不依赖 AstrBot 内部的类名和 import 路径，跨版本更稳。
    指令组（CommandGroupFilter）也满足这两个属性，故按类名排除。
    """
    for filter_obj in getattr(handler, "event_filters", None) or []:
        if not hasattr(filter_obj, "command_name") or not hasattr(filter_obj, "alias"):
            continue
        if "Group" in type(filter_obj).__name__:
            continue
        return filter_obj
    return None


def _apply_command_name(handler: Any, name: str, alias: set[str] | None = None) -> bool:
    """在运行时把某个 handler 的指令名（和别名）改掉。

    做法与 AstrBot 自己的「指令管理」一致：直接改 CommandFilter 的
    ``command_name`` / ``alias``，再把缓存 ``_cmpl_cmd_names`` 清空，
    否则它还会用旧名字匹配。
    """
    filter_obj = _locate_command_filter(handler)
    if filter_obj is None:
        return False
    try:
        filter_obj.command_name = name
        if alias is not None:
            filter_obj.alias = set(alias)
        filter_obj._cmpl_cmd_names = None
    except Exception:  # noqa: BLE001 - 内部结构变化时静默跳过，不影响插件运行
        return False
    return True


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
        """插件加载时从 KV 存储恢复闭嘴状态，并把自定义指令名应用到运行时。"""
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

        renamed = self._apply_command_names()

        logger.info(
            f"[session_switch] v{__version__} 已加载 enabled={self._enabled} "
            f"静默会话数={len(self._muted)} 白名单数={len(self._whitelist)} "
            f"指令={'/'.join(self._command_names.values())}"
            + ("（已应用自定义指令名）" if renamed else "")
        )

    def _apply_command_names(self) -> bool:
        """把配置里的自定义指令名写到自己的 handler 上。

        ``initialize()`` 在 handler 绑定之后、``sync_command_configs()`` 之前被调用，
        此时改 filter 才能被后续的指令管理同步看到。找不到 registry 时静默跳过。
        """
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except Exception:  # noqa: BLE001 - 老版本路径不同，跳过即可
            return False

        targets = {
            "cmd_end": self._command_names["end"],
            "cmd_begin": self._command_names["begin"],
            "cmd_status": self._command_names["switch"],
        }
        applied = False
        for handler in list(star_handlers_registry):
            if getattr(handler, "handler_module_path", None) != __name__:
                continue
            raw = handler.handler
            handler_name = getattr(raw, "__name__", "")
            if not handler_name and hasattr(raw, "func"):
                handler_name = getattr(raw.func, "__name__", "")
            name = targets.get(handler_name)
            if not name:
                continue
            if _apply_command_name(handler, name):
                applied = True
        return applied

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

    @property
    def _command_names(self) -> dict[str, str]:
        """三个指令当前该用的名字。

        配置项缺失、填了空白、或名字撞车时，全部退回默认名 —— 否则会出现
        两个 handler 抢同一个指令，或者某个指令彻底不可用。

        撞车检测要把别名一起算进去：别名是写死的、改不了，如果用户把 end 改名成
        ``unmute``，那 begin 的别名和 end 的主名就会撞，两个 handler 抢同一条消息。
        """
        names = {
            "end": _normalize_command_name(
                self.config.get("end_command"), DEFAULT_COMMANDS["end"]
            ),
            "begin": _normalize_command_name(
                self.config.get("begin_command"), DEFAULT_COMMANDS["begin"]
            ),
            "switch": _normalize_command_name(
                self.config.get("status_command"), DEFAULT_COMMANDS["switch"]
            ),
        }
        # 主名之间不能重复
        if len(set(names.values())) != len(names):
            logger.warning(
                f"[session_switch] 自定义指令名有重复 {names}，本次全部使用默认指令名"
            )
            return dict(DEFAULT_COMMANDS)
        # 主名也不能撞上别人的别名（别名不归用户配置，撞了只能退回默认）。
        # 撞自己的别名无所谓：同一个 filter 里重复匹配两次，没有 handler 冲突。
        for key, name in names.items():
            for alias_owner, aliases in DEFAULT_ALIASES.items():
                if alias_owner != key and name in aliases:
                    logger.warning(
                        f"[session_switch] 自定义指令名 {name!r}（{key}）与 "
                        f"{alias_owner} 的别名冲突，本次全部使用默认指令名"
                    )
                    return dict(DEFAULT_COMMANDS)
        return names

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
            text += self._cfg_text("end_repeat_text", "（本来就是关着的）")
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
            text += self._cfg_text("begin_repeat_text", "（本来就是开着的）")
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
                f"发送 /{self._command_names['begin']} 可以恢复。"
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
