# Changelog

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.2.1] - 2026-11-02

### 修复

- 修复了重复开启/关闭指令的反馈输出，不再跟开启/关闭指令的反馈输出相连

## [1.2.0] - 2026-11-01

### 新增

- 新增 `end_command` / `begin_command` / `status_command` 配置，三个指令名都能改，比如把 `/end` 改成 `/闭嘴`。填的时候带不带斜杠都行。
- 新增 `end_repeat_text` / `begin_repeat_text` 配置，重复操作时的回复文案可以自定义。

### 变更

- 自定义指令名会在 `initialize()` 里直接应用到运行时，与 AstrBot「指令管理」的改名走同一套手法，保存配置热重载后立即生效。
- 指令名清洗规则：去掉前缀斜杠和空白；含空格、为空、或三个名字重复时退回默认值并在日志里 warning。
- 自定义指令名撞上别的指令的内置别名（`mute` / `unmute` / `mute_status`）时也退回默认，避免两个 handler 抢同一条消息。
- `/switch` 的提示语跟随自定义的恢复指令名，不再硬编码 `/begin`。
- `initialize()` 日志补上当前生效的指令名。

## [1.1.1] - 2026-10-30

### 变更

- 更新文档说明。

## [1.1.0] - 2026-10-30

### 新增

- 新增 `/switch`（别名 `/mute_status`）指令，查看本会话当前开关状态、操作人和操作时间。
- 新增 `mute_block_proactive` 配置，闭嘴期间可连带拦截主动消息。
- 新增 `end_repeat_hint` / `begin_repeat_hint` 配置，控制重复操作时是否给提示。
- 新增 `logo.png` 插件图标。

### 变更

- **兼容范围明确为 AstrBot `>=4.16,<5`**，`metadata.yaml` 补上 `astrbot_version`、`support_platforms`、`short_desc`、`tags`。
- 三个事件钩子改为按名称探测后注册：当前版本没有的钩子自动跳过，插件仍能加载，只是少一层拦截。
- 结果判定改为「`is_model_result()` 优先，退回 `is_llm_result()`」，兼容 AstrBot 4.24.4 之前没有 `is_model_result()` 的版本。
- 配置项读取全部加兜底，配置缺项时不再抛异常。
- `initialize()` 日志带出版本号、静默会话数、白名单数。

### 修复

- 修复插件卸载/重载时静默状态可能未落盘的问题（`terminate()` 现在必定保存）。
- 修复 KV 中残留非 dict 脏数据时插件启动报错的问题。

## [1.0.0] - 2026-10-29

### 新增

- 首个版本。
- `/end`（`/mute`）关闭本会话输出，`/begin`（`/unmute`）恢复。
- 按 `unified_msg_origin` 分会话独立控制，状态存插件 KV。
- 三层拦截：`on_waiting_llm_request`、`on_llm_request`、`on_decorating_result`。
- 权限四种模式：白名单、管理员、所有人、白名单为空时兜底。
- 12 项可配置项（开关、权限、提示语）。
