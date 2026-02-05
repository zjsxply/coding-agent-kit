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
- Run and output JSON stats: `cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image]`
- Install fast shell power tools (recommended): `cakit tools`
- Smoke test: `scripts/test_agents.sh [agent ...]`

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
- No output truncation (no `_preview`); output field is `raw_output`.
- `get_version` must not use fallbacks.

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
