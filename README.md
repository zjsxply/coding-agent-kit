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

The default is unrestricted mode (YOLO).

```bash
cakit install <agent> [--scope user|global] [--version <value>]
```

By default, `--scope user` installs npm-based agents under `~/.npm-global` (no sudo). Ensure `~/.npm-global/bin` is on `PATH`.
Use `--scope global` to run `npm install -g` (may require sudo).
Use `--version` to install a specific version or reference:
- `codex` / `claude` / `copilot` / `gemini` / `qwen`: npm version or tag (for example `0.98.0`).
- `cursor`: Cursor build ID (for example `2026.01.28-fd13201`).
- `kimi`: `kimi-cli` package version (for example `1.9.0`).
- `openhands`: `openhands` package version (for example `1.12.1`).
- `swe-agent`: upstream release tag (for example `v1.0.0`).
- `trae-oss`: git ref (tag / branch / commit).

#### Supported Agents

| Name | Website | Docs | OSS Repository | Notes |
| --- | --- | --- | --- | --- |
| claude | [Claude](https://www.anthropic.com/claude) | [Claude Code](https://docs.anthropic.com/en/docs/claude-code/quickstart) | — | — |
| codex | [OpenAI Codex](https://openai.com/codex) | [Codex CLI](https://developers.openai.com/codex/cli) | [openai/codex](https://github.com/openai/codex) | — |
| cursor | [Cursor](https://cursor.com) | [CLI](https://docs.cursor.com/en/cli/using) | — | — |
| copilot | [GitHub Copilot CLI](https://github.com/github/copilot-cli) | [Using Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli) | — | — |
| gemini | [Gemini CLI](https://google-gemini.github.io/gemini-cli/) | [Auth](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html) | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | — |
| kimi | [Kimi Code](https://www.kimi.com/code) | [Kimi CLI Docs](https://moonshotai.github.io/kimi-cli/en/) | [moonshotai/kimi-cli](https://github.com/moonshotai/kimi-cli) | — |
| qwen | [Qwen Code](https://qwenlm.github.io/qwen-code-docs/) | [Auth](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/) | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | — |
| openhands | [OpenHands](https://openhands.dev) | [Headless Mode](https://docs.openhands.dev/openhands/usage/cli/headless) | [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | — |
| swe-agent | [SWE-agent](https://swe-agent.com) | [CLI](https://swe-agent.com/latest/usage/cli/) | [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | — |
| trae-oss | [Trae Agent](https://github.com/bytedance/trae-agent) | [README](https://github.com/bytedance/trae-agent#readme) | [bytedance/trae-agent](https://github.com/bytedance/trae-agent) | OSS Trae Agent to distinguish from other Trae products |

#### Login

For OAuth, use the official CLI login. For API keys, copy `.env.template` to `.env`, then run `set -a; source .env; set +a` in the current shell (and rerun it after changing `.env`).

- claude: run `claude`, then `/login` in the interactive UI; `ANTHROPIC_AUTH_TOKEN` is also supported
- codex: `codex login`
- cursor: `cursor-agent login`
- copilot: run `copilot`, then `/login`; `GH_TOKEN`/`GITHUB_TOKEN` are also supported
- gemini: run `gemini` and choose Login with Google
- kimi: OAuth via `kimi` then `/login`, or API via `KIMI_API_KEY` + `cakit configure kimi`
- qwen: run `qwen` and follow the browser login flow
- openhands: API only (`LLM_API_KEY` + `LLM_MODEL`, see `.env.template`)
- swe-agent: API only (see `.env.template`)
- trae-oss: API only (see `.env.template`)

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
If you update environment variables later, rerun `set -a; source .env; set +a` and then rerun `cakit configure <agent>`.
If an agent does not require a config file, `cakit configure` may report `"config_path": null` and still succeed.
Note: Claude Code reads environment variables directly; `cakit configure claude` is a no-op.

### Run and output JSON stats

```bash
cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video] [--model <base_llm_model>] [--reasoning-effort <value>] [--env-file /path/to/extra.env]
# multiple images: repeat --image or use comma-separated paths
```

If the agent is not installed, `cakit run` will auto-run `cakit install <agent>` (user scope) with a notice.
`--model` overrides the base model for the current run (via agent model env vars and/or model CLI flags).
See `docs/model_override.md` for per-agent details.
`--reasoning-effort` is a unified per-run reasoning/thinking control.
See `docs/reasoning_effort.md` for per-agent options and mappings.
Environment isolation:
- cakit only passes cakit-managed environment variables to the coding agent (the variables listed in `.env.template` and any values it sets explicitly).
- The rest of the current shell environment is not inherited by the coding agent process.
- If you need to pass additional variables, put them in a file and use `--env-file`.
Output fields:
- `agent`, `agent_version`
- `runtime_seconds`
- `response` (final reply message from the coding agent)
- `models_usage` (per model, includes `prompt_tokens`, `completion_tokens`, `total_tokens` when available)
- `total_cost` (when provided by the agent)
- `llm_calls`
- `tool_calls` (when provided by the agent)
- `telemetry_log` (when enabled)
- `exit_code`
- `output_path` (path to a `.log` file containing raw output from the coding agent CLI)
- `raw_output` (captured raw output from the coding agent CLI)
- `trajectory_path` (path to a formatted, human-readable trace file for the run; no truncation)

Telemetry:
- Claude Code / Codex: exported via OpenTelemetry (OTEL, requires an OTEL endpoint); `telemetry_log` is set to that endpoint
- Copilot CLI: local logs in `~/.copilot/logs/` by default (cakit uses `--log-dir` when running)
- Gemini CLI: local log `~/.gemini/telemetry.log`
- Qwen Code: local log `~/.qwen/telemetry.log`

Image and video input support:

| Agent | Image Input | Video Input | Notes |
| --- | --- | --- | --- |
| claude | ✓ | ✗ | `--image` + `Read` tool |
| codex | ✓ | ✗ | `--image` (multi-image) |
| cursor | ✗ | ✗ |  |
| copilot | ✓ | ✗ | `--image` uses natural-language file-path injection |
| gemini | ✓ | ✓ | staged media + `@{path}` injection |
| kimi | ✓ | ✓ | `ReadMediaFile` + model capability (`image_in`/`video_in`) |
| qwen | ✓ | ✓ | `@{path}` injection; depends on model capabilities |
| openhands | ✗ | ✗ | headless CLI has no documented `--image` / `--video` flags |
| swe-agent | ✗ | ✗ | upstream multimodal path supports issue-image URLs (`swe_bench_multimodal`), but `sweagent run` has no generic `--image` / `--video` flags |
| trae-oss | ✗ | ✗ | `trae-cli run` has no `--image` / `--video` flags |

Kimi Agent Swarm:
- Kimi supports launching multiple subagents in one run.
- In your prompt, use wording like `launch multiple subagents` (for example: "Can you launch multiple subagents to solve this task and summarize the results?").
- For Kimi runs, `models_usage`/`llm_calls`/`tool_calls` are aggregated from subagent events in session logs when available.
Note: In our testing, Kimi CLI may hit a race condition when multiple sessions run concurrently, leading to failures. Avoid running multiple Kimi sessions at the same time.

### Skills

Skills are reusable instruction/tooling packs for coding agents (see [agentskills.io](https://agentskills.io)). Install a skill repo with:

```bash
npx skills add <skills> -g [-a <agent1> <agent2> ...]
```

Use `-g`/`--global` to reuse across projects. Example:

```bash
npx skills add vercel-labs/agent-skills -g -a claude-code codex
```

Note: the coding agent names used by `skills` may differ from `cakit` agent names (e.g., `claude-code` vs `cakit`’s `claude`). If something does not work, run `npx skills -h`.

`npx skills` docs: [skills.sh](https://skills.sh/) and [vercel-labs/skills](https://github.com/vercel-labs/skills).

For scripts/CI, prefer non-interactive flags to avoid prompts, e.g.:

```bash
npx skills add --skill <skills> -g --agent '*' -y
```

`cakit` also provides a thin pass-through wrapper: `cakit skills ...` (it delegates to `npx skills ...`).

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
- `CAKIT_CODEX_USE_OAUTH`: if set (e.g., `1`), Codex uses OAuth login instead of API key.
- `CAKIT_CLAUDE_USE_OAUTH`: if set (e.g., `1`) and both Claude API key/token are present, prefer OAuth token.
- `CAKIT_KIMI_PROVIDER_TYPE`: Kimi provider `type` (`kimi`, `openai_legacy`, or `openai_responses`).
- `GOOGLE_API_KEY`: upstream Gemini/Vertex key used by Gemini CLI.
- `CAKIT_QWEN_GOOGLE_API_KEY`: cakit-only per-agent override for Qwen to avoid `GOOGLE_API_KEY` collisions.

## Test Coverage Matrix

This project is not fully tested. ✓ = tested, ✗ = not supported, ✗* = not supported in headless mode adopted by `cakit run` but supported in interactive/GUI, ⚠ = test failed or blocked by missing auth/config/runtime prerequisites, blank = untested.

| Agent | OAuth | API | Image Input | Video Input | MCP | Skills | Telemetry | Web Access | Test Version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 2.1.37 |
| codex | ✓ | ✓ | ✓ | ✗ |  |  |  | ✓ | 0.98.0 |
| cursor |  |  | ✗ | ✗ |  |  |  |  |  |
| copilot | ✓ | ✗ | ✓ | ✗ |  |  |  | ✓ | 0.0.408 |
| gemini |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 0.27.3 |
| kimi |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 1.9.0 |
| qwen |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 0.10.0 |
| openhands | ✗ | ✓ | ✗ | ✗ |  |  |  | ✓ | 1.12.1 |
| swe-agent | ✗ |  | ✗ | ✗ |  |  |  |  |  |
| trae-oss | ✗ |  | ✗ | ✗ |  |  |  |  |  |

## Todo

- [ ] Add `cakit run` flag: disable web search vs fully disable network
- [ ] Support network on/off toggle
- [ ] Support `--timeout` in `cakit run` and return partial run artifacts on timeout
- [x] Support skills
- [ ] Support `AGENTS.md`
- [ ] Namespace agent config/cache paths (e.g. `KIMI_SHARE_DIR`) to avoid conflicts with host agents
- [ ] Support MCP
- [ ] Support balanced mode
- [x] Support installing specific versions
- [x] Validate Kimi token accounting semantics (including subagent aggregation)

Note: currently only supports Linux amd64.
