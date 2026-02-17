# Gemini Agent (cakit)

This document explains how cakit runs Gemini CLI and extracts run metadata.

**Versioned Installation**
- `cakit install gemini --version <npm_version_or_tag>` installs `@google/gemini-cli@<version>`.

**Sources**
- CLI stdout/stderr from `gemini -p ... --output-format json --approval-mode yolo`.
- Local telemetry log: `~/.gemini/telemetry.log`.
- Runtime environment variables: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GEMINI_BASE_URL`, `GOOGLE_CLOUD_PROJECT`, `GEMINI_MODEL`.

**Image and Video Input**
- `cakit run gemini --image/--video` is supported.
- cakit copies each media file into `<run_cwd>/.cakit-media/`.
- cakit prepends staged `@<path>` references so Gemini CLI resolves them through its built-in `@` file injection flow (`read_many_files`).
- The copy mechanism applies only to `--image`/`--video`.
- If you only put local file paths in prompt text (without `--image`/`--video`), cakit does not copy files.
- If prompt-only local paths point outside the current run workspace, Gemini may reject them due to workspace restrictions.

**Field Mapping**
- `agent_version`: from `gemini --version`.
- `runtime_seconds`: wall time of the `gemini` process.
- `response`: top-level JSON field `response`.
- `models_usage`: `stats.models[model].tokens.prompt` / `candidates` / `total`.
- `llm_calls`: sum of `stats.models[model].api.totalRequests`.
- `tool_calls`: `stats.tools.totalCalls`.
- `output_path`/`raw_output`: captured Gemini CLI stdout/stderr.
- `trajectory_path`: formatted human-readable trajectory generated from raw output.

**Parsing and Validation Rules**
- cakit parses only the last JSON value found in stdout and uses exact field names above.
- No model name fallback from config or environment variables is used for `models_usage`.
- If the Gemini command exits `0` but critical fields are missing/invalid (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`, non-empty `trajectory_path`), cakit returns non-zero `exit_code`.
