# KiloCode Agent (cakit)

This document explains how cakit installs and runs Kilo Code CLI (`kilocode`).

## Install

`cakit install kilocode` installs npm package `@kilocode/cli`.

- Default install (no `--version`) installs latest upstream release at install time.
- Versioned install is supported:

```bash
cakit install kilocode --version <npm_version_or_tag>
```

## Configure

`cakit configure kilocode` writes:

- `~/.kilocode/cli/config.json`

Environment variable mapping:

| Environment variable | Meaning | Requirement |
| --- | --- | --- |
| `KILO_OPENAI_API_KEY` | OpenAI-compatible API key (fallback: `OPENAI_API_KEY`) | required |
| `KILO_OPENAI_MODEL_ID` | Upstream model ID (fallback: `OPENAI_DEFAULT_MODEL`) | required |
| `KILO_OPENAI_BASE_URL` | OpenAI-compatible base URL (fallback: `OPENAI_BASE_URL`) | optional |

If required key/model values are missing, cakit does not write config and run returns non-zero.

## Run Behavior

cakit detects KiloCode major version at runtime and uses version-specific commands:

```bash
# 0.x
kilocode --auto --json --yolo --workspace <cwd> --nosplash [--attach <image>] [--model <name>] "<prompt>"

# 1.x
kilocode run --auto --format json [--file <image>] [--model openai/<name>] "<prompt>"
```

- cakit creates a run-local HOME under `/tmp` and writes run-local KiloCode config there.
- This avoids cross-run/session conflicts and keeps artifact matching exact per run.
- `cakit run kilocode --model <name>` takes priority for that run.
- If `--model` is omitted, cakit resolves model from `KILO_OPENAI_MODEL_ID`, then `OPENAI_DEFAULT_MODEL`.
- Video input is unsupported in cakit (`--video` returns unsupported).

## Image Input

`cakit run kilocode --image <path>` is supported.

- cakit passes each image via native `--attach <path>`.
- Image understanding still depends on the selected model capability.

## Stats Extraction

`cakit run kilocode` extracts stats with strict version-aware parsing:

### KiloCode 0.x

Artifacts:

1. `~/.kilocode/cli/global/global-state.json`
2. `~/.kilocode/cli/global/tasks/<task_id>/ui_messages.json`
3. `~/.kilocode/cli/global/tasks/<task_id>/api_conversation_history.json`

Strict extraction rules:
- `models_usage`:
  - from `ui_messages.json` entries with `type="say"` and `say="api_req_started"`
  - parse `text` JSON and sum `tokensIn` + `tokensOut`
  - model name from run artifacts only:
    - first: `taskHistory.apiConfigName` -> `listApiConfigMeta[].modelId`
    - fallback: `<model>...</model>` tag in `api_conversation_history.json`
- `llm_calls`: count of `api_req_started` entries in `ui_messages.json`
- `tool_calls`: count of assistant `tool_use` entries in `api_conversation_history.json`
- `response`:
  - first: `completion_result`/`text` in `ui_messages.json`
  - then assistant text in `api_conversation_history.json`
  - then stream JSON/stdout fallback
- `total_cost`: from `taskHistory.totalCost`

### KiloCode 1.x

Artifacts:

1. `kilocode run --format json` stdout events (contains exact `sessionID`)
2. `kilocode export <sessionID>` JSON payload (`info` + `messages`)

Strict extraction rules:
- `models_usage`:
  - sum assistant `info.tokens.input` + `info.tokens.output` from export payload
  - model name from assistant `providerID` + `modelID` in export payload
- `llm_calls`: count assistant messages (excluding `summary == true`) with token fields
- `tool_calls`: count assistant `parts` where `type == "tool"` and `state.status` is `completed` or `error`
- `response`:
  - first: last `text` event from run JSON stream
  - then: last assistant text part in exported messages
  - then: error message from stream `type == "error"`
- `total_cost`: sum assistant `info.cost` from export payload

If command succeeds but critical fields are missing/invalid (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`, non-empty `trajectory_path`), cakit returns non-zero `exit_code`.

`trajectory_path` points to a YAML-formatted, human-readable trace converted from run artifacts (no truncation).
