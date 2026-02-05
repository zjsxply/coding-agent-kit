# Codex Agent (cakit)

This document explains how cakit collects Codex CLI metadata.

**Sources**
- CLI stdout from `codex exec --json` (JSONL events).
- Response file from `codex exec --output-last-message <path>` (written under `CAKIT_OUTPUT_DIR`, defaulting to `~/.cache/cakit`).
- Session JSONL file at `$CODEX_HOME/sessions/**/rollout-*<thread_id>.jsonl` when available.
- Environment variables such as `CODEX_MODEL`, `CODEX_API_BASE`, `CODEX_USE_OAUTH`, `CODEX_OTEL_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`.

**Field Mapping**
- `agent_version`: from `codex --version`.
- `runtime_seconds`: wall time of the `codex exec` process.
- `response`: content of the file written by `--output-last-message`.
- `models_usage`:
  - Prefer session JSONL: read the last `event_msg` with `payload.type == "token_count"` and use `payload.info.total_token_usage` (`input_tokens`, `output_tokens`, `total_tokens`).
  - Model name comes from the `turn_context` payload field `model`; if missing, use `unknown`.
  - If no session file is found, parse `usage` from CLI JSON events.
- `tool_calls`: best-effort count of JSON payloads that look like tool calls (keys such as `tool`, `tool_name`, `tool_call`, `toolUse`, etc.).
- `llm_calls`: count of distinct `token_count` totals in the session JSONL (deduped by `input_tokens`, `output_tokens`, `total_tokens`). If the session file is unavailable, fallback to the number of `turn.completed`/`turn.failed` events in CLI JSON output.
- `telemetry_log`: `CODEX_OTEL_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` when set.
- `output_path`/`raw_output`: captured stdout/stderr from the Codex CLI run.

**Notes**
- If `CODEX_USE_OAUTH` is set, cakit expects a login file at `${CODEX_HOME}/auth.json` created by `codex login`.
- For API-key mode, set `CODEX_API_KEY` and `CODEX_API_BASE` if you need a non-default base URL.
- To avoid accidental auth mode selection, cakit removes both `OPENAI_API_KEY` and `CODEX_API_KEY` from the Codex CLI environment when OAuth is enabled.
- If API-key mode is requested but `CODEX_API_KEY` is missing, cakit avoids passing `OPENAI_API_KEY`/`CODEX_API_KEY` to Codex (so an existing OAuth login can still work).
