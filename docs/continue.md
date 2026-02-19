# Continue Agent (cakit)

This document explains how cakit runs Continue CLI (`cn`) and extracts run metadata.

**Versioned Installation**
- `cakit install continue` installs `@continuedev/cli`.
- `cakit install continue --version <npm_version_or_tag>` installs `@continuedev/cli@<version>`.

## API Configuration (`cakit configure continue`)

When required env vars are present, cakit writes `~/.continue/config.yaml` for OpenAI-compatible API mode.

| Env var | Purpose | Required |
| --- | --- | --- |
| `CAKIT_CONTINUE_OPENAI_API_KEY` | API key for Continue model config (fallback: `OPENAI_API_KEY`) | required |
| `CAKIT_CONTINUE_OPENAI_MODEL` | Base chat model name (fallback: `OPENAI_DEFAULT_MODEL`) | required |
| `CAKIT_CONTINUE_OPENAI_BASE_URL` | OpenAI-compatible base URL (fallback: `OPENAI_BASE_URL`) | optional |

Runtime/env resolution for Continue is:
- `--model` (per-run override) first
- then `CAKIT_CONTINUE_OPENAI_*`
- then shared `OPENAI_DEFAULT_MODEL` (for model) / `OPENAI_API_KEY` / `OPENAI_BASE_URL`

If required values are missing, `cakit configure continue` returns `config_path: null` and writes nothing.

## Run Behavior

`cakit run continue "<prompt>"` executes Continue CLI in headless mode:
- command: `cn -p --auto --config <runtime_config> <prompt>`
- each run uses a dedicated `CONTINUE_GLOBAL_DIR` under `/tmp/cakit-continue-<uuid>/` to isolate artifacts
- cakit generates a run-local `config.yaml` from resolved model/API env values

## Image/Video Input

- `cakit run continue --image ...` / `--video ...` is not supported.
- Continue CLI headless mode has no documented generic `--image` / `--video` flags.
- Prompt-path multimodal check:
  - image path in plain prompt text: Continue reports it cannot directly view image binaries.
  - video path in plain prompt text: Continue can inspect file metadata via tools (for example `ffprobe`/shell), but this is not formal `--video` multimodal support.

## Stats Extraction

`cakit run continue` extracts `response`, `models_usage`, `llm_calls`, and `tool_calls` from run artifacts with strict parsing:

1. Read session id from:
   - `<CONTINUE_GLOBAL_DIR>/sessions/sessions.json` (`sessionId` from the last entry)
2. Read the exact matching session file:
   - `<CONTINUE_GLOBAL_DIR>/sessions/<session_id>.json`
3. Parse `history[].message` entries:
   - `models_usage`: aggregate `usage.model` + `usage.prompt_tokens` / `usage.completion_tokens` / `usage.total_tokens`
   - `llm_calls`: number of assistant messages that contain valid `usage`
   - `tool_calls`: sum of assistant `message.toolCalls` lengths
4. `response` uses stdout first, then falls back to the last assistant message content in session history.

Model names are taken only from run artifacts (`history[].message.usage.model`). cakit does not backfill model names from config/env.

## Telemetry and Trajectory

- `telemetry_log`: `<CONTINUE_GLOBAL_DIR>/logs/cn.log`
- `trajectory_path`: formatted, human-readable trace built from `raw_output` and rendered as YAML (no truncation)
