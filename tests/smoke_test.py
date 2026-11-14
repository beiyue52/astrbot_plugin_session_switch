"""冒烟测试：用假的 astrbot 包把 main.py 加载起来，验证降级逻辑和指令行为。

只依赖标准库。跑法：
    python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(f"{name} {detail}".strip())
        print(f"  FAIL {name} {detail}")


# ---------------------------------------------------------------------------
# 造一个最小可用的 astrbot 桩：只提供 main.py 实际用到的东西
# ---------------------------------------------------------------------------
def build_stub(*, with_waiting_hook: bool = True, with_is_model_result: bool = True):
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.__path__ = []

    class _Logger:
        def __getattr__(self, name):
            return lambda *a, **k: None

    api.logger = _Logger()

    class AstrBotConfig(dict):
        pass

    api.AstrBotConfig = AstrBotConfig

    # --- astrbot.api.event ---
    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.__path__ = []

    class AstrMessageEvent:
        def __init__(
            self,
            umo="aiocqhttp:GroupMessage:1",
            sender="1001",
            admin=False,
            message="/end",
        ):
            self._umo = umo
            self._sender = sender
            self._admin = admin
            self._message = message
            self._extras: dict = {}
            self._result = None
            self._stopped = False

        @property
        def unified_msg_origin(self):
            return self._umo

        def get_sender_id(self):
            return self._sender

        def get_message_str(self):
            return self._message

        def get_group_id(self):
            return self._umo.rsplit(":", 1)[-1] if "GroupMessage" in self._umo else None

        def get_session_id(self):
            return self._umo

        def is_admin(self):
            return self._admin

        def set_extra(self, k, v):
            self._extras[k] = v

        def get_extra(self, k, default=None):
            return self._extras.get(k, default)

        def set_result(self, r):
            self._result = r

        def get_result(self):
            return self._result

        def stop_event(self):
            self._stopped = True

        def is_stopped(self):
            return self._stopped

    class ResultContentType:
        LLM_RESULT = "llm_result"
        GENERAL_RESULT = "general_result"

    class MessageEventResult:
        def __init__(self, content_type=ResultContentType.GENERAL_RESULT):
            self.texts: list[str] = []
            self.result_content_type = content_type

        def message(self, text):
            self.texts.append(text)
            return self

        def plain_result(self, text):
            self.texts.append(text)
            return self

        if with_is_model_result:

            def is_model_result(self):
                return self.result_content_type == ResultContentType.LLM_RESULT

        def is_llm_result(self):
            return self.result_content_type == ResultContentType.LLM_RESULT

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageEventResult = MessageEventResult
    event_mod.ResultContentType = ResultContentType
    event_mod.CommandResult = MessageEventResult
    event_mod.EventResultType = object
    event_mod.MessageChain = MessageEventResult

    # --- astrbot.api.event.filter ---
    filter_mod = types.ModuleType("astrbot.api.event.filter")
    filter_mod.__path__ = []

    def command(name, **kwargs):
        def deco(func):
            func.__command__ = name
            func.__command_kwargs__ = kwargs
            return func

        return deco

    def on_llm_request():
        def deco(func):
            func.__hook__ = "on_llm_request"
            return func

        return deco

    def on_decorating_result():
        def deco(func):
            func.__hook__ = "on_decorating_result"
            return func

        return deco

    if with_waiting_hook:

        def on_waiting_llm_request():
            def deco(func):
                func.__hook__ = "on_waiting_llm_request"
                return func

            return deco

        filter_mod.on_waiting_llm_request = on_waiting_llm_request

    filter_mod.command = command
    filter_mod.on_llm_request = on_llm_request
    filter_mod.on_decorating_result = on_decorating_result

    event_mod.filter = filter_mod

    # --- astrbot.api.star ---
    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.__path__ = []

    class Context:
        def __init__(self):
            self.sent_messages = []

        async def send_message(self, session, message_chain):
            self.sent_messages.append((session, message_chain))
            return True

    class Star:
        def __init__(self, context, config=None):
            self.context = context
            self._kv: dict = {}

        async def get_kv_data(self, key, default=None):
            return deepcopy(self._kv.get(key, default))

        async def put_kv_data(self, key, value):
            self._kv[key] = deepcopy(value)

    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = object
    star_mod.register = lambda *a, **k: lambda c: c

    api.event = event_mod
    api.star = star_mod
    astrbot.api = api

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_mod,
        "astrbot.api.event.filter": filter_mod,
        "astrbot.api.star": star_mod,
    }
    return (
        modules,
        AstrMessageEvent,
        MessageEventResult,
        ResultContentType,
        AstrBotConfig,
    )


def load_plugin(modules, plugin_path: Path):
    sys.modules.update(modules)
    sys.modules.pop("session_switch_main", None)
    import importlib.util

    spec = importlib.util.spec_from_file_location("session_switch_main", plugin_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["session_switch_main"] = module
    spec.loader.exec_module(module)
    return module


def run_cases(label, *, with_waiting_hook=True, with_is_model_result=True):
    print(f"\n=== {label} ===")
    modules, Event, Result, ContentType, Config = build_stub(
        with_waiting_hook=with_waiting_hook, with_is_model_result=with_is_model_result
    )
    module = load_plugin(modules, ROOT / "main.py")

    cfg = Config(enabled=True, whitelist=["1001"], allow_admin=True)
    plugin = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(), config=cfg
    )

    check("__version__ 已声明", isinstance(getattr(module, "__version__", None), str))
    check(
        "缺钩子时装饰器降级为空",
        callable(getattr(plugin, "block_waiting_llm_request", None)),
    )

    asyncio.run(plugin.initialize())

    # 权限：白名单内
    ev = Event(sender="1001")
    check("白名单内放行", plugin._is_allowed(ev) is True)

    # 权限：白名单外
    ev2 = Event(sender="2002")
    check("白名单外拒绝", plugin._is_allowed(ev2) is False)

    # /end
    ev3 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    asyncio.run(plugin.cmd_end(ev3))
    check("cmd_end 有回执", ev3.get_result() is not None)
    check("cmd_end 打了放行标记", ev3.get_extra(module.ALLOW_EXTRA) is True)
    check("会话已进入静默", ev3.unified_msg_origin in plugin._muted)

    # 静默期：另一个会话不受影响
    other = Event(sender="1001", umo="aiocqhttp:GroupMessage:2")
    check("其他会话不受影响", plugin._is_muted(other) is False)

    # 拦截 1：on_llm_request
    ev4 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    asyncio.run(plugin.block_llm_request(ev4))
    check("静默期 on_llm_request 被拦", ev4.is_stopped() is True)

    # 放行标记能绕过
    ev5 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    ev5.set_extra(module.ALLOW_EXTRA, True)
    asyncio.run(plugin.block_llm_request(ev5))
    check("放行标记绕过拦截", ev5.is_stopped() is False)

    # 拦截 2：只拦模型结果
    ev6 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    ev6.set_result(Result(ContentType.LLM_RESULT))
    asyncio.run(plugin.block_decorating_result(ev6))
    check("静默期模型结果被拦", ev6.is_stopped() is True)

    ev7 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    ev7.set_result(Result(ContentType.GENERAL_RESULT))
    asyncio.run(plugin.block_decorating_result(ev7))
    check("静默期指令回执照常发出", ev7.is_stopped() is False)

    # /switch
    ev8 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    asyncio.run(plugin.cmd_status(ev8))
    check(
        "/switch 显示已关闭",
        "已关闭输出" in "".join(ev8.get_result().texts),
    )

    # /begin
    ev9 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    asyncio.run(plugin.cmd_begin(ev9))
    check("cmd_begin 解除静默", ev9.unified_msg_origin not in plugin._muted)

    # 状态持久化：新实例从 KV 恢复
    asyncio.run(plugin.cmd_end(Event(sender="1001", umo="aiocqhttp:GroupMessage:9")))
    plugin2 = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(), config=cfg
    )
    plugin2._kv = plugin._kv
    asyncio.run(plugin2.initialize())
    check("重启后静默状态恢复", "aiocqhttp:GroupMessage:9" in plugin2._muted)

    # 总开关关掉后全部不生效
    plugin3 = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(), config=Config(enabled=False)
    )
    ev10 = Event(sender="1001", umo="aiocqhttp:GroupMessage:1")
    asyncio.run(plugin3.block_llm_request(ev10))
    check("总开关关闭时不拦截", ev10.is_stopped() is False)

    # 白名单为空时的兜底
    plugin4 = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(), config=Config(whitelist=[])
    )
    check("白名单为空默认拒绝", plugin4._is_allowed(Event(sender="1001")) is False)
    plugin5 = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(whitelist=[], empty_whitelist_allow=True),
    )
    check("白名单为空 + 兜底放行", plugin5._is_allowed(Event(sender="1001")) is True)

    # 管理员
    plugin6 = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(), config=Config(whitelist=["9999"])
    )
    check(
        "管理员无视白名单",
        plugin6._is_allowed(Event(sender="1001", admin=True)) is True,
    )

    # 脏 KV 数据
    plugin7 = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(), config=cfg
    )
    plugin7._kv = {module.KV_KEY: {"ok": {"by": "1"}, "bad": "not-a-dict"}}
    asyncio.run(plugin7.initialize())
    check("KV 脏数据被过滤", list(plugin7._muted) == ["ok"], str(plugin7._muted))

    # 自定义指令名：默认值
    check(
        "默认指令名",
        plugin._command_names == {"end": "end", "begin": "begin", "switch": "switch"},
        str(plugin._command_names),
    )

    # 自定义指令名：带斜杠、空格、全角空格都要洗干净
    p_cmd = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(
            end_command="/闭嘴", begin_command="  说话  ", status_command="／状态"
        ),
    )
    check(
        "指令名清洗（去斜杠和空白）",
        p_cmd._command_names == {"end": "闭嘴", "begin": "说话", "switch": "状态"},
        str(p_cmd._command_names),
    )

    # 自定义指令名：空白/多词/非字符串一律退回默认
    p_bad = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(end_command="   ", begin_command="恢复 输出", status_command=123),
    )
    check(
        "非法指令名退回默认",
        p_bad._command_names == {"end": "end", "begin": "begin", "switch": "switch"},
        str(p_bad._command_names),
    )

    # 自定义指令名：撞车时全部退回默认，避免两个 handler 抢同一个指令
    p_dup = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(end_command="闭嘴", begin_command="闭嘴"),
    )
    check(
        "指令名重复时退回默认",
        p_dup._command_names == {"end": "end", "begin": "begin", "switch": "switch"},
        str(p_dup._command_names),
    )

    # 自定义指令名撞上内置别名：别名是写死的，主名抢了别名会让两个 handler 抢消息
    p_alias = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(end_command="unmute"),
    )
    check(
        "指令名撞内置别名时退回默认",
        p_alias._command_names == {"end": "end", "begin": "begin", "switch": "switch"},
        str(p_alias._command_names),
    )

    # 运行时改名：直接作用在 CommandFilter 上，并清掉匹配缓存
    class _FakeCommandFilter:
        def __init__(self):
            self.command_name = "end"
            self.alias = {"mute"}
            self._cmpl_cmd_names = ["end", "mute"]

    class _FakeHandler:
        def __init__(self):
            self.event_filters = [_FakeCommandFilter()]

    fake = _FakeHandler()
    changed = module._apply_command_name(fake, "闭嘴", alias=set())
    check("运行时改名成功", changed is True)
    check("command_name 已改", fake.event_filters[0].command_name == "闭嘴")
    check("别名已清空", fake.event_filters[0].alias == set())
    check("匹配缓存已失效", fake.event_filters[0]._cmpl_cmd_names is None)

    # 运行时改名：没有 CommandFilter 时安全返回 False
    class _EmptyHandler:
        event_filters = []

    check(
        "无指令过滤器时安全跳过",
        module._apply_command_name(_EmptyHandler(), "x") is False,
    )

    # 自定义重复提示语：填了就以它为准，不与主文案相连
    p_hint = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(
            whitelist=["1001"],
            end_text="好",
            end_repeat_text="（已经关过了）",
            begin_text="行",
            begin_repeat_text="（一直开着呢）",
        ),
    )
    ev_h1 = Event(sender="1001", umo="aiocqhttp:GroupMessage:7")
    asyncio.run(p_hint.cmd_begin(ev_h1))
    check(
        "重复恢复只输出自定义提示语",
        "".join(ev_h1.get_result().texts) == "（一直开着呢）",
        "".join(ev_h1.get_result().texts),
    )
    ev_h2 = Event(sender="1001", umo="aiocqhttp:GroupMessage:7")
    asyncio.run(p_hint.cmd_end(ev_h2))
    ev_h3 = Event(sender="1001", umo="aiocqhttp:GroupMessage:7")
    asyncio.run(p_hint.cmd_end(ev_h3))
    check(
        "重复关闭只输出自定义提示语",
        "".join(ev_h3.get_result().texts) == "（已经关过了）",
        "".join(ev_h3.get_result().texts),
    )

    # 没自定义提示语时，重复操作只回默认的那句括号文案
    p_def = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(whitelist=["1001"]),
    )
    ev_d1 = Event(sender="1001", umo="aiocqhttp:GroupMessage:9")
    asyncio.run(p_def.cmd_begin(ev_d1))
    check(
        "默认提示语在重复恢复时单独成句",
        "".join(ev_d1.get_result().texts) == "（本来就是开着的）",
        "".join(ev_d1.get_result().texts),
    )
    ev_d2 = Event(sender="1001", umo="aiocqhttp:GroupMessage:9")
    asyncio.run(p_def.cmd_end(ev_d2))
    ev_d3 = Event(sender="1001", umo="aiocqhttp:GroupMessage:9")
    asyncio.run(p_def.cmd_end(ev_d3))
    check(
        "默认提示语在重复关闭时单独成句",
        "".join(ev_d3.get_result().texts) == "（本来就是关着的）",
        "".join(ev_d3.get_result().texts),
    )

    # 首次操作仍走主文案，不受重复提示语影响
    p_first = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(whitelist=["1001"], begin_text="输出已恢复"),
    )
    # 先 /end 进入静默，这样紧接着的 /begin 才是真正的「首次恢复」
    asyncio.run(p_first.cmd_end(Event(sender="1001", umo="aiocqhttp:GroupMessage:10")))
    ev_f1 = Event(sender="1001", umo="aiocqhttp:GroupMessage:10")
    asyncio.run(p_first.cmd_begin(ev_f1))
    check(
        "首次恢复只输出主文案",
        "".join(ev_f1.get_result().texts) == "输出已恢复",
        "".join(ev_f1.get_result().texts),
    )
    ev_f2 = Event(sender="1001", umo="aiocqhttp:GroupMessage:10")
    asyncio.run(p_first.cmd_begin(ev_f2))
    check(
        "重复恢复只输出提示语，不与主文案相连",
        "".join(ev_f2.get_result().texts) == "（本来就是开着的）",
        "".join(ev_f2.get_result().texts),
    )

    # 关掉重复提示后，重复操作只回主文案
    p_nohint = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(whitelist=["1001"], end_repeat_hint=False, end_text="好"),
    )
    asyncio.run(p_nohint.cmd_end(Event(sender="1001", umo="aiocqhttp:GroupMessage:8")))
    ev_n = Event(sender="1001", umo="aiocqhttp:GroupMessage:8")
    asyncio.run(p_nohint.cmd_end(ev_n))
    check(
        "关掉重复提示后只回主文案",
        "".join(ev_n.get_result().texts) == "好",
        "".join(ev_n.get_result().texts),
    )

    # /switch 的提示语跟随自定义的 begin 指令名
    p_sw = module.SessionSwitchPlugin(
        context=modules["astrbot.api.star"].Context(),
        config=Config(whitelist=["1001"], begin_command="说话"),
    )
    ev_sw = Event(sender="1001", umo="aiocqhttp:GroupMessage:6")
    asyncio.run(p_sw.cmd_end(ev_sw))
    ev_sw2 = Event(sender="1001", umo="aiocqhttp:GroupMessage:6")
    asyncio.run(p_sw.cmd_status(ev_sw2))
    check(
        "状态提示用自定义 begin 指令名",
        "/说话" in "".join(ev_sw2.get_result().texts),
        "".join(ev_sw2.get_result().texts),
    )


def run_timed_cases(label, *, with_waiting_hook=True, with_is_model_result=True):
    print(f"\n=== {label}：限时静音 ===")
    modules, Event, Result, ContentType, Config = build_stub(
        with_waiting_hook=with_waiting_hook, with_is_model_result=with_is_model_result
    )
    module = load_plugin(modules, ROOT / "main.py")

    for raw, expected in (
        ("", None),
        ("  ", None),
        ("90s", 90),
        ("17m", 1020),
        ("5h", 18000),
        ("3d", 259200),
        (" 2H ", 7200),
        ("30d", module.MAX_MUTE_SECONDS),
        ("2592000s", module.MAX_MUTE_SECONDS),
    ):
        check(f"时长解析 {raw!r}", module._parse_duration(raw) == expected)

    invalid = (
        "0s",
        "-1m",
        "+1m",
        "1.5h",
        "30",
        "1w",
        "31d",
        "2592001s",
        "999999999999999999999h",
        "1h 30m",
        "30 m",
        "30m 多余参数",
        "nan",
        "１m",
    )
    for raw in invalid:
        try:
            module._parse_duration(raw)
        except ValueError:
            rejected = True
        else:
            rejected = False
        check(f"拒绝非法时长 {raw!r}", rejected)

    async def cases():
        plugins = []

        def make_plugin(**config):
            cfg = {"whitelist": ["1001"]}
            cfg.update(config)
            plugin = module.SessionSwitchPlugin(
                context=modules["astrbot.api.star"].Context(), config=Config(cfg)
            )
            plugins.append(plugin)
            return plugin

        def text(event):
            return "".join(event.get_result().texts)

        try:
            with patch.object(module.time, "time", return_value=1800000000.25) as clock:
                plugin = make_plugin(
                    end_command="闭嘴",
                    begin_command="说话",
                    status_command="状态",
                    end_text="好",
                    end_repeat_text="（已经关过了）",
                    timed_mute_show_duration=True,
                )
                await plugin.initialize()
                ev = Event(message="/闭嘴 17m")
                umo = ev.unified_msg_origin
                await plugin.cmd_end(ev, duration_text="17m")
                original_info = deepcopy(plugin._muted[umo])
                original_task = plugin._expiry_tasks[umo]
                deadline = clock.return_value + 1020
                check(
                    "自定义关闭指令可设置任意分钟数",
                    original_info["expires_at"] == deadline,
                )
                check(
                    "限时回执保留自定义文案并显示时长",
                    text(ev) == "好\n将在 17 分钟后自动恢复。",
                )
                check("限时回执带放行标记", ev.get_extra(module.ALLOW_EXTRA) is True)
                check(
                    "限时静音保存绝对到期时间",
                    plugin._kv[module.KV_KEY][umo] == original_info,
                )
                check(
                    "限时静音不影响其他会话", not plugin._is_muted(Event(umo="other"))
                )
                status = Event(message="/状态")
                await plugin.cmd_status(status)
                check(
                    "状态显示剩余时间和自定义恢复指令",
                    "剩余：17 分钟" in text(status) and "/说话" in text(status),
                )

                restarted = make_plugin()
                restarted._kv = deepcopy(plugin._kv)
                clock.return_value += 10
                await restarted.initialize()
                check(
                    "重启沿用原到期时间，不重新计时",
                    restarted._muted[umo]["expires_at"] == deadline,
                )
                check("重启重新安排计时任务", umo in restarted._expiry_tasks)

                for raw in invalid:
                    bad = Event(message=f"/闭嘴 {raw}")
                    await plugin.cmd_end(bad, duration_text=raw)
                    check(
                        f"非法参数不改变现有静音 {raw!r}",
                        plugin._muted[umo] == original_info
                        and plugin._expiry_tasks[umo] is original_task,
                    )
                    check(f"非法参数提示自定义指令 {raw!r}", "/闭嘴 30m" in text(bad))

                denied = Event(sender="2002", message="/闭嘴 2h")
                await plugin.cmd_end(denied, duration_text="2h")
                check(
                    "无权限不能重设计时",
                    plugin._muted[umo] == original_info and "没有权限" in text(denied),
                )
                disabled = make_plugin(enabled=False)
                await disabled.cmd_end(Event(message="/end 2h"), duration_text="2h")
                check(
                    "插件停用时不能设置计时",
                    not disabled._muted and not disabled._expiry_tasks,
                )

                reset = Event(message="/mute 90s")
                await plugin.cmd_end(reset, duration_text="90s")
                replacement_task = plugin._expiry_tasks[umo]
                await asyncio.gather(original_task, return_exceptions=True)
                check(
                    "别名支持时长并从本次操作重新计时",
                    plugin._muted[umo]["expires_at"] == clock.return_value + 90,
                )
                check(
                    "重设计时取消旧任务且保留新任务",
                    original_task.cancelled()
                    and plugin._expiry_tasks[umo] is replacement_task,
                )
                check(
                    "重设计时回执明确显示新时长",
                    "1 分钟 30 秒" in text(reset) and "已经关过" not in text(reset),
                )
                # 即使旧任务晚一步执行，也不能解除新的静音。
                await plugin._resume_after(umo, original_info)
                check(
                    "旧任务不会误解除新的静音",
                    plugin._is_muted(Event())
                    and plugin._expiry_tasks[umo] is replacement_task,
                )

                permanent = Event(message="/闭嘴")
                await plugin.cmd_end(permanent)
                await asyncio.gather(replacement_task, return_exceptions=True)
                check(
                    "不带时长可改为一直静音并取消计时",
                    "expires_at" not in plugin._muted[umo]
                    and umo not in plugin._expiry_tasks,
                )
                check(
                    "不带时长的重复关闭仍只回自定义重复提示",
                    text(permanent) == "（已经关过了）",
                )
                check("旧版静音记录不受影响", plugin._is_muted(Event()))

                await plugin.cmd_end(Event(message="/闭嘴 5h"), duration_text="5h")
                manual_task = plugin._expiry_tasks[umo]
                begin = Event(message="/说话")
                await plugin.cmd_begin(begin)
                await asyncio.gather(manual_task, return_exceptions=True)
                check(
                    "手动恢复取消计时并清除持久化状态",
                    manual_task.cancelled()
                    and umo not in plugin._expiry_tasks
                    and not plugin._kv[module.KV_KEY],
                )
                check("手动恢复使用原有回执", text(begin) == "已启用输出")

                await plugin.cmd_end(Event(message="/闭嘴 90s"), duration_text="90s")
                task = plugin._expiry_tasks[umo]
                expires_at = plugin._muted[umo]["expires_at"]
                clock.return_value = expires_at - 0.1
                check("到期前仍然静音", plugin._is_muted(Event()))
                clock.return_value = expires_at
                for hook in (
                    plugin.block_waiting_llm_request,
                    plugin.block_llm_request,
                    plugin.block_decorating_result,
                ):
                    event = Event()
                    event.set_result(Result(ContentType.LLM_RESULT))
                    await hook(event)
                    check(f"到期边界立即放行 {hook.__name__}", not event.is_stopped())
                ended = Event(message="/状态")
                await plugin.cmd_status(ended)
                check(
                    "到期后状态立即显示正常输出", text(ended) == "本会话当前：正常输出"
                )
                await task
                check(
                    "计时到期清除内存、KV 和任务",
                    not plugin._muted
                    and not plugin._kv[module.KV_KEY]
                    and not plugin._expiry_tasks,
                )

                # 框架先匹配命令、保存参数，其他处理器可能在调用命令前修改消息全文。
                native = make_plugin(end_command="闭嘴", timed_mute_show_duration=True)
                for message, duration_text, seconds in (
                    ("闭嘴 10m [MSG_ID:12345]", "10m", 600),
                    ("闭嘴 100s [MSG_ID:12345]", "100s", 100),
                    ("@机器人 /闭嘴 10m [MSG_ID:12345]", "10m", 600),
                    ("[引用消息] 闭嘴 100s", "100s", 100),
                ):
                    event = Event(message=message)
                    await native.cmd_end(event, duration_text=duration_text)
                    check(
                        f"消息被增强后仍使用框架参数 {message!r}",
                        native._muted[umo]["expires_at"] == clock.return_value + seconds
                        and "自动恢复" in text(event),
                    )
                before = deepcopy(native._muted[umo])
                extra = Event(message="闭嘴 1h 30m [MSG_ID:12345]")
                await native.cmd_end(extra, duration_text="1h", extra="30m")
                check(
                    "框架传入多余参数时拒绝操作，不改变状态",
                    native._muted[umo] == before and "格式不对" in text(extra),
                )
                await native.cmd_end(Event(message="闭嘴 [MSG_ID:12345]"))
                check(
                    "不带时长时不把消息附加信息误作参数",
                    "expires_at" not in native._muted[umo]
                    and umo not in native._expiry_tasks,
                )

                startup = make_plugin()
                startup._kv = {
                    module.KV_KEY: {
                        "expired": {
                            "by": "1001",
                            "at": 1,
                            "expires_at": clock.return_value - 1,
                        },
                        "active": {
                            "by": "1001",
                            "at": 1,
                            "expires_at": clock.return_value + 3600,
                        },
                        "legacy": {"by": "1001", "at": 1},
                        "bad_expiry": {"expires_at": "坏数据"},
                        "bad_record": "坏数据",
                    }
                }
                await startup.initialize()
                check(
                    "启动清理停机期间到期的记录并落盘",
                    "expired" not in startup._muted
                    and "expired" not in startup._kv[module.KV_KEY],
                )
                check(
                    "启动保留未到期、旧版和损坏到期字段的记录",
                    set(startup._muted) == {"active", "legacy", "bad_expiry"},
                )
                check(
                    "只给有效的限时记录安排任务",
                    set(startup._expiry_tasks) == {"active"},
                )
                startup_tasks = list(startup._expiry_tasks.values())
                await startup.terminate()
                check(
                    "卸载取消全部任务但保留未到期状态",
                    not startup._expiry_tasks
                    and all(t.done() for t in startup_tasks)
                    and "active" in startup._kv[module.KV_KEY],
                )

            # 真正让事件循环等待到期，覆盖任务休眠和自动唤醒，不依赖消息触发。
            live = make_plugin(timed_mute_notify_on_expire=True, begin_text="继续说吧")
            await live.cmd_end(Event(message="/end 1s"), duration_text="1s")
            await asyncio.wait_for(live._expiry_tasks[umo], timeout=3)
            check(
                "没有新消息时也会在到期后自动恢复",
                not live._muted
                and not live._kv[module.KV_KEY]
                and not live._expiry_tasks,
            )
            check(
                "没有新消息时到期也会发送自定义恢复文案",
                [
                    (session, "".join(chain.texts))
                    for session, chain in live.context.sent_messages
                ]
                == [(umo, "继续说吧")],
            )

            # 模拟持久化变慢：第二次状态变更不能被较早发起的保存覆盖。
            concurrent = make_plugin()
            started = asyncio.Event()
            release = asyncio.Event()
            writes = []

            async def delayed_put(key, value):
                writes.append(deepcopy(value))
                if len(writes) == 1:
                    started.set()
                    await release.wait()
                concurrent._kv[key] = deepcopy(value)

            concurrent.put_kv_data = delayed_put
            first = asyncio.create_task(concurrent.cmd_end(Event(umo="first")))
            await started.wait()
            second = asyncio.create_task(concurrent.cmd_end(Event(umo="second")))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)
            check("保存使用独立快照", set(writes[0]) == {"first"})
            check(
                "并发状态变更串行落盘，最终状态不丢",
                len(writes) == 2
                and set(concurrent._kv[module.KV_KEY]) == {"first", "second"},
            )
        finally:
            for plugin in reversed(plugins):
                await plugin.terminate()

    asyncio.run(cases())


def run_timed_reply_cases(label, *, with_waiting_hook=True, with_is_model_result=True):
    print(f"\n=== {label}：限时静音回复配置 ===")
    modules, Event, _, _, Config = build_stub(
        with_waiting_hook=with_waiting_hook, with_is_model_result=with_is_model_result
    )
    module = load_plugin(modules, ROOT / "main.py")

    async def cases():
        plugins = []
        umo = Event().unified_msg_origin

        def make_plugin(**config):
            plugin = module.SessionSwitchPlugin(
                modules["astrbot.api.star"].Context(),
                Config(whitelist=["1001"], **config),
            )
            plugins.append(plugin)
            return plugin

        def sent(plugin):
            return [
                (session, "".join(chain.texts))
                for session, chain in plugin.context.sent_messages
            ]

        async def expire(plugin, session=umo):
            # 将到期时间提前，重新安排任务；真实等待另有用例覆盖。
            task = plugin._expiry_tasks[session]
            plugin._cancel_expiry(session)
            await asyncio.gather(task, return_exceptions=True)
            plugin._muted[session]["expires_at"] = module.time.time()
            plugin._schedule_expiry(session)
            await plugin._expiry_tasks[session]

        try:
            for options in ({}, {"timed_mute_show_duration": False}):
                plugin = make_plugin(end_text="好", **options)
                event = Event(message="/end 10m")
                await plugin.cmd_end(event, duration_text="10m")
                check(
                    f"时长显示默认或显式关闭时只回自定义文案 {options!r}",
                    "".join(event.get_result().texts) == "好"
                    and event.get_extra(module.ALLOW_EXTRA) is True,
                )
                plugin.config["timed_mute_show_duration"] = True
                await plugin.cmd_end(event, duration_text="90s")
                check(
                    "打开时长显示后重设计时仍保留自定义文案",
                    "".join(event.get_result().texts)
                    == "好\n将在 1 分钟 30 秒后自动恢复。",
                )
                plugin.config["timed_mute_show_duration"] = False
                await plugin.cmd_end(event, duration_text="10m")
                check(
                    "再次关闭时长显示立即生效",
                    "".join(event.get_result().texts) == "好",
                )
                await expire(plugin)
                check("到期默认不发送提醒", sent(plugin) == [])

            for options in ({}, {"timed_mute_notify_on_expire": False}):
                quiet = make_plugin(**options)
                await quiet.cmd_end(Event(), duration_text="10m")
                await expire(quiet)
                check("关闭到期提醒仍正常恢复", not quiet._muted and sent(quiet) == [])

            notify = make_plugin(
                end_command="闭嘴",
                begin_command="说话",
                begin_text="继续说吧",
                begin_repeat_text="一直开着呢",
                timed_mute_notify_on_expire=True,
            )
            private = "aiocqhttp:FriendMessage:1001"
            for session in (umo, private):
                await notify.cmd_end(Event(umo=session), duration_text="10m")
            await expire(notify)
            check(
                "到期只向原会话发送自定义恢复文案，不用重复提示语",
                sent(notify) == [(umo, "继续说吧")]
                and notify._is_muted(Event(umo=private)),
            )
            notify.config["begin_text"] = "现在可以聊了"
            await expire(notify, private)
            check(
                "私聊到期使用当前配置的恢复文案",
                sent(notify) == [(umo, "继续说吧"), (private, "现在可以聊了")]
                and not notify._kv[module.KV_KEY],
            )

            for begin_text in (None, "  "):
                fallback = make_plugin(
                    timed_mute_notify_on_expire=True, begin_text=begin_text
                )
                await fallback.cmd_end(Event(), duration_text="10m")
                await expire(fallback)
                check(
                    "到期文案缺失或空白时沿用恢复指令兜底",
                    sent(fallback) == [(umo, "已启用输出")],
                )

            manual = make_plugin(
                timed_mute_notify_on_expire=True, begin_text="继续说吧"
            )
            await manual.cmd_end(Event(), duration_text="10m")
            task = manual._expiry_tasks[umo]
            begin = Event(message="/begin")
            await manual.cmd_begin(begin)
            await asyncio.gather(task, return_exceptions=True)
            check(
                "手动恢复仍回自定义文案且不会重复发到期提醒",
                "".join(begin.get_result().texts) == "继续说吧"
                and sent(manual) == []
                and not manual._expiry_tasks,
            )

            reset = make_plugin(timed_mute_notify_on_expire=True)
            await reset.cmd_end(Event(), duration_text="10m")
            old_task = reset._expiry_tasks[umo]
            await reset.cmd_end(Event(), duration_text="20m")
            await asyncio.gather(old_task, return_exceptions=True)
            check("重设计时取消旧提醒", old_task.cancelled() and sent(reset) == [])
            await expire(reset)
            check(
                "重设计时后仅在新计时到期时提醒一次",
                sent(reset) == [(umo, "已启用输出")],
            )
            await reset.cmd_end(Event(), duration_text="10m")
            old_task = reset._expiry_tasks[umo]
            await reset.cmd_end(Event())
            await asyncio.gather(old_task, return_exceptions=True)
            check(
                "改为长期静音不会残留到期提醒",
                old_task.cancelled()
                and not reset._expiry_tasks
                and reset._is_muted(Event())
                and len(sent(reset)) == 1,
            )

            restored = make_plugin(
                timed_mute_notify_on_expire=True, begin_text="继续说吧"
            )
            await restored.cmd_end(Event(), duration_text="10m")
            await restored.terminate()
            reloaded = make_plugin(
                timed_mute_notify_on_expire=True, begin_text="继续说吧"
            )
            reloaded._kv = deepcopy(restored._kv)
            await reloaded.initialize()
            await expire(reloaded)
            check(
                "重载后未到期记录仍能发送自定义恢复文案",
                sent(reloaded) == [(umo, "继续说吧")],
            )

            offline = make_plugin(timed_mute_notify_on_expire=True)
            offline._kv = {module.KV_KEY: {umo: {"expires_at": module.time.time() - 1}}}
            await offline.initialize()
            check(
                "停机期间已到期的记录不补发提醒",
                not offline._muted and sent(offline) == [],
            )

            disabled = make_plugin(timed_mute_notify_on_expire=True)
            await disabled.cmd_end(Event(), duration_text="10m")
            disabled.config["enabled"] = False
            await expire(disabled)
            check(
                "插件停用后到期不发提醒", not disabled._muted and sent(disabled) == []
            )

            toggled = make_plugin(timed_mute_notify_on_expire=True)
            await toggled.cmd_end(Event(), duration_text="10m")
            toggled.config["timed_mute_notify_on_expire"] = False
            await expire(toggled)
            check("计时中关闭到期提醒立即生效", sent(toggled) == [])

            for send_result in (RuntimeError("模拟发送失败"), False):
                failed = make_plugin(timed_mute_notify_on_expire=True)
                if isinstance(send_result, Exception):
                    failed.context.send_message = AsyncMock(side_effect=send_result)
                else:
                    failed.context.send_message = AsyncMock(return_value=send_result)
                await failed.cmd_end(Event(), duration_text="10m")
                await expire(failed)
                check(
                    "提醒发送异常或平台不可用不影响恢复，不重复发送",
                    not failed._muted
                    and not failed._kv[module.KV_KEY]
                    and not failed._expiry_tasks
                    and failed.context.send_message.await_count == 1,
                )
        finally:
            for plugin in reversed(plugins):
                await plugin.terminate()

    asyncio.run(cases())


def main() -> int:
    run_cases("当前版本（钩子齐全 + is_model_result）")
    run_cases("旧版本（无 on_waiting_llm_request）", with_waiting_hook=False)
    run_cases("4.24.4 之前（无 is_model_result）", with_is_model_result=False)
    run_timed_cases("当前版本（钩子齐全 + is_model_result）")
    run_timed_cases("旧版本（无 on_waiting_llm_request）", with_waiting_hook=False)
    run_timed_cases("4.24.4 之前（无 is_model_result）", with_is_model_result=False)
    run_timed_reply_cases("当前版本（钩子齐全 + is_model_result）")
    run_timed_reply_cases(
        "旧版本（无 on_waiting_llm_request）", with_waiting_hook=False
    )
    run_timed_reply_cases(
        "4.24.4 之前（无 is_model_result）", with_is_model_result=False
    )

    print()
    if failures:
        print(f"=== {len(failures)} 项失败 ===")
        for item in failures:
            print(" -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
