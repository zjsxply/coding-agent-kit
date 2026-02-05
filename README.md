# Coding Agent Kit (cakit)

Coding Agent Kit is a lightweight CLI for academic experiments. It installs and runs mainstream coding agents with a unified interface and returns structured stats (token usage, tool calls, runtime, version, etc.). “Coding agent” here means the CLI itself (including `cursor-agent` and `copilot`), not IDEs or IDE plugins (e.g., Cursor IDE or Copilot IDE extensions).

## Install

```bash
pip install git+https://github.com/zjsxply/coding-agent-kit
# or
uv tool install git+https://github.com/zjsxply/coding-agent-kit
```

## Commands

### Install an agent

Default is unrestricted mode (Yolo).

```bash
cakit install <agent> [--scope user|global]
```

By default, `--scope user` installs npm-based agents under `~/.npm-global` (no sudo). Ensure `~/.npm-global/bin` is on `PATH`.
Use `--scope global` to run `npm install -g` (may require sudo).

#### Supported Agents

| Name | Website | Docs | Notes |
| --- | --- | --- | --- |
| codex | [OpenAI Codex](https://openai.com/codex) | [Codex CLI](https://developers.openai.com/codex/cli) | — |
| claude | [Claude](https://www.anthropic.com/claude) | [Claude Code](https://docs.anthropic.com/en/docs/claude-code/quickstart) | — |
| copilot | [GitHub Copilot CLI](https://github.com/github/copilot-cli) | [Using Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/using-github-copilot-in-the-cli) | — |
| gemini | [Gemini CLI](https://google-gemini.github.io/gemini-cli/) | [Auth](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html) | — |
| kimi | [Kimi Code](https://www.kimi.com/code) | [Kimi CLI Docs](https://moonshotai.github.io/kimi-cli/en/) | — |
| qwen | [Qwen Code](https://qwenlm.github.io/qwen-code-docs/) | [Auth](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/) | — |
| openhands | [OpenHands](https://openhands.dev) | [Headless Mode](https://docs.openhands.dev/cli/headless-mode/) | — |
| swe-agent | [SWE-agent](https://swe-agent.com) | [CLI](https://swe-agent.com/latest/usage/cli/) | — |
| trae-oss | [Trae Agent](https://github.com/bytedance/trae-agent) | [README](https://github.com/bytedance/trae-agent#readme) | OSS Trae Agent to distinguish from other Trae products |
| cursor | [Cursor](https://cursor.com) | [CLI](https://docs.cursor.com/en/cli/using) | — |

#### Login

For OAuth, use the official CLI login. For API keys, copy `.env.template` to `.env`, then run `set -a; source .env; set +a` in the current shell.

- codex: `codex login`
- claude: run `claude`, then `/login` in the interactive UI; `ANTHROPIC_AUTH_TOKEN` is also supported
- copilot: run `copilot`, then `/login`; `GH_TOKEN`/`GITHUB_TOKEN` are also supported
- gemini: run `gemini` and choose Login with Google
- kimi: run `kimi`, then `/login` in the CLI
- qwen: run `qwen` and follow the browser login flow
- openhands: API only (see `.env.template`)
- swe-agent: API only (see `.env.template`)
- trae-oss: API only (see `.env.template`)
- cursor: `cursor-agent login`

### Generate .env template

```bash
cakit env --output .env
```

Writes the environment template file for configuring API keys and endpoints.

### Configure an agent

```bash
cakit configure <agent>
```

This regenerates the agent config based on current environment variables.
If you update environment variables later, rerun `cakit configure <agent>`.

### Run and output JSON stats

```bash
cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image]
# multiple images: repeat --image or use comma-separated paths
```

If the agent is not installed, `cakit run` will auto-run `cakit install <agent>` (user scope) with a notice.
Output fields:
- `agent`, `agent_version`
- `runtime_seconds`
- `prompt_tokens`, `completion_tokens`, `total_tokens`
- `models_usage` (per model, when available)
- `tool_calls` (best effort)
- `llm_calls`, `total_cost` (when provided by the agent)
- `telemetry_log` (when enabled)
- `exit_code`, `output_path`, `raw_output`

Telemetry:
- Qwen Code: local log `~/.qwen/telemetry.log`
- Gemini CLI: local log `~/.gemini/telemetry.log`
- Codex / Claude Code: exported via OpenTelemetry (OTEL, requires OTEL endpoint); log address is the OTEL endpoint
- Copilot CLI: local logs in `~/.copilot/logs/` by default (cakit uses `--log-dir` when running)

Image input support:

| Agent | Image input |
| --- | --- |
| codex | Supported via `--image` (multiple images allowed) |
| qwen | Supported via `@{path}` image injection in prompt |
| gemini | Supported via `read_many_files` for image files (cakit injects file paths) |
| claude | Interactive mode only (paste/path); not supported by `cakit run` |
| copilot | No image input documented in the CLI docs |
| kimi | No image input documented in CLI docs |
| openhands | No image input documented in CLI docs |
| swe-agent | No image input documented in CLI docs |
| trae-oss | No image input documented in CLI docs |
| cursor | No image input documented in CLI docs |

### Install fast shell power tools (recommended)

```bash
cakit tools
```

Installs (Linux only): `rg`, `fd`, `fzf`, `jq`, `yq`, `ast-grep`, `bat`, `git`, `git-delta`, `gh`.

## Environment Variables

- Full list in `.env.template`.
- `CAKIT_OUTPUT_DIR`: override log output directory.
- `CAKIT_TRAE_TRAJECTORY`: override Trae trajectory output path.
- `CAKIT_NPM_PREFIX`: override the user install prefix for npm-based agents (default: `~/.npm-global`).
- `CODEX_USE_OAUTH`: if set (e.g., `1`), Codex uses OAuth login instead of API key.

## Test Coverage Matrix

This project is not fully tested. ✓ = tested, ✗ = not supported, ✗* = not supported in headless mode adopted by `cakit run` but supported in interactive/GUI, blank = untested.

| Agent | OAuth | API | Image Input | MCP | Skills | Telemetry | Web Access | Test Version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex |  | ✓ | ✓ |  |  |  |  | 0.95.0 |
| claude |  |  | ✗* |  |  |  |  |  |
| copilot |  |  |  |  |  |  |  |  |
| gemini |  |  |  |  |  |  |  |  |
| kimi |  |  | ✗* |  |  |  |  |  |
| qwen |  |  |  |  |  |  |  |  |
| openhands | ✗ |  |  |  |  |  |  |  |
| swe-agent | ✗ |  |  |  |  |  |  |  |
| trae-oss | ✗ |  |  |  |  |  |  |  |
| cursor |  |  |  |  |  |  |  |  |

## Todo

- [ ] Support network on/off toggle
- [ ] Support skills and `AGENTS.md`
- [ ] Support MCP

Note: currently only supports Linux amd64.
