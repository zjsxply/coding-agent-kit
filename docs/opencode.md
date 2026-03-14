# OpenCode Agent (cakit)

This document explains how cakit runs OpenCode and extracts run metadata.

**Installation**
- `cakit install opencode` runs OpenCode's official install script with cakit's install-time adjustments: cakit auto-installs the missing `which` runtime dependency via the host package manager when needed, and upstream PATH mutation is disabled with `--no-modify-path`.
- `cakit install opencode --version <version>` runs the same script with `--no-modify-path --version <value>`.
- Effective upstream invocation:
  - `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path`
  - `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path --version <version>`
- cakit tries that script path first and falls back to `npm install -g opencode-ai` if the script path fails.
- `--scope user|global` does not affect the primary script path. It only affects the npm fallback path if cakit has to use it.
- cakit intentionally does not let the upstream installer edit shell rc/profile files; expose `~/.opencode/bin` yourself if you need it in login shells outside the current cakit-managed flow.

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
- Local video files are currently not passed through as multimodal attachments in OpenCode `1.2.24` (upstream Read handling rejects binary video files).

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
    - completion tokens: `output + reasoning` when `reasoning` is present
    - total tokens: `info.tokens.total` when present; otherwise cakit falls back to `prompt_tokens + completion_tokens`
    - historical OpenCode step logs include upstream `info.tokens.total` values that do not equal the cakit prompt/completion split; cakit preserves the upstream total in the result
- `llm_calls`: number of exported assistant messages.
- `tool_calls`: number of exported assistant parts with `type == "tool"`.
- `total_cost`: sum of exported assistant `info.cost`.
- `output_path` / `raw_output`: captured OpenCode stdout/stderr.
- `trajectory_path`: formatted YAML trace generated from raw run output (no truncation).
