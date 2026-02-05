# Codex Agent（cakit）

本文说明 cakit 如何收集 Codex CLI 的统计与元信息。

**数据来源**
- `codex exec --json` 的 stdout（JSONL 事件）。
- `codex exec --output-last-message <path>` 输出的响应文件（写入 `CAKIT_OUTPUT_DIR`，默认 `~/.cache/cakit`）。
- `$CODEX_HOME/sessions/**/rollout-*<thread_id>.jsonl` 的会话 JSONL（若存在）。
- 环境变量，例如 `CODEX_MODEL`、`CODEX_API_BASE`、`CODEX_USE_OAUTH`、`CODEX_OTEL_ENDPOINT`、`OTEL_EXPORTER_OTLP_ENDPOINT`。

**字段映射**
- `agent_version`：来自 `codex --version`。
- `runtime_seconds`：`codex exec` 进程的墙钟耗时。
- `response`：`--output-last-message` 输出文件的内容。
- `models_usage`：
  - 优先读取会话 JSONL：找到最后一个 `event_msg` 且 `payload.type == "token_count"`，使用 `payload.info.total_token_usage`（`input_tokens`/`output_tokens`/`total_tokens`）。
  - 模型名来自 `turn_context` 的 `model` 字段；若缺失则回退到 `CODEX_MODEL` 或 `gpt-5-codex`。
  - 若找不到会话文件，则从 CLI JSON 事件中的 `usage` 解析。
- `tool_calls`：尽力统计，扫描 JSON 负载中类似工具调用的字段（`tool`、`tool_name`、`tool_call`、`toolUse` 等）。
- `llm_calls`：会话 JSONL 中 `token_count` 的去重计数（按 `input_tokens`/`output_tokens`/`total_tokens` 去重）；若会话文件不可用，则回退为 CLI JSON 输出中的 `turn.completed`/`turn.failed` 数量。
- `telemetry_log`：若设置了 `CODEX_OTEL_ENDPOINT` 或 `OTEL_EXPORTER_OTLP_ENDPOINT`，则返回该 endpoint。
- `output_path`/`raw_output`：本次运行捕获的 stdout/stderr。

**备注**
- 若设置了 `CODEX_USE_OAUTH`，cakit 会要求 `${CODEX_HOME}/auth.json`（由 `codex login` 生成）。
