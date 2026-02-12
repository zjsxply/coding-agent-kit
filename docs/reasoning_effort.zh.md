# 推理强度参数（`cakit run --reasoning-effort`）

本文说明 cakit 如何把统一的 `--reasoning-effort` 参数映射到各 coding agent。

若某个 agent 在 cakit 中不支持推理强度/思考开关，传入 `--reasoning-effort` 会报错并返回退出码 `2`。

本表核对时间为 **2026 年 2 月 12 日**。

- 开源 agent（`codex`、`gemini`、`kimi`、`qwen`、`openhands`、`swe-agent`、`trae-oss`）：基于上游源码核对。
- 非开源 agent（`claude`、`cursor`、`copilot`）：基于官方 CLI 文档核对。

| Agent | cakit 支持的取值 | cakit 映射行为 | 上游状态 |
| --- | --- | --- | --- |
| `claude` | `low`、`medium`、`high`、`max` | 为 `claude` CLI 设置 `CLAUDE_CODE_EFFORT_LEVEL=<value>` | 闭源 CLI；cakit 映射依据官方文档 |
| `codex` | `minimal`、`low`、`medium`、`high`、`xhigh` | 在 `codex exec` 上追加 `-c model_reasoning_effort=<value>` | 上游 SDK/CLI 支持通过 `--config` 设置 `model_reasoning_effort` |
| `cursor` | 不支持 | cakit 中不支持 | 闭源 CLI；文档未提供 reasoning/thinking 开关 |
| `copilot` | 不支持 | cakit 中不支持 | 闭源 CLI；文档未提供 reasoning/thinking 开关 |
| `gemini` | `cakit run` 中不支持 | cakit 中不支持 | 上游可通过 model config aliases/settings 的 `thinkingConfig` 控制思考，但没有独立的按次运行 reasoning-effort 参数 |
| `kimi` | `thinking`、`none` | 在 `kimi` 命令上追加 `--thinking` / `--no-thinking` | 上游 CLI 直接提供 `--thinking/--no-thinking` |
| `qwen` | `cakit run` 中不支持 | cakit 中不支持 | 上游在配置中支持 `model.generationConfig.reasoning`（以及 provider `extra_body`），但没有独立的按次运行 reasoning-effort 参数 |
| `openhands` | `cakit run` 中不支持 | cakit 中不支持 | 上游在 LLM 配置/环境变量中支持 `reasoning_effort`（`LLM_REASONING_EFFORT`），但无独立 reasoning-effort CLI 参数 |
| `swe-agent` | `cakit run` 中不支持 | cakit 中不支持 | 上游可通过 `agent.model.completion_kwargs` 透传 provider 的 reasoning 参数，但没有统一的 reasoning-effort CLI 参数 |
| `trae-oss` | 不支持 | cakit 中不支持 | 上游 CLI/配置中没有 reasoning-effort 设置（仅有 `sequentialthinking` 工具） |
