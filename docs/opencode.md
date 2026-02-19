# OpenCode Agent (cakit)

This document explains how cakit runs OpenCode and extracts run metadata.

**Versioned Installation**
- `cakit install opencode --version <npm_version_or_tag>` installs `opencode-ai@<version>`.

**Auth and Configuration**
- OAuth: run `opencode auth login` with the upstream CLI flow.
- API mode (OpenAI-compatible): set:
  - `CAKIT_OPENCODE_OPENAI_API_KEY`
  - `CAKIT_OPENCODE_MODEL` (use `provider/model` or `provider:model`; if using a bare model id, set `CAKIT_OPENCODE_PROVIDER`)
  - `CAKIT_OPENCODE_PROVIDER` (optional unless `CAKIT_OPENCODE_MODEL` is bare; list providers via `opencode models`)
  - `CAKIT_OPENCODE_OPENAI_BASE_URL` (optional)
  - `CAKIT_OPENCODE_MODEL_CAPABILITIES` (optional for custom API models; comma-separated input modalities from `text,audio,image,video,pdf`, for example `image,video`)
- Shared fallback is supported when agent-specific vars are unset:
  - `OPENAI_API_KEY` -> `CAKIT_OPENCODE_OPENAI_API_KEY`
  - `OPENAI_BASE_URL` -> `CAKIT_OPENCODE_OPENAI_BASE_URL`
  - `OPENAI_DEFAULT_MODEL` -> `CAKIT_OPENCODE_MODEL` (defaults provider to `openai` when needed)
- `cakit configure opencode` is a no-op because cakit uses per-run environment injection.

**Run Command**
- cakit runs:
  - `opencode run --format json [--model <provider/model>] [--file <path> ...] -- <prompt>`
- In API mode, cakit sets run-local XDG paths under `/tmp/cakit-opencode-*` for isolation.
- If `CAKIT_OPENCODE_OPENAI_BASE_URL` is set, cakit injects provider `baseURL` via `OPENCODE_CONFIG_CONTENT`.
- If `CAKIT_OPENCODE_MODEL_CAPABILITIES` is set, cakit injects `modalities.input`/`modalities.output` into `OPENCODE_CONFIG_CONTENT` so custom API models expose declared multimodal capabilities to OpenCode.
- Model priority is: `--model` > `CAKIT_OPENCODE_MODEL` > `OPENAI_DEFAULT_MODEL`.

**Image and Video Input**
- cakit maps local media files to repeated `opencode run --file <path>` arguments.
- Image input works when the selected model/provider supports image attachments.
- Local video files are currently not passed through as multimodal attachments in OpenCode `1.2.6` (upstream Read handling rejects binary video files).

**Stats Extraction (strict)**
- cakit first reads `sessionID` from OpenCode JSON events (`--format json`) for the current run.
- cakit then runs `opencode export <sessionID>` and parses only that exact session.
- `agent_version`: from `opencode --version`.
- `response`: from the last `text` event in run JSON output (`type == "text"` and `part.type == "text"`).
- `models_usage`:
  - From exported assistant messages (`messages[].info.role == "assistant"`).
  - Model key: `providerID/modelID`.
  - Token fields from `info.tokens`:
    - prompt tokens: `input + cache.read + cache.write`
    - completion tokens: `output + reasoning`
    - total tokens: `total` when present, otherwise prompt + completion
- `llm_calls`: number of exported assistant messages.
- `tool_calls`: number of exported assistant parts with `type == "tool"`.
- `total_cost`: sum of exported assistant `info.cost`.
- `output_path` / `raw_output`: captured OpenCode stdout/stderr.
- `trajectory_path`: formatted YAML trace generated from raw run output (no truncation).
