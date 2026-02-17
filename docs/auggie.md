# Auggie Agent (cakit)

This document describes how cakit runs Auggie and extracts run metadata.

**Versioned Installation**
- `cakit install auggie --version <npm_version_or_tag>` installs `@augmentcode/auggie@<version>`.

**Auth**
- OAuth: run `auggie login`.
- API mode in cakit:
  - `AUGMENT_API_TOKEN`
  - `AUGMENT_API_URL`
  - optional: `AUGMENT_SESSION_AUTH`
- If you keep provider credentials in `LLM_API_KEY` / `LLM_BASE_URL`, map them in the current shell before running:
  - `export AUGMENT_API_TOKEN="$LLM_API_KEY"`
  - `export AUGMENT_API_URL="$LLM_BASE_URL"`

**Run Behavior**
- cakit runs:
  - `auggie --print --quiet --output-format json --workspace-root <run_cwd> --instruction <prompt> --log-file <tmp_log> --log-level debug`
- cakit sets `AUGMENT_DISABLE_AUTO_UPDATE=1` for stable runs.
- cakit passes native image arguments (`--image <path>`) directly to Auggie.

**Model Selection**
- `cakit run auggie --model <name>` takes precedence.
- If `--model` is not set, cakit reads `CAKIT_AUGGIE_MODEL` and passes `--model <name>` when present.

**Image and Video Input**
- `cakit run auggie --image <path>` is supported (native Auggie flag).
- `cakit run auggie --video <path>` is unsupported.

**Field Mapping**
- `agent_version`: from `auggie --version`.
- `response`: from result payload field `result`.
- `models_usage`: from `result.stats.models[<model>].tokens`:
  - `prompt_tokens` <- `prompt`
  - `completion_tokens` <- `candidates`
  - `total_tokens` <- `total`
- `llm_calls`: sum of `result.stats.models[<model>].api.totalRequests`.
- `tool_calls`: `result.stats.tools.totalCalls`.
- `telemetry_log`: run-local log path passed via `--log-file`.
- `trajectory_path`: YAML-formatted trace generated from raw CLI output.

**Parsing and Validation Rules**
- cakit parses only the JSON result payload with `type == "result"` and exact field names above.
- If a successful command does not produce required stats (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`), cakit returns non-zero `exit_code`.
