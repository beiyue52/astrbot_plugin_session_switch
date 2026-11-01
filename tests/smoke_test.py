"""冒烟测试：用假的 astrbot 包把 main.py 加载起来，验证降级逻辑和指令行为。

只依赖标准库。跑法：
    python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

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
        def __init__(self, umo="aiocqhttp:GroupMessage:1", sender="1001", admin=False):
            self._umo = umo
            self._sender = sender
            self._admin = admin
            self._extras: dict = {}
            self._result = None
            self._stopped = False

        @property
        def unified_msg_origin(self):
            return self._umo

        def get_sender_id(self):
            return self._sender

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
    event_mod.MessageChain = object

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
        pass

    class Star:
        def __init__(self, context, config=None):
            self.context = context
            self._kv: dict = {}

        async def get_kv_data(self, key, default=None):
            return self._kv.get(key, default)

        async def put_kv_data(self, key, value):
            self._kv[key] = value

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


def main() -> int:
    run_cases("当前版本（钩子齐全 + is_model_result）")
    run_cases("旧版本（无 on_waiting_llm_request）", with_waiting_hook=False)
    run_cases("4.24.4 之前（无 is_model_result）", with_is_model_result=False)

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
