# AGENTS.md

## Project Positioning
- Coding Agent Kit is a lightweight CLI for academic experiments. It installs and runs mainstream coding agents with a unified interface and outputs structured stats.
- “coding agent” refers to the CLI itself (including `cursor-agent`), not IDEs or IDE plugins.
- This repository is a standalone project; the repository root is the project root.

## Environment and Dependencies
- Use `uv` to manage the Python environment and dependencies.
- Install dependencies: `uv sync`
- Before running any Python command, activate the environment: `source .venv/bin/activate`
- For API auth, generate `.env` from `.env.template` and run `set -a; source .env; set +a` in the current shell.

## Common Commands
- Generate `.env` template: `cakit env --output .env`
- Install and configure an agent: `cakit install <agent>` (default is unrestricted mode/Yolo)
- Run and output JSON stats: `cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video]`
- Generic availability test workflow: `python tests/availability_test.py <agent...>`
- Install fast shell power tools (recommended): `cakit tools`

## Agent Availability Test Workflow
- Prefer running the consolidated script first:
  - `source .venv/bin/activate`
  - `set -a; source .env; set +a`
  - `python tests/availability_test.py <agent...>`
- If manual validation is required, run tests in this order and in the same shell:
  1. `source .venv/bin/activate`
  2. `set -a; source .env; set +a`
  3. `cakit run <agent> "Reply with exactly this text and nothing else: CAKIT_HEALTHCHECK_OK" > /tmp/cakit-<agent>-basic.json` (basic reply check)
  4. `cakit run <agent> "What is in this image? What text is shown?" --image tests/image1.png > /tmp/cakit-<agent>-image.json` (image input check)
  5. `cakit run <agent> "What happens in this video? List any visible text." --video tests/video.mp4 > /tmp/cakit-<agent>-video.json` (video input check; use a small local mp4)
  6. `cakit run <agent> "Visit https://github.com/algorithmicsuperintelligence/openevolve and summarize what is on that page." > /tmp/cakit-<agent>-web.json` (web access check)
- Record whether each check passes based on the actual response content (not just process start).
- Verify stats field extraction from JSON outputs:
  1. `response`: key exists and value is non-empty text.
  2. `models_usage`: key exists and must be a non-empty object with integer token fields for successful runs.
  3. `llm_calls`: key exists and must be an integer (`>= 1`) for successful runs.
  4. `tool_calls`: key exists and must be an integer (`>= 0`).
- If `models_usage` is `{}` or `llm_calls`/`tool_calls` is missing/`null` on a successful run, treat it as extraction failure.
- Do not write guessed values for missing stats. If extraction is not possible, keep `None` (`null`) instead of writing placeholder values like `0`.
- For session/log fallback parsing, use exact session matching (for example by exact `session_id` path match). Do not use fuzzy matching by mtime or nearest file.
- Model name in `models_usage` must come from run artifacts (stdout payload/session logs). Do not fill it from config/env/`--model` input.
- Parsing must be strict and format-aware: read only exact, documented fields; if structure is unexpected, return `None` immediately instead of stacking fallback parsers.
- Field names must be exact and stable. Do not try multiple alternative field names or fallback chains for the same signal; if a required field is missing, return `None`.
- Usage extraction must be source-verified. When a coding agent CLI has an open-source repository, read the source to confirm how usage is produced before implementing or changing token accounting.
- Token usage is defined as the sum of prompt tokens and completion tokens across all LLM calls made during the agent run (including subagents when applicable).
- Code and documentation must stay consistent. When behavior changes, update docs in the same PR/patch and ensure they reflect the exact implementation (no mismatched fallbacks or fields).
- On extraction failure, inspect:
  1. `output_path` / `raw_output` from `cakit run`.
  2. Upstream coding agent logs/sessions (for example Kimi: `~/.kimi/logs`, `~/.kimi/sessions/*/*/wire.jsonl`, `~/.kimi/sessions/*/*/context.jsonl`).
  3. Agent extraction code in `src/agents/<agent>.py`, then fix parsing.
- Update `README.md` and `README.zh.md` Test Coverage Matrix after testing, and record `Test Version` from `agent_version` in `cakit run` output.

## Code Structure and Style
- `src/agents/`: one file per agent, one class per agent. All agent-specific logic (install, run, usage extraction, etc.) must live in the corresponding class.
- `src/utils.py`: only necessary shared utilities; do not wrap one-liners into functions.
- Use the standard library to parse JSON; if custom parsing is unavoidable, put it in `src/utils.py`.
- Use the term “coding agent” consistently.
- Use the name `trae-oss` to distinguish from other Trae products.

## Behavioral Constraints
- If an agent is not installed, `cakit run` must auto-run `cakit install <agent>` with a notice.
- Commands that are expected to succeed must return exit code 0; usage parsing failures or missing critical fields must return non-zero.
- `cakit install` must auto-install missing runtime dependencies (e.g., Node.js, uv) and work without `sudo` or in root environments.
- `cakit tools` is Linux-only; handle no-`sudo`/root cases; on non-`x86_64/amd64`, provide a clear message and skip.
- For debugging, store temporary files under `/tmp` instead of writing into the project directory.
- No output truncation (no `_preview`); output field is `raw_output`.
- `get_version` must not use fallbacks.
- Do not set hardcoded default values for environment variables in code (for example, avoid `os.environ.get("X") or "default"`). Read env vars as-is; if a required value is missing, fail clearly or skip writing config.
- Keep upstream coding agent environment variable names unchanged. If an upstream name is duplicated across different coding agents, add a coding-agent-specific prefix to disambiguate.
- Any environment variable defined only by cakit (not by upstream coding agents) must use the `CAKIT_` prefix.

## Auth and Stats Output Requirements
- Both OAuth and API auth must be supported, and each agent’s login method must be documented in README.
- Stats output must include:
  - `agent`, `agent_version`
  - `runtime_seconds`
  - `models_usage` (per-model breakdown with token usage)
  - `tool_calls`, `llm_calls`, `total_cost` (when available)
  - `telemetry_log` (when enabled, return log path or OTEL endpoint)
  - `response`, `exit_code`, `output_path`, `raw_output`
- Agents that can support image input must do so; Codex supports multiple images. If not supported, state clearly in README.

## Documentation and Config Sync
- When adding or changing an agent, update:
  - `README.md`, `README.zh.md`
  - `.env.template`
  - `docs/<agent>.md` (for example `docs/codex.md`)
  - `docs/<agent>.zh.md` (for example `docs/codex.zh.md`)
  - Supported agents list, login methods, test coverage matrix, and Todo
- When updating `AGENTS.md`, update `AGENTS.zh.md` as well.
