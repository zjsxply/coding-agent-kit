# Claude Agent（cakit）

本文说明 cakit 如何运行 Claude Code 并提取统计信息。

**数据来源**
- `~/.npm-global/bin/claude -p --output-format stream-json --verbose ...` 的 stdout（每行一个 JSON 对象，类似 JSONL）。
- 环境变量：`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`CAKIT_CLAUDE_USE_OAUTH`、`ANTHROPIC_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL`/`ANTHROPIC_DEFAULT_HAIKU_MODEL`、`CLAUDE_CODE_SUBAGENT_MODEL`、`OTEL_EXPORTER_OTLP_ENDPOINT` 等。

**图像输入**
- `cakit run claude --image <path>`：cakit 会把图片路径注入到 prompt，并让 Claude Code 通过内置 `Read` 工具读取图片文件。
- cakit 会传 `--add-dir <父目录>` 以允许 Claude Code 访问这些路径，并在 prompt 前加 `--` 结束参数（因为 `--add-dir` 是可变参数，可能把 prompt 当成目录吃掉）。

**视频输入**
- Claude Code CLI 文档未描述视频输入；按不支持处理。

**推理强度参数**
- `cakit run claude ... --reasoning-effort <value>` 支持：`low`、`medium`、`high`、`max`。
- cakit 通过环境变量 `CLAUDE_CODE_EFFORT_LEVEL=<value>` 传给 Claude Code CLI（不做 prompt 注入）。
- `max` 是否可用取决于所选 Claude 模型；模型不支持时会返回上游错误。

**字段映射**
- `agent_version`：来自 `~/.npm-global/bin/claude --version`。
- `runtime_seconds`：来自最终 `{"type":"result", ...}` 的 `duration_ms / 1000`。
- `response`：来自 `result` 负载的 `result` 字段。
- `models_usage`：来自 `result` 负载的 `modelUsage`（逐模型的 `inputTokens`/`outputTokens`，以及必须存在的 `cacheReadInputTokens`/`cacheCreationInputTokens`，会加到 `prompt_tokens` 里）。
- `tool_calls`：统计 `{"type":"assistant", "message": {"content": [{"type":"tool_use", ...}, ...]}}` 的 `tool_use` 块数量。
- `llm_calls`：来自 `result` 负载的 `num_turns`。
- `total_cost`：来自 `result` 负载的 `total_cost_usd`。
- `telemetry_log`：当同时设置 `CLAUDE_CODE_ENABLE_TELEMETRY` 和 `OTEL_EXPORTER_OTLP_ENDPOINT` 时，返回该 endpoint。
- `output_path`/`raw_output`：本次运行捕获的 stdout/stderr。

**备注**
- cakit 会为 Claude Code 运行设置 `IS_SANDBOX=1`，以便在 root/sudo 环境下使用 `--dangerously-skip-permissions`。
- `CAKIT_CLAUDE_USE_OAUTH` 用于在同时存在 API key 和 auth token 时选择 OAuth。
- cakit 在运行 Claude 时会固定设置 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`。
- 在运行环境和网络策略允许时，常规联网动作（例如 `curl`）通常可用。
- 使用第三方 Anthropic 兼容 API 时，即使基础联网可用，Web Search 工具通常也不可用。
- `cakit configure claude` 不会写入 `~/.claude/settings.json`；Claude Code 的模型选择由命令行参数与环境变量控制。
