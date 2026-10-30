# 会话开关 · astrbot_plugin_session_switch

用 `/end` 让机器人闭嘴、`/begin` 让她恢复说话。**按会话独立生效** —— 在哪个群或私聊里关，就只关那一个，别的会话照常聊天。

状态存在插件 KV 里，重启不丢。

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-blue)](https://github.com/AstrBotDevs/AstrBot)
[![version](https://img.shields.io/badge/version-1.1.0-green)](CHANGELOG.md)
[![license](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

---

## 为什么需要它

大部分开关类实现都是**全局单开关**：在一个群里 `/end`，所有群、所有私聊一起哑。而且它们通常在「唤醒检查」阶段就把整条消息事件 `stop_event()` 掉，副作用很明显：

- 闭嘴期间 `/help` 之类的指令也一起失灵
- 群聊上下文不再累积，`/begin` 之后出现消息断层
- 挡不住内置的「空 @ 自动搭话」链路（`handle_empty_mention` 的 priority 更高，先跑并直接调了模型）

本插件换了个思路：**不动消息事件本身，只在「要调模型」和「要发消息」这两个点上掐断**。所以闭嘴期间消息照收、指令照用、群聊记录照攒，只是模型不说话、也不烧 token。

### 两种拦截思路的取舍

| | 拦消息事件 | 本插件（拦 LLM / 结果） |
|---|---|---|
| 作用范围 | 通常全局一个开关 | 按 `unified_msg_origin` 分会话 |
| 拦截位置 | 唤醒检查阶段 `stop_event()` 整条事件 | 三层 LLM/结果钩子，事件本身放行 |
| 静默期 `/help` 等指令 | 一起失灵 | 正常回复 |
| 静默期群聊记录 | 断层 | 不断层 |
| 空 @ 自动搭话 | 拦不住 | 拦得住 |
| 挡主动消息 | 否 | 可选（`mute_block_proactive`） |
| 状态存储 | 配置文件里的一个布尔值 | 插件 KV，按会话存 |

---

## 安装

### 方式一：WebUI 上传

把打包好的 `astrbot_plugin_session_switch.zip` 在 WebUI「插件管理 → 安装插件」里上传。

### 方式二：手动放目录

把整个仓库文件夹放到：

```
<AstrBot 数据目录>/plugins/astrbot_plugin_session_switch/
```

Windows 默认是 `C:\Users\<用户名>\.astrbot\data\plugins\`。

### ⚠️ 装之前先看这条

**如果之前装过别的 `/end`、`/begin` 开关插件，必须先停用或删掉它。**
指令名一样，同时启用会互相抢执行，行为不可预测。

### 装完确认

在 WebUI 里重载插件，日志出现：

```
[session_switch] v1.1.0 已加载 enabled=True 静默会话数=0 白名单数=1
```

就说明起来了。

---

## 指令

| 指令 | 别名 | 作用 |
|---|---|---|
| `/end` | `/mute` | 关闭**本会话**输出，机器人回一句「已关闭输出」 |
| `/begin` | `/unmute` | 恢复**本会话**输出，机器人回一句「已启用输出」 |
| `/switch` | `/mute_status` | 查看本会话当前是开还是关，以及是谁、什么时候关的 |

`/end` 和 `/begin` 都会先回一句确认再闭嘴/恢复，所以你能确认指令收到了。重复 `/end` 会追加「（本来就是关着的）」，重复 `/begin` 追加「（本来就是开着的）」。

---

## 配置项

在 WebUI 插件配置面板里改，改完热生效。

### 开关

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 插件总开关。关掉后 `/end`、`/begin` 都不再生效，机器人照常说话（等于临时停用插件） |
| `mute_block_proactive` | bool | `true` | 闭嘴期间连主动消息也拦掉。主动消息插件不走普通对话链路，关掉这个的话闭嘴期间她仍可能自己冒出来说话 |

### 权限

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `whitelist` | list | `[]` | 谁能用 `/end`、`/begin`。可以填 QQ 号、群号，或完整会话标识（如 `aiocqhttp:GroupMessage:12345`） |
| `allow_admin` | bool | `true` | AstrBot 管理员（`admins_id` 里的人）无视白名单，永远能用 |
| `allow_all` | bool | `false` | 所有人都能用。打开后白名单失效，群里任何人都能让她闭嘴 —— 不推荐 |
| `empty_whitelist_allow` | bool | `false` | 白名单为空时是否允许所有人使用。默认关闭，意味着**装完必须先填白名单**，否则谁都关不了 |

权限判定顺序：`allow_all` → 管理员（`allow_admin`）→ 白名单匹配 → 名单为空时看 `empty_whitelist_allow`。

白名单匹配时会同时拿这四个值去比对，命中任意一个就放行：

- 发送者 ID（`sender_id`）
- 完整会话标识（`unified_msg_origin`）
- 群号（`group_id`）
- 会话 ID（`session_id`）

不知道自己该填什么？直接发一句 `/end`，被拒绝时机器人会把你的 UID 回给你，复制到配置里就行。

### 提示语

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `end_text` | string | `已关闭输出` | `/end` 之后的回复内容 |
| `begin_text` | string | `已启用输出` | `/begin` 之后的回复内容 |
| `end_repeat_hint` | bool | `true` | 重复 `/end` 时追加「（本来就是关着的）」 |
| `begin_repeat_hint` | bool | `true` | 重复 `/begin` 时追加「（本来就是开着的）」 |

---

## 闭嘴期间到底发生了什么

这是最容易误解的地方，逐条说清楚。

### 消息：照常接收

消息照常进适配器、照常打日志、照常写数据库。本插件只掐 LLM 相关钩子，不拦消息事件。

### 模型：不调用，不烧 token

拦截点在 `agent_sub_stages/internal.py` 的 `OnWaitingLLMRequestEvent` —— 这一步在**选择 provider、拼装上下文之前**。所以上下文都不拼，模型更不会被调用。

兜底还有两道：

1. `on_llm_request` —— 覆盖不走上面那条链路的场景
2. `on_decorating_result` —— 只拦模型产出的结果（`is_model_result()` 为真，即 `LLM_RESULT` 和 `AGENT_RUNNER_ERROR`），所以 `/help` 等指令回执在闭嘴期间**照常返回**

### 群聊上下文：不断层

因为消息事件没被拦，内置的 `on_message` 照常跑，`GroupChatContext` 继续累积静默期间的群聊内容；`persist_group_message` 也照写数据库。`/begin` 之后模型能看到这段时间群里聊了什么。

### 私聊：会断层

静默期间没有执行 agent，AstrBot 就不会把你说的话写进 conversation。`/begin` 后模型不知道你之前说了什么。**这是 AstrBot 本身没有私聊旁路记录机制导致的，任何「拦截式」插件都绕不过去。** 介意的话可以在恢复后补一句上下文。

### 仍有极小残留开销

以下两处不属于本插件的拦截范围，静默期间仍会跑：

- **长期记忆类插件**：被动群消息捕获通常在唤醒检查阶段就触发，会把群消息写进长期记忆并走 embedding。按条计费，开销极小，但不是零。
- **自己调模型的插件**：用 `context.llm_generate()` 之类接口独立调模型的插件不走 LLM 钩子，静默期间照样消耗 token。如果很在意，可以在闭嘴期间临时停用这类插件。

---

## 版本兼容

**要求 AstrBot `>= 4.16, < 5`。**

插件对钩子做了降级处理，缺哪个钩子就不注册那一层拦截，不会因为版本差异加载失败：

| 依赖 | 引入版本 | 缺失时的降级行为 |
|---|---|---|
| `on_waiting_llm_request` | 4.16.0 | 少最早那道闸，靠 `on_llm_request` 兜底 |
| `on_llm_request` | 4.x 早期 | 少一层兜底 |
| `on_decorating_result` | 4.x 早期 | 挡不住主动消息 |
| `MessageEventResult.is_model_result()` | 4.24.4 | 自动退回 `is_llm_result()`（不覆盖 `AGENT_RUNNER_ERROR`） |
| `PluginKVStoreMixin` | 4.16.0 | 无降级；4.16 之前插件 KV 接口不一致，未适配 |

4.16 之前的版本（如 4.10）缺少 `on_waiting_llm_request` 且 KV 接口不同，未做兼容。

---

## 常见问题

**Q：`/end` 之后机器人还会自己冒出来说话？**
检查 `mute_block_proactive` 是否为 `true`。另外确认没有别的插件用 `context.send_message()` 之类的接口直接发消息 —— 那种不经过 `on_decorating_result`，拦不住。

**Q：`/end` 提示「你没有权限使用这个指令」？**
白名单没配或没配到你。把提示里的 UID 加到 `whitelist` 里，或者把 `allow_admin` 保持开启并用管理员账号发指令。

**Q：`/end` 提示「还没有配置白名单」？**
`whitelist` 是空的，且 `empty_whitelist_allow` 是 `false`。填个 QQ 号进去，或临时打开 `empty_whitelist_allow`。

**Q：状态重启后会丢吗？**
不会。存在插件 KV 的 `muted_sessions` 键里，`initialize()` 时恢复，`terminate()` 时落盘。

**Q：能同时装别的 `/end` 开关插件吗？**
不能。指令名冲突，必须停用其中一个。

**Q：怎么一次性解除所有会话的静默？**
目前只能逐个会话发 `/begin`。批量解除可以直接清 KV：在 WebUI 的插件数据里把 `muted_sessions` 置空，或重载插件前编辑对应存储。

**Q：支持哪些平台？**
不依赖任何平台专有字段，`unified_msg_origin` 在 AstrBot 支持的所有适配器上都有。QQ（aiocqhttp）、Telegram、Discord、飞书等都可以用。

---

## 开发

插件入口是 `main.py` 里的 `SessionSwitchPlugin`。它继承 `astrbot.api.star.Star`，AstrBot 加载时自动识别（v3.5.19 之后不需要 `@register` 装饰器）。

校验和打包走同一个脚本，只依赖标准库：

```bash
python -m py_compile main.py     # 语法检查
python scripts/build.py --check  # 只校验（CI 的 PR 检查跑这个）
python scripts/build.py          # 校验 + 打包到 dist/
python tests/smoke_test.py       # 冒烟测试（用假的 astrbot 包跑，不需要装 AstrBot）
```

`scripts/build.py` 会检查：`_conf_schema.json` 每项都有 `type`/`description`、`metadata.yaml` 必填字段齐全且 `name` 与目录名一致、`astrbot_version` 是合法 PEP 440 区间、README 说明了全部配置项、打包文件清单完整。

`tests/smoke_test.py` 只依赖标准库，造一个最小的 `astrbot` 桩把插件加载起来，覆盖三种版本形态（钩子齐全 / 缺 `on_waiting_llm_request` / 缺 `is_model_result`）下的权限判定、三层拦截、状态持久化、脏 KV 容错。

仓库结构：

```
astrbot_plugin_session_switch/
├── main.py                    # 插件主体
├── metadata.yaml              # 元数据（插件市场读取）
├── _conf_schema.json          # WebUI 配置面板 schema
├── logo.png                   # 插件图标
├── README.md
├── CHANGELOG.md
├── LICENSE
├── scripts/
│   └── build.py               # 校验 + 打包
├── tests/
│   └── smoke_test.py          # 冒烟测试
└── .github/workflows/
    └── release.yml            # PR 校验；打 v* tag 自动发布 zip
```

发版流程：改 `metadata.yaml` 和 `CHANGELOG.md` 里的版本号 → `git tag v1.1.0 && git push --tags` → Actions 校验通过后自动生成 Release 并附上 `astrbot_plugin_session_switch.zip`。

---

## License

[MIT](LICENSE)
