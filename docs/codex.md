# Codex Agent (cakit)

This document explains how cakit collects Codex CLI metadata.

**Versioned Installation**
- `cakit install codex --version <npm_version_or_tag>` installs `@openai/codex@<version>`.

**Sources**
- CLI stdout from `codex exec --json` (JSONL events).
- Response file from `codex exec --output-last-message <path>` (written under `CAKIT_OUTPUT_DIR`, defaulting to `~/.cache/cakit`).
- Session JSONL file is resolved by exact thread-id match under `$CODEX_HOME/sessions/**/rollout-*<thread_id>.jsonl` and used only for model name lookup (`turn_context.payload.model`).
- Environment variables such as `CODEX_MODEL`, `CODEX_API_BASE`, `CAKIT_CODEX_USE_OAUTH`, `CODEX_OTEL_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Shared OpenAI fallback is supported when agent-specific vars are unset:
  - `OPENAI_API_KEY` -> `CODEX_API_KEY`
  - `OPENAI_BASE_URL` -> `CODEX_API_BASE`
  - `OPENAI_DEFAULT_MODEL` -> `CODEX_MODEL`

**Image Input**
- `cakit run codex --image <path>` is supported by passing the image path(s) to the Codex CLI `--image` flag (multiple images allowed).

**Video Input**
- Codex CLI documentation does not describe video input; treat video input as unsupported.

**Field Mapping**
- `agent_version`: from `codex --version`.
- `runtime_seconds`: wall time of the `codex exec` process.
- `response`: content of the file written by `--output-last-message`.
- `models_usage`:
  - Sum `turn.completed.usage` from CLI stdout.
  - Required fields per turn: `input_tokens`, `cached_input_tokens`, `output_tokens`.
  - `prompt_tokens = input_tokens + cached_input_tokens`, `completion_tokens = output_tokens`.
  - Model name comes from session JSONL `turn_context.payload.model`. If unavailable, the model name is `unknown`.
- `tool_calls`: count of unique tool items from CLI JSON events. We count distinct `item.id` where `item.type` is one of `mcp_tool_call`, `collab_tool_call`, `command_execution`, or `web_search`.
- `llm_calls`: count of `turn.completed` entries with valid `usage` in CLI stdout.
- `telemetry_log`: `CODEX_OTEL_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` when set.
- `output_path`/`raw_output`: captured stdout/stderr from the Codex CLI run.
- `trajectory_path`: formatted, human-readable trace built from the Codex stdout/stderr JSON stream and rendered as YAML (no truncation).

**Notes**
- If `CAKIT_CODEX_USE_OAUTH` is set, cakit expects a login file at `${CODEX_HOME}/auth.json` created by `codex login`.
- For API-key mode, set `CODEX_API_KEY` and `CODEX_API_BASE` if you need a non-default base URL.
- Model priority is: `--model` > `CODEX_MODEL` > `OPENAI_DEFAULT_MODEL`.
- To avoid accidental auth mode selection, cakit removes both `OPENAI_API_KEY` and `CODEX_API_KEY` from the Codex CLI environment when OAuth is enabled.
- If API-key mode is requested but `CODEX_API_KEY` is missing, cakit avoids passing `OPENAI_API_KEY`/`CODEX_API_KEY` to Codex (so an existing OAuth login can still work).
- Codex behavior with Chat Completions-only API bases (no Responses support) has not been tested yet.
