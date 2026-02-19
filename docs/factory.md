# Factory Agent (cakit)

This document explains how cakit installs and runs Factory Droid CLI (`droid`).

## Install

Install latest:

```bash
cakit install factory
```

Install a specific version:

```bash
cakit install factory --version <factory_cli_version>
```

cakit behavior:
- Without `--version`, cakit runs the official installer script: `curl -fsSL https://app.factory.ai/cli | sh`
- With `--version`, cakit downloads exact release binaries from `https://downloads.factory.ai/factory-cli/releases/<version>/...` and verifies SHA-256 checksums before installing.

## Configuration

`cakit configure factory` is a no-op (`config_path: null`).

Environment variable mapping for `cakit run factory`:

| Environment variable | Meaning | Requirement |
| --- | --- | --- |
| `FACTORY_API_KEY` | Factory API key for API auth | optional (required if not using OAuth login) |
| `FACTORY_API_BASE_URL` | Optional upstream API base URL override | optional |
| `FACTORY_TOKEN` | Optional alternate token env name used in some CI workflows | optional |
| `CAKIT_FACTORY_MODEL` | cakit default model for `droid exec --model` (fallback for BYOK: `OPENAI_DEFAULT_MODEL`) | optional |
| `CAKIT_FACTORY_BYOK_API_KEY` | cakit BYOK upstream API key (`customModels[].apiKey`, fallback: `OPENAI_API_KEY`) | optional |
| `CAKIT_FACTORY_BYOK_BASE_URL` | cakit BYOK upstream base URL (`customModels[].baseUrl`, fallback: `OPENAI_BASE_URL`) | optional |
| `CAKIT_FACTORY_BYOK_PROVIDER` | cakit BYOK provider (`openai` / `anthropic` / `generic-chat-completion-api`) | optional (auto-inferred when omitted) |
| `FACTORY_LOG_FILE` | Optional upstream CLI log file path | optional |
| `FACTORY_DISABLE_KEYRING` | Optional keyring-disable switch for headless envs | optional |

When `CAKIT_FACTORY_BYOK_API_KEY` + `CAKIT_FACTORY_BYOK_BASE_URL` + `CAKIT_FACTORY_MODEL` are set, cakit writes/updates `~/.factory/settings.json` `customModels` and runs Droid with the generated `custom:...` model reference.

When BYOK mode is active, cakit also supports shared fallback:
- `OPENAI_API_KEY` -> `CAKIT_FACTORY_BYOK_API_KEY`
- `OPENAI_BASE_URL` -> `CAKIT_FACTORY_BYOK_BASE_URL`
- `OPENAI_DEFAULT_MODEL` -> `CAKIT_FACTORY_MODEL`

Factory authentication is still required even when using BYOK custom models. Use OAuth (`droid` then `/login`) or set a valid `FACTORY_API_KEY`.

## Image and Video Input

- `cakit run factory --image <path>` is supported.
  - cakit injects local paths into prompt text and instructs Droid to open files via the `Read` tool.
- `cakit run factory --video <path>` is unsupported.
  - `droid exec` has no documented generic `--video` flag.

## Reasoning Effort

`cakit run factory --reasoning-effort <value>` maps directly to `droid exec --reasoning-effort <value>`.

Supported values in cakit:
- `off`, `none`, `low`, `medium`, `high`

## Stats Extraction

`cakit run factory` uses strict parsing with exact run artifacts:

1. Parse the exact `{"type":"result", ...}` payload from `droid exec --output-format json`.
2. Extract:
   - `response` from `result`
   - `llm_calls` from `num_turns`
   - token usage from `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`
3. Extract exact `session_id` from the same result payload.
4. Resolve model name from exact session settings file:
   - `~/.factory/sessions/**/<session_id>.settings.json`
   - field: `model`
5. Resolve tool calls from exact session transcript file:
   - `~/.factory/sessions/**/<session_id>.jsonl`
   - count `type == "tool_call"` events (and `hook_event_name == "PreToolUse"` if present in transcript data)
6. Build `models_usage` with model from run artifacts only; no backfill from config/env inputs.

If required stats fields cannot be extracted exactly, cakit returns non-zero for the run.

`trajectory_path` points to a formatted, human-readable YAML trace generated from raw CLI output.
