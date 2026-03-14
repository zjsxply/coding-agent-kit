# Claude Agent (cakit)

This document explains how cakit runs Claude Code and extracts metadata.

**Installation**
- `cakit install claude` runs Anthropic's official install script: `curl -fsSL https://claude.ai/install.sh | bash`.
- `cakit install claude --version <installer_selector>` runs the same script with a version selector: `curl -fsSL https://claude.ai/install.sh | bash -s -- <installer_selector>`.
- Upstream documentation still lists `npm install -g @anthropic-ai/claude-code` for compatibility/migration, but marks npm installation as deprecated. cakit tries the native script installer first and falls back to that npm package only if the script path fails.
- `--scope user|global` does not affect the primary script path. It only affects the npm fallback path if cakit has to use it.

**Sources**
- CLI stdout from `claude -p --output-format stream-json --verbose ...` (JSONL-like events, one JSON object per line).
- Environment variables such as `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `CAKIT_CLAUDE_USE_OAUTH`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL`/`ANTHROPIC_DEFAULT_HAIKU_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_ENABLE_AGENT_TEAMS`, `OTEL_EXPORTER_OTLP_ENDPOINT`.

**Image Input**
- `cakit run claude --image <path>` is supported by injecting the image file path(s) into the prompt and letting Claude Code open them via the built-in `Read` tool.
- cakit passes `--add-dir <parent_dir>` so Claude Code can access the image paths, and uses `--` to terminate options (because `--add-dir` is variadic and would otherwise consume the prompt).

**Video Input**
- Claude Code CLI documentation does not describe video input; treat video input as unsupported.

**Reasoning Effort**
- `cakit run claude ... --reasoning-effort <value>` is supported with values: `low`, `medium`, `high`, `max`.
- cakit forwards this to Claude Code CLI via `CLAUDE_CODE_EFFORT_LEVEL=<value>` (no prompt injection).
- `max` availability depends on the selected Claude model; unsupported models may return an upstream error.

**Field Mapping**
- `agent_version`: from `claude --version`.
- `runtime_seconds`: from the final `{"type":"result", ...}` payload field `duration_ms / 1000`.
- `response`: from the `result` payload field `result`.
- `models_usage`:
  - default source: `result.modelUsage`
  - when `result.modelUsage` is empty on a normal non-team run, cakit falls back to assistant
    stream messages (`message.usage` + `message.model`) from CLI stdout
  - when the exact Claude transcript family exists under `~/.claude/projects/.../subagents`, cakit aggregates
    deduplicated assistant messages from the lead transcript plus child transcripts instead, so Agent Teams /
    runtime-created subagents are included in `models_usage`
- `tool_calls`:
  - default source: count of `tool_use` blocks in CLI stdout assistant messages
  - when transcript-family aggregation is available, count `tool_use` blocks across the deduplicated lead + child transcripts
- `llm_calls`:
  - default source: `result.num_turns`
  - when transcript-family aggregation is available, count deduplicated assistant message IDs across the lead + child transcripts
- `total_cost`: from the `result` payload field `total_cost_usd`.
- `telemetry_log`: `OTEL_EXPORTER_OTLP_ENDPOINT` when telemetry is enabled and the endpoint is present.
- `output_path`/`raw_output`: captured stdout/stderr from the Claude Code run.
- `trajectory_path`:
  - when transcript files are available, cakit writes a family-aware YAML trace containing CLI stdout plus the main transcript and any Agent Teams child transcripts
  - otherwise it falls back to the formatted stdout/stderr trace

**Notes**
- cakit sets `IS_SANDBOX=1` for Claude Code runs so `--dangerously-skip-permissions` can be used in root/sudo environments.
- `CAKIT_CLAUDE_USE_OAUTH` is the cakit switch for choosing OAuth when both API key and auth token are present.
- To enable upstream Agent Teams, set `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1`; cakit passes it through unchanged.
- Telemetry behavior: if `CLAUDE_CODE_ENABLE_TELEMETRY` is unset and `OTEL_EXPORTER_OTLP_ENDPOINT` is set, cakit enables telemetry for the run; if `CLAUDE_CODE_ENABLE_TELEMETRY` is explicitly set, that value is respected. When explicitly set to a falsey value, cakit also unsets `OTEL_EXPORTER_OTLP_ENDPOINT` for the child process.
- cakit always sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` for Claude runs.
- Standard network actions (for example `curl`) generally work when the runtime/network policy allows them.
- With third-party Anthropic-compatible APIs, Web Search tool support is typically unavailable even if basic network access works.
- `cakit configure claude` does not write `~/.claude/settings.json`; Claude Code model selection is controlled via CLI flags and environment variables.
