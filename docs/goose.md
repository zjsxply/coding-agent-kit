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
| `CAKIT_GOOSE_MODEL` | Model name used for Goose run (fallback: `OPENAI_DEFAULT_MODEL`) | required in cakit API mode (`--model` can override per run) |
| `CAKIT_GOOSE_OPENAI_API_KEY` | OpenAI-compatible API key (fallback: `OPENAI_API_KEY`) | required when provider is `openai` in cakit API mode |
| `CAKIT_GOOSE_OPENAI_BASE_URL` | OpenAI-compatible base URL (for example `https://host/v1`; fallback: `OPENAI_BASE_URL`) | optional |
| `CAKIT_GOOSE_OPENAI_BASE_PATH` | Optional API path override (for example `v1/chat/completions`) | optional |

When shared `OPENAI_*` vars are set and Goose provider is unset, cakit defaults provider to `openai`.

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
- Model priority is: `--model` > `CAKIT_GOOSE_MODEL`/`GOOSE_MODEL` > `OPENAI_DEFAULT_MODEL`.
- `cakit run goose --image/--video` is supported through natural-language local-path injection.

## Stats Extraction

`cakit run goose` uses the run-local temporary Goose home as the authoritative source of swarm/subagent stats:

1. cakit creates an isolated temporary `HOME`/`XDG_*` tree per run.
2. Stats are aggregated from that exact run-local state:
   - session database:
     - `<temp HOME>/data/goose/sessions/sessions.db`
   - request logs:
     - `<temp HOME>/state/goose/logs/llm_request.*.jsonl`
   - main-session export (response only):
     - `goose session export --session-id <id> --format json`
3. Read exact fields:
   - `models_usage`:
     - sum `accumulated_input_tokens` / `accumulated_output_tokens` / `accumulated_total_tokens`
       across every session row in the run-local SQLite database, including `sub_agent` sessions
   - model name:
     - per-session `model_config_json.model_name`
   - `tool_calls`:
     - count assistant message `content_json` blocks where `type` is `toolRequest` or `frontendToolRequest`
       across all run-local sessions
   - `llm_calls`:
     - count `llm_request.*.jsonl` files only when the summed request-log usage exactly matches the
       summed session usage; otherwise cakit returns `null` instead of guessing
   - `response`:
     - last assistant text block from the exported main session

If Goose command succeeds but strict fields are missing/invalid, cakit returns a non-zero `exit_code`.

`trajectory_path` points to a family-aware YAML trace containing CLI stdout, the exported main session,
a SQLite-derived snapshot of all run-local Goose sessions/messages, and any available
`llm_request.*.jsonl` logs from that same run-local home.
