# OpenHands Agent (cakit)

This document explains how cakit runs OpenHands CLI and extracts run metadata.

**Versioned Installation**
- `cakit install openhands --version <pip_version>` installs `openhands==<version>` (`uv tool install` when available, otherwise `pip install`).

**Sources**
- CLI stdout/stderr from `openhands --headless --json --override-with-envs -t ...`.
- Conversation artifacts under `~/.openhands/conversations/<conversation_id>/` (or `OPENHANDS_CONVERSATIONS_DIR`):
  - `base_state.json`
  - `events/event-*.json`

**Auth**
- cakit currently supports API mode for OpenHands.
- Required environment variables:
  - `LLM_API_KEY` (fallback: `OPENAI_API_KEY`)
  - `LLM_MODEL` (fallback: `OPENAI_DEFAULT_MODEL`)
- Optional environment variable:
  - `LLM_BASE_URL` (fallback: `OPENAI_BASE_URL`)
- cakit normalizes OpenHands model format for LiteLLM routing:
  - `provider:model` is rewritten to `provider/model`.
  - bare model name (for example `kimi-k2.5`) is rewritten to `openai/<model>`.
- Model priority is: `--model` > `LLM_MODEL` > `OPENAI_DEFAULT_MODEL`.

**Image and Video Input**
- OpenHands headless CLI does not provide documented `--image` / `--video` run flags.
- `cakit run openhands --image/--video` is treated as unsupported.

**Field Mapping**
- `agent_version`: from `openhands --version`.
- `runtime_seconds`: wall time of the `openhands` process.
- `models_usage`:
  - Model name: `base_state.stats.usage_to_metrics.agent.model_name`.
  - Tokens: `base_state.stats.usage_to_metrics.agent.accumulated_token_usage.prompt_tokens` and `completion_tokens`.
  - `total_tokens = prompt_tokens + completion_tokens`.
- `llm_calls`: `len(base_state.stats.usage_to_metrics.agent.token_usages)`.
- `tool_calls`: count of `ActionEvent` items in `events/event-*.json` with non-empty `tool_name`.
- `total_cost`: `base_state.stats.usage_to_metrics.agent.accumulated_cost`.
- `response`:
  - Preferred: latest `FinishObservation` text in `ObservationEvent`.
  - Fallback: latest assistant `MessageEvent` text (`llm_message.role == "assistant"`).
  - If both are unavailable, return `None`.
  - Reason: OpenHands can end successfully in two valid event shapes. Tool-based completion uses `FinishObservation`, while direct completion may only emit assistant `MessageEvent`.
  - The order is fixed and format-aware to cover both official shapes without introducing alias field parsing.
- `output_path`/`raw_output`: captured OpenHands stdout/stderr stream.
- `trajectory_path`: formatted YAML trace converted from conversation artifacts; if artifacts are unavailable, it falls back to formatted raw output.

**Exit Code Rules**
- cakit marks OpenHands runs as failed (non-zero `exit_code`) when any of the following is true:
  - OpenHands process exits non-zero.
  - `ConversationErrorEvent`/`AgentErrorEvent` appears.
  - Successful-looking run is missing critical fields (`models_usage`, `llm_calls`, `tool_calls`, `response`, `trajectory_path`).
