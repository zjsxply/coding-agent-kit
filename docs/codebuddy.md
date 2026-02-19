# CodeBuddy Agent (cakit)

This document describes how cakit runs CodeBuddy Code and extracts run metadata.

**Versioned Installation**
- `cakit install codebuddy --version <npm_version_or_tag>` installs `@tencent-ai/codebuddy-code@<version>`.

**Auth**
- OAuth: run `codebuddy login`.
- API mode in cakit:
  - `CODEBUDDY_API_KEY`
  - optional: `CODEBUDDY_AUTH_TOKEN`
  - optional: `CODEBUDDY_BASE_URL`
  - optional: `CODEBUDDY_INTERNET_ENVIRONMENT` (`internal` for CN, `ioa` for iOA)
- OpenAI-compatible shared fallback is supported when agent-specific vars are unset:
  - `OPENAI_API_KEY` -> `CODEBUDDY_API_KEY`
  - `OPENAI_BASE_URL` -> `CODEBUDDY_BASE_URL`
  - `OPENAI_DEFAULT_MODEL` -> `CODEBUDDY_MODEL`

**Run Behavior**
- cakit runs:
  - `codebuddy -p --output-format stream-json -y "<prompt>"`
- with `--image`, cakit runs:
  - `codebuddy -p --input-format stream-json --output-format stream-json -y`
  - stdin payload embeds image blocks as `{"type":"image","source":{"type":"base64","media_type":"...","data":"..."}}`
- If `--model` is provided, cakit appends:
  - `--model <name>`

**Model Selection**
- `cakit run codebuddy --model <name>` takes precedence.
- If `--model` is not set, cakit reads `CODEBUDDY_MODEL`, then `OPENAI_DEFAULT_MODEL`.

**Image and Video Input**
- `cakit run codebuddy --image <path>` is supported via headless stream-json image blocks (model-dependent).
- `cakit run codebuddy --video <path>` is unsupported.
- Prompt-path check (without `--image`): observed working with local image path text in prompt; CodeBuddy can read and describe the image content.

**Field Mapping**
- `agent_version`: from `codebuddy --version`.
- `response`: from `result.result` when `result.subtype == "success"`; otherwise fallback to assistant/error text.
- `models_usage`: aggregated by `assistant.message.model` from `assistant.message.usage`:
  - `prompt_tokens` <- `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
  - `completion_tokens` <- `output_tokens`
  - `total_tokens` <- `prompt_tokens + completion_tokens`
- `llm_calls`: number of parsed `assistant` messages.
- `tool_calls`: count of `tool_use` blocks in `assistant.message.content`.
- `total_cost`: `result.total_cost_usd`.
- `trajectory_path`: YAML-formatted trace generated from raw CLI output.

**Parsing and Validation Rules**
- cakit parses only `stream-json` payloads using documented message types: `system/init`, `assistant`, `result`.
- Result subtype is strict: if `result.is_error == true` (for example `error_during_execution`) while command exit code is `0`, cakit marks run as failed.
- If a successful command does not produce required stats (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`), cakit returns non-zero `exit_code`.
