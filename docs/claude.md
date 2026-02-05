# Claude Agent (cakit)

This document explains how cakit runs Claude Code and extracts metadata.

**Sources**
- CLI stdout from `~/.npm-global/bin/claude -p --output-format stream-json --verbose ...` (JSONL-like events, one JSON object per line).
- Environment variables such as `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `CLAUDE_USE_OAUTH`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL`/`ANTHROPIC_DEFAULT_HAIKU_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, `OTEL_EXPORTER_OTLP_ENDPOINT`.

**Image Input**
- `cakit run claude --image <path>` is supported by injecting the image file path(s) into the prompt and letting Claude Code open them via the built-in `Read` tool.
- cakit passes `--add-dir <parent_dir>` so Claude Code can access the image paths, and uses `--` to terminate options (because `--add-dir` is variadic and would otherwise consume the prompt).

**Field Mapping**
- `agent_version`: from `~/.npm-global/bin/claude --version`.
- `runtime_seconds`: from the final `{"type":"result", ...}` payload field `duration_ms / 1000`.
- `response`: from the `result` payload field `result`.
- `models_usage`: from the `result` payload field `modelUsage` (per-model `inputTokens`/`outputTokens`, and `cacheReadInputTokens`/`cacheCreationInputTokens` are added into `prompt_tokens` when present).
- `tool_calls`: count of `{"type":"assistant", "message": {"content": [{"type":"tool_use", ...}, ...]}}` blocks.
- `llm_calls`: from the `result` payload field `num_turns`.
- `total_cost`: from the `result` payload field `total_cost_usd`.
- `telemetry_log`: `OTEL_EXPORTER_OTLP_ENDPOINT` when `CLAUDE_CODE_ENABLE_TELEMETRY` is set (cakit also enables it implicitly when an OTEL endpoint is provided).
- `output_path`/`raw_output`: captured stdout/stderr from the Claude Code run.

**Notes**
- cakit sets `IS_SANDBOX=1` for Claude Code runs so `--dangerously-skip-permissions` can be used in root/sudo environments.
- `cakit configure claude` does not write `~/.claude/settings.json`; Claude Code model selection is controlled via CLI flags and environment variables.
