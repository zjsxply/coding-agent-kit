# Qwen Agent (cakit)

This document explains how cakit runs Qwen Code and extracts run metadata.

**Sources**
- CLI stdout/stderr from `qwen -p ... --output-format json --approval-mode yolo`.
- Local telemetry log: `~/.qwen/telemetry.log`.
- Runtime environment variables:
  - `QWEN_OPENAI_API_KEY` -> `OPENAI_API_KEY`
  - `QWEN_OPENAI_BASE_URL` -> `OPENAI_BASE_URL`
  - `QWEN_OPENAI_MODEL` -> `OPENAI_MODEL` and `--model`
  - `CAKIT_QWEN_GOOGLE_API_KEY` -> `GOOGLE_API_KEY`
  - `GOOGLE_SEARCH_ENGINE_ID`, `TAVILY_API_KEY`

**Run Behavior**
- When `QWEN_OPENAI_API_KEY` exists, cakit passes `--auth-type openai`.

**Image and Video Input**
- `cakit run qwen --image/--video` is supported through prompt injection.
- cakit copies each media file into `<run_cwd>/.cakit-media/` and prepends `@{.cakit-media/<file>}`.
- Actual media understanding depends on the selected base model capability; text-only models may not produce correct image/video descriptions.
- The copy mechanism applies only to `--image`/`--video`.
- If you only put local file paths in prompt text (without `--image`/`--video`), cakit does not copy files, and Qwen may reject paths outside the current run workspace.

**Field Mapping**
- `agent_version`: from `qwen --version`.
- `runtime_seconds`: wall time of the `qwen` process.
- `response`: from `result.result`; fallback to last assistant text block in JSON output.
- `models_usage`: `result.stats.models[model].tokens.prompt` / `candidates` / `total`.
- `llm_calls`: sum of `result.stats.models[model].api.totalRequests`.
- `tool_calls`: `result.stats.tools.totalCalls`.
- `output_path`/`raw_output`: captured Qwen CLI stdout/stderr.
- `trajectory_path`: formatted human-readable trajectory generated from raw output.

**Parsing and Validation Rules**
- cakit parses only the last JSON value from stdout, then selects the last `type == "result"` payload.
- No model name fallback from config/env is used for `models_usage`.
- If the Qwen command exits `0` but critical fields are missing/invalid (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`), cakit returns non-zero `exit_code`.
