# Codex Agent (cakit)

This document explains how cakit collects Codex CLI metadata.

**Versioned Installation**
- `cakit install codex --version <npm_version_or_tag>` installs `@openai/codex@<version>`.

**Sources**
- CLI stdout from `codex exec --json` (JSONL events).
- Response file from `codex exec --output-last-message <path>` (written under `CAKIT_OUTPUT_DIR`, defaulting to `~/.cache/cakit`).
- Session rollout JSONL files under `$CODEX_HOME/sessions/**/rollout-*.jsonl`.
  cakit builds the exact thread family from `session_meta.payload.id` plus `session_meta.payload.source.subagent.thread_spawn.parent_thread_id`, so spawned subagents are included in the same run stats.
- Environment variables such as `CODEX_MODEL`, `CODEX_BASE_URL`, `OPENAI_BASE_URL`, `CAKIT_CODEX_USE_OAUTH`, `CODEX_OTEL_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Shared OpenAI fallback is supported when agent-specific API key/model/base-URL vars are unset:
  - `OPENAI_API_KEY` -> `CODEX_API_KEY`
  - `OPENAI_BASE_URL` -> `CODEX_BASE_URL`
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
  - Primary source: exact rollout family for the main thread plus spawned subagent threads.
  - Per thread, cakit reads the last non-null `event_msg.payload.info.total_token_usage` from `token_count` events.
  - Required fields per snapshot: `input_tokens`, `cached_input_tokens`, `output_tokens`.
  - `prompt_tokens = input_tokens + cached_input_tokens`, `completion_tokens = output_tokens`.
  - Model name comes from rollout `turn_context.payload.model`.
  - Fallback when rollout family cannot be resolved: aggregate `turn.completed.usage` from CLI stdout.
- `tool_calls`:
  - Primary source: count of rollout `response_item.payload.type == "function_call"` across the exact thread family.
  - Fallback: CLI JSON events (`response_item` function calls, then legacy tool-item IDs if needed).
- `llm_calls`:
  - Primary source: per thread, count distinct cumulative `token_count` snapshots whose total usage changes; then sum across the exact thread family.
  - This avoids undercounting spawned subagents and avoids double-counting repeated `token_count` emissions with unchanged totals.
  - Fallback: count `turn.completed` entries in CLI stdout.
- `telemetry_log`: `CODEX_OTEL_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` when set.
- `output_path`/`raw_output`: captured stdout/stderr from the Codex CLI run.
- `trajectory_path`:
  - when rollout family is available, cakit writes a family-aware YAML trace containing CLI stdout plus every exact rollout file for the main thread and spawned subagent threads
  - otherwise it falls back to the formatted stdout/stderr trace

**Notes**
- If `CAKIT_CODEX_USE_OAUTH` is set, cakit expects a login file at `${CODEX_HOME}/auth.json` created by `codex login`.
- For API-key mode, set `CODEX_API_KEY` and `CODEX_BASE_URL` if you need a non-default base URL; cakit forwards the resolved value to Codex via `OPENAI_BASE_URL` at runtime.
- If you need unsupported custom Codex settings after `cakit configure codex`, set `CAKIT_CONFIGURE_POST_COMMAND`; the hook receives `CAKIT_CONFIG_PATH` for post-processing `config.toml`.
- To enable upstream multi-agent via that hook in a simple fresh-config setup:

```bash
export CAKIT_CONFIGURE_POST_COMMAND='if [ "$CAKIT_CONFIGURE_AGENT" = "codex" ]; then printf "\n[features]\nmulti_agent = true\n" >> "$CAKIT_CONFIG_PATH"; fi'
cakit install codex
```

- If your config already contains `[features]`, edit the existing section instead of appending a duplicate block.
- `cakit run codex` currently invokes `codex exec --dangerously-bypass-approvals-and-sandbox`, so sandbox config keys written to `config.toml` (for example `[sandbox_workspace_write].network_access = false`) are not enforced during `cakit run codex`. Top-level config such as `web_search = "disabled"` is still respected.
- Model priority is: `--model` > `CODEX_MODEL` > `OPENAI_DEFAULT_MODEL`.
- To avoid accidental auth mode selection, cakit removes both `OPENAI_API_KEY` and `CODEX_API_KEY` from the Codex CLI environment when OAuth is enabled.
- If API-key mode is requested but `CODEX_API_KEY` is missing, cakit avoids passing `OPENAI_API_KEY`/`CODEX_API_KEY` to Codex (so an existing OAuth login can still work).
- Codex behavior with Chat Completions-only API bases (no Responses support) has not been tested yet.
