# Aider Agent (cakit)

This document explains how cakit installs and runs Aider CLI.

## Install

Install latest:

```bash
cakit install aider
```

Install a specific version:

```bash
cakit install aider --version <aider_chat_version>
```

cakit installs `aider-chat` via `uv tool install` (with `--force`) and falls back to `pip install` when `uv` is unavailable.

## Configuration

`cakit configure aider` is a no-op (`config_path: null`).

Environment variable mapping for `cakit run aider`:

| Environment variable | Meaning | Requirement |
| --- | --- | --- |
| `AIDER_OPENAI_API_KEY` | API key for OpenAI-compatible endpoint (fallback: `OPENAI_API_KEY`) | required |
| `AIDER_OPENAI_API_BASE` | OpenAI-compatible base URL (fallback: `OPENAI_BASE_URL`) | optional |
| `AIDER_MODEL` | Base model (fallback: `OPENAI_DEFAULT_MODEL`) | required |

Model resolution:
- `--model` has highest priority for the run.
- If `--model` is omitted, cakit uses `AIDER_MODEL`, then `OPENAI_DEFAULT_MODEL`.
- If the model has no provider prefix, cakit normalizes it to `openai/<model>`.
- If the model is `provider:model`, cakit normalizes it to `provider/model`.

## Image and Video Input

- `cakit run aider --image ...` is supported.
- `cakit run aider --video ...` is unsupported.

Implementation detail:
- cakit maps each `--image` file to Aider positional file args (same behavior as launching `aider <image-file> ...`), which adds image files into chat context.
- Image support is model-dependent. The selected model must support vision.

## Web Access

- URL detection is enabled (Aider default behavior) for `cakit run aider`.
- When the prompt includes URLs, Aider may fetch and add page content into chat context, depending on upstream behavior and runtime/network policy.

## Stats Extraction

`cakit run aider` runs Aider in single-message mode and writes run-local artifacts under `/tmp/cakit-aider-*`, including:
- `analytics.jsonl`
- `chat.history.md`
- `llm.history.log`

Strict parsing:
1. Parse `analytics.jsonl` JSONL events exactly.
2. `models_usage`: aggregate `message_send` events by `properties.main_model`, summing `prompt_tokens`, `completion_tokens`, `total_tokens`.
3. `llm_calls`: number of `message_send` events.
4. `tool_calls`: number of analytics events whose `event` starts with `command_` (for `--message` runs this is typically `0`).
5. `total_cost`: latest `properties.total_cost` from `message_send`.
6. `response`: last assistant response from `llm.history.log` (`LLM RESPONSE` block), then fallback to chat history/output parsing.

If required stats cannot be parsed, cakit returns non-zero.
