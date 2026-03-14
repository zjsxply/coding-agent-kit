# Cursor Agent (cakit)

This document explains how cakit installs and runs Cursor Agent CLI (`cursor-agent`).

## Install

`cakit install cursor` uses Cursor's official install script:

```bash
curl -fsS https://cursor.com/install | bash
```

- Default install (no `--version`) installs the latest upstream build.
- Versioned install is supported:

```bash
cakit install cursor --version <cursor_build_id>
```

When a version is specified, cakit downloads the matching Cursor agent package and updates `~/.local/bin/cursor-agent` symlink.

## Configure

`cakit configure cursor` is a no-op (`config_path: null`).

## Run Behavior

`cakit run cursor "<prompt>"` runs:

```bash
cursor-agent -p "<prompt>" --print --output-format stream-json --force
```

- Optional model override: `cakit run cursor --model <model>`
- Model priority: `--model` > `CURSOR_MODEL` > `OPENAI_DEFAULT_MODEL`
- Optional API endpoint override: `CURSOR_BASE_URL` (fallback: `OPENAI_BASE_URL`)
- API key: `CURSOR_API_KEY` (fallback: `OPENAI_API_KEY`)

Implementation detail:
- cakit keeps `CURSOR_BASE_URL` as the user-facing env name and passes the resolved value to Cursor via `--endpoint`.
- This matches Cursor's public CLI surface; the installed upstream bundle also contains `CURSOR_API_ENDPOINT` handling internally.

Image/video flags are not supported in cakit for Cursor (`--image` / `--video` return unsupported).

## Stats Extraction

cakit parses stream-JSON output with strict event paths:
- `response`:
  - primary: last `type == "result"` payload field `result`
  - fallback: last `type == "assistant"` payload `message.content[*].text`
- `tool_calls`:
  - count unique `call_id` from `type == "tool_call"` with `subtype == "started"`
  - fallback to unique `call_id` across all `type == "tool_call"` payloads
- `llm_calls`:
  - primary: count unique `model_call_id` across `type == "assistant"` and `type == "tool_call"` payloads
  - fallback: count of `type == "assistant"` payloads
- `models_usage`:
  - usage is read only from exact fields: `usage`, `message.usage`, `result.usage`
  - usage schema: `input_tokens` + `output_tokens` (+ optional `total_tokens`) or `prompt_tokens` + `completion_tokens` (+ optional `total_tokens`)
  - model name is read only from run artifacts (`type == "system"`, `subtype == "init"`, field `model`)
  - no model-name backfill from `--model` or environment variables

`trajectory_path` points to a YAML-formatted, human-readable trace converted from run output.
