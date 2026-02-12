# Kimi Agent (cakit)

This document explains how cakit installs and configures Kimi Code CLI.

## Install

`cakit install kimi` uses the official installer script:

```bash
curl -LsSf https://code.kimi.com/install.sh | bash
```

The upstream installer handles runtime bootstrap (including `uv` when needed).

## API Configure (`cakit configure kimi`)

When `KIMI_API_KEY` is set, cakit writes `~/.kimi/config.toml` using Kimi CLI provider/model format.

Environment variable mapping:

| Environment variable | Meaning | Requirement |
| --- | --- | --- |
| `KIMI_API_KEY` | Provider API key | required |
| `KIMI_BASE_URL` | Provider base URL | required |
| `KIMI_MODEL_NAME` | Upstream model id (`model`), used at run-time `--model` | optional |
| `CAKIT_KIMI_PROVIDER_TYPE` | Provider `type` in Kimi config | required (`kimi`, `openai_legacy`, `openai_responses`) |

If any required variable above is missing, or `CAKIT_KIMI_PROVIDER_TYPE` is outside the allowed set, `cakit configure kimi` returns `config_path: null` and does not write a config file.

cakit writes provider config only:
- provider key: `kimi`
- no `default_model` and no `[models.*]` block are written by `cakit configure kimi`

Reference:
- Environment variable overrides: https://moonshotai.github.io/kimi-cli/zh/configuration/overrides.html#%E7%8E%AF%E5%A2%83%E5%8F%98%E9%87%8F%E8%A6%86%E7%9B%96

## Image Input

`cakit run kimi --image <path>` is supported.

- cakit uses print mode `--prompt` input and injects absolute image paths into the prompt so Kimi can read the files.
- For image runs, if `KIMI_MODEL_CAPABILITIES` is not set in the shell, cakit sets it to `image_in` for that run process so `ReadMediaFile` can be available.
- Image understanding still depends on the selected model capability (`image_in`). If the model does not support image input, Kimi may fail or return that image reading is unsupported.

## Video Input

`cakit run kimi --video <path>` is supported.

- With videos: cakit uses print mode `--prompt` input and injects absolute video paths into the prompt.
- For video runs, if `KIMI_MODEL_CAPABILITIES` is not set in the shell, cakit sets it to `video_in` (or `image_in,video_in` when both image and video inputs are provided) so `ReadMediaFile` can be available.
- Video understanding depends on the selected model capability (`video_in`). If the model does not support video input, Kimi may fail or return that video reading is unsupported.

## Agent Swarm

Kimi supports Agent Swarm style workflows. You can trigger it directly in prompt text, for example:

- `Can you launch multiple subagents to solve this and summarize the results?`

## Run-time Model and Update Behavior

- cakit always passes model via CLI flag: `kimi ... --model <KIMI_MODEL_NAME>`.
- `cakit run kimi --model <name>` takes priority for that run (it overrides `KIMI_MODEL_NAME` in the run process, then restores it).
- cakit always sets `KIMI_CLI_NO_AUTO_UPDATE=1` when running Kimi.

## SearchWeb and FetchURL Behavior

According to Kimi CLI provider behavior:

- Native Kimi provider mode (`type = "kimi"`): both `SearchWeb` and `FetchURL` are supported by Kimi services.
- Third-party OpenAI-compatible mode (`type = "openai_legacy"` or `type = "openai_responses"`): `SearchWeb` is not supported; `FetchURL` still works via local URL fetching.

Reference:
- Provider search/fetch behavior: https://moonshotai.github.io/kimi-cli/zh/configuration/providers.html#%E6%90%9C%E7%B4%A2%E5%92%8C%E6%8A%93%E5%8F%96%E6%9C%8D%E5%8A%A1

## Stats Extraction

`cakit run kimi` extracts `response`, `models_usage`, `llm_calls`, and `tool_calls` with strict parsing in this order:

1. cakit generates a UUID per run and passes it via `--session`, then reads `wire.jsonl` using the exact session path derived from `work_dir` + Kimi metadata:
   - `~/.kimi/sessions/<kaos_or_md5>/<session_id>/wire.jsonl`
2. From the session `wire.jsonl`:
   - `StatusUpdate.payload.token_usage` -> token usage (`models_usage`)
   - `SubagentEvent.event.type == "StatusUpdate"` token usage is aggregated into the same total
   - `StatusUpdate` + subagent `StatusUpdate` count -> `llm_calls`
   - `ToolCall` + subagent `ToolCall` count -> `tool_calls`
   - model name from `payload.model` when present
3. If session data is still incomplete, parse stdout `stream-json` payloads with exact fields only (usage/response only).
4. If session wire has usage but no model field, parse `~/.kimi/logs/kimi.log` by exact `session_id` markers (`Created new session:` / `Switching to session:` / `Session ... not found`) and read `Using LLM model: ... model='...'` in that same block.
5. No guessed placeholders are written for model name. If model cannot be extracted from run artifacts, `models_usage` remains empty.

Model name is extracted from run artifacts only (session wire / session logs). It is not backfilled from config/env input.
`prompt_tokens` is computed from Kimi input usage fields (`input_other`, `input_cache_read`, `input_cache_creation`), clamped per-field to avoid negative deltas.
If upstream emits these values as `0`, `prompt_tokens` can be `0`.

When extraction fails unexpectedly, inspect `output_path` / `raw_output` plus Kimi session/log files.

## Reasoning Effort Mapping

In `cakit run kimi ... --reasoning-effort <value>`:

- `thinking` -> adds `--thinking`
- `none` -> adds `--no-thinking`
