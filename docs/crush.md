# Crush Agent (cakit)

This document explains how cakit runs Crush and extracts run metadata.

**Versioned Installation**
- `cakit install crush --version <npm_version_or_tag>` installs `@charmland/crush@<version>`.

**Auth**
- OAuth: use `crush login` (for example `crush login hyper` or `crush login copilot`).
- API mode in cakit:
  - `CRUSH_OPENAI_API_KEY` (fallback: `OPENAI_API_KEY`)
  - `CRUSH_OPENAI_BASE_URL` (fallback: `OPENAI_BASE_URL`)
  - `CAKIT_CRUSH_MODEL` (fallback: `OPENAI_DEFAULT_MODEL`)
- `cakit configure crush` writes `~/.config/crush/crush.json` when all API mode variables above are present.
- `cakit run crush` uses a run-local runtime config in API mode; OAuth mode uses your existing Crush config/auth.

**Run Behavior**
- cakit runs:
  - `crush --cwd <run_cwd> --data-dir <tmp_dir> run --quiet <prompt>`
- cakit always sets `CRUSH_DISABLE_PROVIDER_AUTO_UPDATE=1` for stable runs.
- `--data-dir` is isolated per run (under `/tmp`) so session/stat parsing is exact for that run.

**Model Selection**
- `cakit run crush --model <name>` takes precedence over `CAKIT_CRUSH_MODEL`.
- If `--model` is omitted, cakit resolves model from `CAKIT_CRUSH_MODEL`, then `OPENAI_DEFAULT_MODEL`.
- API mode: cakit generates run-local Crush config with that model.
- OAuth mode: when a model is selected, cakit passes both `--model <name>` and `--small-model <name>` to `crush run`.

**Image and Video Input**
- `cakit run crush --image/--video` is treated as unsupported.

**Field Mapping**
- `agent_version`: from `crush --version`.
- `response`: from Crush stdout.
- `models_usage`: from `<data-dir>/crush.db`:
  - token totals: `sessions.prompt_tokens`, `sessions.completion_tokens`
  - model name: single distinct non-summary assistant model from `messages.model`
- `llm_calls`: count of non-summary assistant messages in `messages`.
- `tool_calls`: count of `tool_call` parts from `messages.parts` JSON (`json_each` + `$.type == "tool_call"`).
- `telemetry_log`: `<data-dir>/logs/crush.log`.
- `trajectory_path`: YAML-formatted trace generated from run DB artifacts (`session` + `messages`).

**Parsing and Validation Rules**
- cakit reads only exact fields from Crush run artifacts (`crush.db` tables/columns).
- Session matching is exact: one isolated root session in the run-local `--data-dir`.
- If critical stats are missing/invalid on an otherwise successful command (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`), cakit returns non-zero `exit_code`.
