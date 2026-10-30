# Changelog

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.1] - 2026-10-30

### 变更

- 更新文档说明。

## [1.1.0] - 2026-10-30

### 新增

- 新增 `/switch`（别名 `/mute_status`）指令，查看本会话当前开关状态、操作人和操作时间。
- 新增 `mute_block_proactive` 配置，闭嘴期间可连带拦截主动消息。
- 新增 `end_repeat_hint` / `begin_repeat_hint` 配置，控制重复操作时的追加提示。
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
