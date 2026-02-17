# Goose Agent (cakit)

This document explains how cakit installs and runs Goose CLI.

## Install

`cakit install goose` uses Goose's official install script in non-interactive mode:

```bash
curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash
```

To install a specific Goose version:

```bash
cakit install goose --version <goose_version>
```

cakit maps `--version` to `GOOSE_VERSION=<value>` for the same official installer script.

## Configure

`cakit configure goose` is currently a no-op (`config_path: null`).

Goose can be configured via environment variables for `cakit run`, or via Goose's own interactive setup (`goose configure`) outside cakit.

## API Environment Variables

cakit-managed Goose API variables:

| Environment variable | Meaning | Requirement |
| --- | --- | --- |
| `CAKIT_GOOSE_PROVIDER` | Provider name (for example `openai`) | required in cakit API mode |
| `CAKIT_GOOSE_MODEL` | Model name used for Goose run | required in cakit API mode (`--model` can override per run) |
| `CAKIT_GOOSE_OPENAI_API_KEY` | OpenAI-compatible API key | required when provider is `openai` in cakit API mode |
| `CAKIT_GOOSE_OPENAI_BASE_URL` | OpenAI-compatible base URL (for example `https://host/v1`) | optional |
| `CAKIT_GOOSE_OPENAI_BASE_PATH` | Optional API path override (for example `v1/chat/completions`) | optional |

When `CAKIT_GOOSE_OPENAI_BASE_URL` is set, cakit derives Goose upstream OpenAI settings:
- `OPENAI_HOST`
- `OPENAI_BASE_PATH`

## Run Behavior

cakit runs Goose in headless mode with stream JSON output:

```bash
goose run -t "<prompt>" --name <unique_name> --output-format stream-json
```

- cakit always sets `GOOSE_MODE=auto` for non-interactive runs.
- `cakit run goose --model <name>` passes `--model <name>` and sets run-local `GOOSE_MODEL`.
- media flags are unsupported (`--image` / `--video`).

## Stats Extraction

`cakit run goose` extracts stats with strict parsing from run artifacts:

1. Run Goose with a unique session name (`--name`).
2. Export that exact session:
   - `goose session export --name <unique_name> --format json`
3. Read exact fields:
   - `models_usage`:
     - `accumulated_input_tokens` / `accumulated_output_tokens` / `accumulated_total_tokens`
     - falls back to non-accumulated `input_tokens` / `output_tokens` / `total_tokens` only when needed
   - model name:
     - `stream-json` `model_change.model`, then session `model_config.model_name`
   - `llm_calls`: count of `conversation.messages` with `role == "assistant"`
   - `tool_calls`: count of assistant message `content` items where `type` is `toolRequest` or `frontendToolRequest`
   - `response`: last assistant text block in session conversation

If Goose command succeeds but strict fields are missing/invalid, cakit returns a non-zero `exit_code`.

`trajectory_path` points to a YAML-formatted, human-readable trace converted from raw Goose output.
