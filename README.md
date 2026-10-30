# 会话开关

用 `/end` 让机器人闭嘴、`/begin` 让她恢复说话。**按会话独立生效** —— 在哪个群关，就只关那一个，别的群和私聊照常聊天。状态存插件 KV，重启不丢。

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-blue)](https://github.com/AstrBotDevs/AstrBot)
[![version](https://img.shields.io/badge/version-1.1.1-green)](CHANGELOG.md)
[![license](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

---

## 特点

本插件不动消息事件，只在**要调模型**和**要发消息**这两处掐断。所以闭嘴期间：

- 消息照收，指令照用（`/help`、`/switch` 正常回复），群聊记录照攒
- 不调模型、不烧 token —— 拦截点在 `OnWaitingLLMRequestEvent`，早于选 provider 和拼上下文
- `/begin` 后模型能看到静默期间群里聊了什么；私聊会断层，因为静默期不执行 agent，框架就不写 conversation，任何拦截式插件都绕不过去
- 空 @ 自动搭话、主动消息也能一并拦住（`mute_block_proactive`）

**残留开销**：长期记忆类插件在唤醒检查阶段就捕获群消息，按条计费；自己调模型的插件不走 LLM 钩子，静默期照样消耗 token。介意的话闭嘴期间临时停用它们。

---

## 安装

**插件市场**：WebUI → 插件 → 插件市场 → 搜索「会话开关」或 `session_switch` → 点安装。

**WebUI 上传**：插件管理 → 安装插件 → 上传 `astrbot_plugin_session_switch.zip`。

**手动**：把仓库文件夹放到 `<AstrBot 数据目录>/plugins/astrbot_plugin_session_switch/`。Windows 默认是 `C:\Users\<用户名>\.astrbot\data\plugins\`。

> ⚠️ 本插件会与其他带有 `/end`、`/begin` 命令的插件起冲突，同时启用会互相抢执行。

装完重载插件，日志出现这行就算起来了：

```
[session_switch] v1.1.1 已加载 enabled=True 静默会话数=0 白名单数=1
```

---

## 指令

| 指令 | 作用 |
|---|---|
| `/end` | 关闭本会话输出 |
| `/begin` | 恢复本会话输出 |
| `/switch` | 查看本会话状态、谁关的、什么时候 |

`/end` 和 `/begin` 都先回一句确认再执行，所以你能确认指令收到了。三个指令都有别名（`/mute`、`/unmute`、`/mute_status`），效果与主指令完全相同。

---

## 配置

在 WebUI 插件配置面板改，热生效。

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 插件总开关。关掉后 `/end`、`/begin` 都不生效 |
| `mute_block_proactive` | bool | `true` | 闭嘴期间连主动消息也拦掉 |
| `whitelist` | list | `[]` | 谁能用开关。可填 QQ 号、群号或完整会话标识（如 `aiocqhttp:GroupMessage:12345`） |
| `allow_admin` | bool | `true` | AstrBot 管理员无视白名单 |
| `allow_all` | bool | `false` | 所有人都能用，白名单失效 |
| `empty_whitelist_allow` | bool | `false` | 白名单为空时是否允许所有人用 |
| `end_text` | string | `已关闭输出` | `/end` 的回复内容 |
| `begin_text` | string | `已启用输出` | `/begin` 的回复内容 |
| `end_repeat_hint` | bool | `true` | 重复 `/end` 时追加「（本来就是关着的）」 |
| `begin_repeat_hint` | bool | `true` | 重复 `/begin` 时追加「（本来就是开着的）」 |

**权限判定顺序**：`allow_all` → 管理员 → 白名单匹配 → 名单为空时看 `empty_whitelist_allow`。

白名单会同时比对发送者 ID、完整会话标识、群号、会话 ID，命中任意一个就放行。不知道填什么就直接发 `/end`，被拒绝时机器人会把你的 UID 回给你。

---

## 版本要求

**AstrBot `>= 4.16, < 5`。**

插件对钩子做了降级处理，缺哪个就不注册那一层，不会加载失败：

| 依赖 | 引入版本 | 缺失时 |
|---|---|---|
| `on_waiting_llm_request` | 4.16.0 | 靠 `on_llm_request` 兜底 |
| `on_llm_request` | 4.x 早期 | 少一层兜底 |
| `on_decorating_result` | 4.x 早期 | 挡不住主动消息 |
| `is_model_result()` | 4.24.4 | 退回 `is_llm_result()` |

4.16 之前 KV 接口不同，未适配。

---

## 更新

- **从插件市场装的**：WebUI 插件列表点更新按钮。
- **从 GitHub 链接装的**：同上，会自动拉最新代码。
- **从 zip 上传装的**：更新按钮是灰的，重新下载新 zip 覆盖安装。

---

## 开发

入口是 `main.py` 的 `SessionSwitchPlugin`，继承 `astrbot.api.star.Star`（v3.5.19 之后不需要 `@register`）。改完代码跑这两条：

```bash
python scripts/build.py --check   # 校验配置、元数据、打包清单
python tests/smoke_test.py        # 冒烟测试，不需要装 AstrBot
```

打包用 `python scripts/build.py`，产物在 `dist/`。

---

## License

[MIT](LICENSE)
