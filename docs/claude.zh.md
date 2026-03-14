# Claude Agent（cakit）

本文说明 cakit 如何运行 Claude Code 并提取统计信息。

**安装版本**
- `cakit install claude --version <npm_version_or_tag>` 会安装 `@anthropic-ai/claude-code@<version>`。

**数据来源**
- `claude -p --output-format stream-json --verbose ...` 的 stdout（每行一个 JSON 对象，类似 JSONL）。
- 环境变量：`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`CAKIT_CLAUDE_USE_OAUTH`、`ANTHROPIC_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL`/`ANTHROPIC_DEFAULT_HAIKU_MODEL`、`CLAUDE_CODE_SUBAGENT_MODEL`、`CLAUDE_CODE_ENABLE_AGENT_TEAMS`、`OTEL_EXPORTER_OTLP_ENDPOINT` 等。

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
- `agent_version`：来自 `claude --version`。
- `runtime_seconds`：来自最终 `{"type":"result", ...}` 的 `duration_ms / 1000`。
- `response`：来自 `result` 负载的 `result` 字段。
- `models_usage`：
  - 默认来源是 `result.modelUsage`
  - 如果普通非-team 运行里的 `result.modelUsage` 为空，cakit 会回退到 CLI stdout
    assistant stream 消息里的 `message.usage` + `message.model`
  - 当 `~/.claude/projects/.../subagents` 下存在本次运行的精确 transcript family 时，
    cakit 会改为聚合 lead transcript 与 child transcript 中去重后的 assistant 消息，
    从而把 Agent Teams / 运行时创建的子 agent 一并计入 `models_usage`
- `tool_calls`：
  - 默认来源是 CLI stdout assistant 消息里的 `tool_use`
  - 当可用 transcript family 聚合时，会统计去重后的 lead + child transcript 中所有 `tool_use`
- `llm_calls`：
  - 默认来源是 `result.num_turns`
  - 当可用 transcript family 聚合时，会统计 lead + child transcript 中去重后的 assistant message id 数量
- `total_cost`：来自 `result` 负载的 `total_cost_usd`。
- `telemetry_log`：当 telemetry 处于启用状态且存在 `OTEL_EXPORTER_OTLP_ENDPOINT` 时，返回该 endpoint。
- `output_path`/`raw_output`：本次运行捕获的 stdout/stderr。
- `trajectory_path`：
  - 当 transcript 文件可用时，cakit 会写入 family-aware 的 YAML 轨迹，包含 CLI stdout、主 transcript，以及 Agent Teams 的 child transcript
  - 否则回退到格式化后的 stdout/stderr 轨迹

**备注**
- cakit 会为 Claude Code 运行设置 `IS_SANDBOX=1`，以便在 root/sudo 环境下使用 `--dangerously-skip-permissions`。
- `CAKIT_CLAUDE_USE_OAUTH` 用于在同时存在 API key 和 auth token 时选择 OAuth。
- 如需启用上游 Agent Teams，请设置 `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1`；cakit 会原样透传该变量。
- telemetry 行为：若未设置 `CLAUDE_CODE_ENABLE_TELEMETRY` 且设置了 `OTEL_EXPORTER_OTLP_ENDPOINT`，cakit 会在本次运行中自动启用 telemetry；若显式设置了 `CLAUDE_CODE_ENABLE_TELEMETRY`，则以该值为准。若显式设置为假值，cakit 还会在子进程中移除 `OTEL_EXPORTER_OTLP_ENDPOINT`。
- cakit 在运行 Claude 时会固定设置 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`。
- 在运行环境和网络策略允许时，常规联网动作（例如 `curl`）通常可用。
- 使用第三方 Anthropic 兼容 API 时，即使基础联网可用，Web Search 工具通常也不可用。
- `cakit configure claude` 不会写入 `~/.claude/settings.json`；Claude Code 的模型选择由命令行参数与环境变量控制。
