# Qoder Agent (cakit)

This document explains how cakit runs Qoder CLI and extracts run metadata.

**Versioned Installation**
- `cakit install qoder --version <npm_version_or_tag>` installs `@qoder-ai/qodercli@<version>`.

**Auth**
- OAuth: run `qodercli /login`.
- Token auth in cakit:
  - `QODER_PERSONAL_ACCESS_TOKEN` (recommended)
- Custom OpenAI-compatible API auth (`api_key`/`base_url`) is not supported by upstream `qodercli`.

**Run Behavior**
- cakit runs:
  - `qodercli -q -p "<prompt>" --output-format stream-json --dangerously-skip-permissions`
- cakit passes `--model <name>` when `cakit run qoder --model <name>` is provided, or when `CAKIT_QODER_MODEL` is set.
- cakit forwards image attachments with native flags:
  - `--attachment <image_path>` for each `--image`.

**Image and Video Input**
- `cakit run qoder --image <path>` is supported via Qoder native `--attachment`.
- `cakit run qoder --video <path>` is unsupported in cakit.

**Field Mapping**
- `agent_version`: from `qodercli --version`.
- `runtime_seconds`: wall time of the `qodercli` process.
- `telemetry_log`: `~/.qoder/logs/qodercli.log` when present.
- `output_path`/`raw_output`: captured Qoder CLI stdout/stderr.
- `trajectory_path`: YAML-formatted, human-readable trace generated from raw output.

**Stats Extraction**
- cakit reads stream JSON from stdout and uses strict, format-aware parsing:
  - `qoder_message` schema:
    - `models_usage` from `message.usage.total_prompt_tokens` / `total_completed_tokens` / `total_tokens`.
    - model key from `message.response_meta.model_name`.
    - `llm_calls` from unique `message.response_meta.request_id`.
    - `tool_calls` from `message.tool_calls` length.
    - `response` from the last non-empty assistant `message.content`.
  - message-stream schema (`message_start`/`message_stop`):
    - `models_usage` from `message_start.message.usage.input_tokens` + `cache_read_tokens`, and output tokens from `message_delta.usage.output_tokens`.
    - model key from `message_start.message.model`.
    - `llm_calls` from unique `message_start.message.id`.
    - `tool_calls` by counting `content_block_start` where `content_block.type == "tool_use"`.
    - `response` from accumulated text deltas (`content_block_start` + `content_block_delta`).

If payload structure does not match the expected schema exactly, cakit returns empty stats (`None`/`{}`), and strict run validation makes `cakit run` exit non-zero.
