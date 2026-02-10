# 推理强度参数（`cakit run --reasoning-effort`）

本文说明 cakit 如何把统一的 `--reasoning-effort` 参数映射到各 coding agent。

若某个 agent 在 cakit 中不支持推理强度/思考开关，传入 `--reasoning-effort` 会报错并返回退出码 `2`。

本表基于官方 CLI 文档核对，时间为 **2026 年 2 月 9 日**。

| Agent | cakit 支持的取值 | cakit 映射行为 |
| --- | --- | --- |
| `claude` | `low`、`medium`、`high`、`max` | 为 `claude` CLI 设置 `CLAUDE_CODE_EFFORT_LEVEL=<value>` |
| `codex` | `minimal`、`low`、`medium`、`high`、`xhigh` | 在 `codex exec` 上追加 `-c model_reasoning_effort=<value>` |
| `cursor` | 不支持 | cursor-agent CLI 文档中未提供 reasoning effort / thinking 开关 |
| `copilot` | 不支持 | Copilot CLI 文档中未提供 reasoning effort / thinking 开关 |
| `gemini` | 不支持 | Gemini CLI headless 参数中未提供 reasoning effort / thinking 开关 |
| `kimi` | `thinking`、`none` | 在 `kimi` 命令上追加 `--thinking` / `--no-thinking` |
| `qwen` | `cakit run` 中不支持 | Qwen 文档在 settings 中提供 `model.generationConfig.extra_body.enable_thinking`，但未提供按次运行参数 |
| `openhands` | 不支持 | OpenHands headless CLI 文档中未提供按次运行 reasoning effort 参数 |
| `swe-agent` | 不支持 | SWE-agent CLI 文档中未提供按次运行 reasoning effort 参数 |
| `trae-oss` | 不支持 | Trae CLI 文档中未提供按次运行 reasoning effort 参数 |
