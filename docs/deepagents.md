# Deep Agents Agent (cakit)

This document explains how cakit installs and runs Deep Agents CLI.

## Install

Install latest:

```bash
cakit install deepagents
```

Install a specific version:

```bash
cakit install deepagents --version <deepagents_cli_version>
```

cakit installs `deepagents-cli` via `uv tool install` (with `--force`), and falls back to `pip install` when `uv` is unavailable.

## Configuration

`cakit configure deepagents` is a no-op (`config_path: null`).

Environment variable mapping for `cakit run deepagents`:

| Environment variable | Meaning | Requirement |
| --- | --- | --- |
| `DEEPAGENTS_OPENAI_API_KEY` | API key for OpenAI-compatible endpoint (fallback: `OPENAI_API_KEY`) | required |
| `DEEPAGENTS_OPENAI_BASE_URL` | OpenAI-compatible base URL (fallback: `OPENAI_BASE_URL`) | optional |
| `DEEPAGENTS_OPENAI_MODEL` | Base model (fallback: `OPENAI_DEFAULT_MODEL`) | required |

Model resolution:
- `--model` has highest priority for the run.
- If `--model` is omitted, cakit uses `DEEPAGENTS_OPENAI_MODEL`, then `OPENAI_DEFAULT_MODEL`.
- If model is `provider/model`, cakit rewrites it to `provider:model` for Deep Agents CLI.
- If model has no provider prefix, cakit normalizes it to `openai:<model>`.

## Image and Video Input

- `cakit run deepagents --image ...` is unsupported.
- `cakit run deepagents --video ...` is unsupported.

Deep Agents non-interactive CLI has no documented generic `--image` / `--video` flags.

## Stats Extraction

`cakit run deepagents` uses strict parsing:

1. Run `deepagents -n ... --no-stream` and parse exact `Thread: <id>` from run output.
2. Read `~/.deepagents/sessions.db`, select the latest `checkpoints` row for that exact `thread_id`.
3. Decode checkpoint payload with LangGraph `JsonPlusSerializer` from Deep Agents tool runtime.
4. Aggregate stats from `channel_values.messages`:
   - `llm_calls`: count of `AIMessage` entries.
   - `models_usage`: aggregate `usage_metadata.input_tokens` + `usage_metadata.output_tokens` by exact `response_metadata.model_name`.
   - `tool_calls`: sum of `AIMessage.tool_calls` lengths.
   - `response`: last non-empty assistant text from AI messages.
5. If required stats fields cannot be extracted exactly, cakit returns non-zero for the run.

`trajectory_path` points to a formatted, human-readable trace generated from raw CLI output.
