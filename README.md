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
cakit install [<agent|all|*>] [--scope user|global] [--version <value>]
```

By default, `--scope user` installs npm-based agents under `~/.npm-global` (no sudo). Ensure `~/.npm-global/bin` is on `PATH`.
For npm-based agents, use `--scope global` to run system-level install commands (may require sudo).
For Python/uv-based agents, `--scope` is currently ignored; cakit uses the agent installer's default behavior.
`all` and `*` install all supported agents (`*` should be quoted to avoid shell expansion).
If `<agent>` is omitted, it defaults to `all`.
When `--version` is omitted, `cakit install` always installs the latest upstream release available at install time.
Use `--version` to install a specific version or reference:
- `codex` / `codebuddy` / `claude` / `copilot` / `gemini` / `qwen` / `qoder` / `continue` / `crush` / `opencode` / `auggie` / `kilocode` / `openclaw` / `kimi`: npm package version or tag (for example `0.98.0`, `2026.2.15`, `1.9.0`).
- `aider`: `aider-chat` package version (for example `0.88.0`).
- `cursor`: Cursor build ID (for example `2026.01.28-fd13201`).
- `goose`: Goose CLI release version (for example `v1.2.3` or `1.2.3`).
- `deepagents`: `deepagents-cli` package version (for example `0.0.21`).
- `factory`: Factory CLI release version (for example `0.57.15`).
- `trae-cn`: TRAE CLI version (for example `0.111.5`).
- `openhands`: `openhands` package version (for example `1.12.1`).
- `swe-agent`: upstream release tag (for example `v1.0.0`).
- `trae-oss`: git ref (tag / branch / commit).

#### Supported Agents

| Name | Website | Docs | OSS Repository | Notes |
| --- | --- | --- | --- | --- |
| claude | [Claude](https://www.anthropic.com/claude) | [Claude Code](https://docs.anthropic.com/en/docs/claude-code/quickstart) | — | — |
| codex | [OpenAI Codex](https://openai.com/codex) | [Codex CLI](https://developers.openai.com/codex/cli) | [openai/codex](https://github.com/openai/codex) | — |
| codebuddy | [CodeBuddy](https://www.codebuddy.ai/) | [Docs](https://cnb.cool/codebuddy/codebuddy-code/-/blob/main/docs) | [codebuddy/codebuddy-code](https://cnb.cool/codebuddy/codebuddy-code) | OSS repo publishes docs/examples; npm package ships bundled CLI runtime |
| aider | [Aider](https://aider.chat/) | [Usage](https://aider.chat/docs/usage.html) | [Aider-AI/aider](https://github.com/Aider-AI/aider) | cakit runs `aider --message` with strict analytics-log parsing |
| cursor | [Cursor](https://cursor.com) | [CLI](https://docs.cursor.com/en/cli/using) | — | — |
| copilot | [GitHub Copilot CLI](https://github.com/github/copilot-cli) | [Using Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli) | — | — |
| gemini | [Gemini CLI](https://google-gemini.github.io/gemini-cli/) | [Auth](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html) | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | — |
| crush | [Crush](https://github.com/charmbracelet/crush) | [README](https://github.com/charmbracelet/crush#readme) | [charmbracelet/crush](https://github.com/charmbracelet/crush) | Formerly opencode (`opencode-ai/opencode`) |
| opencode | [OpenCode](https://opencode.ai/) | [Docs](https://opencode.ai/docs) | [anomalyco/opencode](https://github.com/anomalyco/opencode) | cakit runs `opencode run --format json` and extracts strict stats via exact `opencode export <sessionID>` |
| factory | [Factory](https://factory.ai/) | [Droid Exec](https://docs.factory.ai/cli/droid-exec/overview) | [Factory-AI/factory](https://github.com/Factory-AI/factory) | cakit runs `droid exec --output-format json` and parses exact session artifacts under `~/.factory/sessions` |
| auggie | [Auggie](https://github.com/augmentcode/auggie) | [CLI Overview](https://docs.augmentcode.com/cli/overview) | [augmentcode/auggie](https://github.com/augmentcode/auggie) | OSS repo publishes docs/examples; npm package ships bundled CLI runtime |
| continue | [Continue](https://www.continue.dev/) | [Continue CLI](https://github.com/continuedev/continue/tree/main/extensions/cli) | [continuedev/continue](https://github.com/continuedev/continue) | CLI binary is `cn` |
| goose | [Goose](https://block.github.io/goose/) | [Goose CLI Commands](https://block.github.io/goose/docs/guides/goose-cli-commands) | [block/goose](https://github.com/block/goose) | cakit runs goose in headless `run` mode with strict session export parsing |
| kilocode | [Kilo Code](https://kilo.ai) | [README](https://github.com/Kilo-Org/kilocode#readme) | [Kilo-Org/kilocode](https://github.com/Kilo-Org/kilocode) | cakit installs `@kilocode/cli` and parses run artifacts strictly |
| openclaw | [OpenClaw](https://openclaw.ai/) | [Getting Started](https://docs.openclaw.ai/start/getting-started) | [openclaw/openclaw](https://github.com/openclaw/openclaw) | cakit runs `openclaw agent --local --json` and parses session transcript strictly |
| deepagents | [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) | [Deep Agents CLI](https://docs.langchain.com/oss/python/deepagents/cli) | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | cakit installs `deepagents-cli` and parses session checkpoints strictly |
| kimi | [Kimi Code](https://www.kimi.com/code) | [Kimi CLI Docs](https://moonshotai.github.io/kimi-cli/en/) | [moonshotai/kimi-cli](https://github.com/moonshotai/kimi-cli) | — |
| trae-cn | [TRAE](https://www.trae.cn/) | [TRAE CLI Docs](https://docs.trae.cn/cli) | — | Official TRAE CLI from trae.cn |
| qwen | [Qwen Code](https://qwenlm.github.io/qwen-code-docs/) | [Auth](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/) | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | — |
| qoder | [Qoder](https://qoder.com) | [Qoder CLI Quick Start](https://docs.qoder.com/cli/quick-start) | — | cakit runs `qodercli` in non-interactive print mode and parses stream JSON strictly |
| openhands | [OpenHands](https://openhands.dev) | [Headless Mode](https://docs.openhands.dev/openhands/usage/cli/headless) | [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | — |
| swe-agent | [SWE-agent](https://swe-agent.com) | [CLI](https://swe-agent.com/latest/usage/cli/) | [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | — |
| trae-oss | [Trae Agent](https://github.com/bytedance/trae-agent) | [README](https://github.com/bytedance/trae-agent#readme) | [bytedance/trae-agent](https://github.com/bytedance/trae-agent) | OSS Trae Agent to distinguish from other Trae products |

#### Login

For OAuth, use the official CLI login. For API keys, copy `.env.template` to `.env`, then run `set -a; source .env; set +a` in the current shell (and rerun it after changing `.env`).
For coding agents with OpenAI-compatible API mode, shared fallback vars are also supported:
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_DEFAULT_MODEL`
When agent-specific API/base/model vars are unset, cakit maps these shared vars to the corresponding agent vars.
Model priority for those agents is: `--model` > agent-specific model env var > `OPENAI_DEFAULT_MODEL`.

- claude: run `claude`, then `/login` in the interactive UI; `ANTHROPIC_AUTH_TOKEN` is also supported
- codex: `codex login`
- codebuddy: OAuth via `codebuddy login`, or API via `CODEBUDDY_API_KEY` (+ `CODEBUDDY_BASE_URL` / `CODEBUDDY_MODEL` / `CODEBUDDY_INTERNET_ENVIRONMENT` as needed)
- aider: API only via `AIDER_OPENAI_API_KEY` + `AIDER_MODEL`
- cursor: `cursor-agent login`
- copilot: run `copilot`, then `/login`; `GH_TOKEN`/`GITHUB_TOKEN` are also supported
- gemini: run `gemini` and choose Login with Google
- crush: OAuth via `crush login` (for example `crush login hyper`), or API via `CRUSH_OPENAI_API_KEY` + `CRUSH_OPENAI_BASE_URL` + `CAKIT_CRUSH_MODEL`
- opencode: OAuth via `opencode auth login`, or API via `CAKIT_OPENCODE_OPENAI_API_KEY` + `CAKIT_OPENCODE_MODEL` (+ optional `CAKIT_OPENCODE_OPENAI_BASE_URL`; if model is bare, set `CAKIT_OPENCODE_PROVIDER`; for custom API models you can declare multimodal input capabilities via `CAKIT_OPENCODE_MODEL_CAPABILITIES=image,video`; provider list: `opencode models`)
- factory: OAuth via `droid` then `/login`, or API via `FACTORY_API_KEY`; BYOK custom models are supported via `CAKIT_FACTORY_BYOK_API_KEY` + `CAKIT_FACTORY_BYOK_BASE_URL` + `CAKIT_FACTORY_MODEL` (optional `CAKIT_FACTORY_BYOK_PROVIDER`; `OPENAI_*` fallback also applies)
- auggie: OAuth via `auggie login`, or API via `AUGMENT_API_TOKEN` + `AUGMENT_API_URL` (optional `AUGMENT_SESSION_AUTH`)
- continue: OAuth via `cn login`, or API via `CAKIT_CONTINUE_OPENAI_API_KEY` + `CAKIT_CONTINUE_OPENAI_MODEL` + `cakit configure continue`
- goose: API via `CAKIT_GOOSE_PROVIDER` + `CAKIT_GOOSE_MODEL` + `CAKIT_GOOSE_OPENAI_API_KEY` (+ `CAKIT_GOOSE_OPENAI_BASE_URL` for OpenAI-compatible endpoints)
- kilocode: API via `KILO_OPENAI_API_KEY` + `KILO_OPENAI_MODEL_ID` + `cakit configure kilocode`
- openclaw: API via `CAKIT_OPENCLAW_API_KEY` + `CAKIT_OPENCLAW_BASE_URL` + `CAKIT_OPENCLAW_MODEL` + `cakit configure openclaw`
- deepagents: API only via `DEEPAGENTS_OPENAI_API_KEY` + `DEEPAGENTS_OPENAI_MODEL`
- kimi: OAuth via `kimi` then `/login`, or API via `KIMI_API_KEY` + `cakit configure kimi`
- trae-cn: OAuth via `traecli` then `/login`, or API via `CAKIT_TRAE_CN_API_KEY` + `cakit configure trae-cn`
- qwen: run `qwen` and follow the browser login flow
- qoder: OAuth via `qodercli /login`, or Qoder token auth via `QODER_PERSONAL_ACCESS_TOKEN` (no custom OpenAI-compatible API auth)
- openhands: API only (`LLM_API_KEY` + `LLM_MODEL`, or `OPENAI_API_KEY` + `OPENAI_DEFAULT_MODEL` fallback; see `.env.template`)
- swe-agent: API only (see `.env.template`)
- trae-oss: API only (see `.env.template`)

### Generate .env template

```bash
cakit env --output .env [--lang en|zh]
```

Writes the environment template file for configuring API keys and endpoints.
`--lang en` writes from `.env.template`; `--lang zh` writes from `.env.template.zh`.

### Configure an agent

```bash
cakit configure [<agent|all|*>]
```

This regenerates the agent config based on current environment variables.
If `<agent>` is omitted, it defaults to `all`.
If you update environment variables later, rerun `set -a; source .env; set +a` and then rerun `cakit configure [<agent|all|*>]`.
If an agent does not require a config file, `cakit configure` may report `"config_path": null` and still succeed.
Note: Claude Code reads environment variables directly; `cakit configure claude` is a no-op.

### Run and output JSON stats

```bash
cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video] [--model <base_llm_model>] [--reasoning-effort <value>] [--env-file /path/to/extra.env]
# multiple images: repeat --image or use comma-separated paths
```

If the agent is not installed, `cakit run` will auto-run `cakit install <agent>` (user scope) with a notice.
`--model` overrides the base model for the current run (via agent model env vars and/or model CLI flags).
For OpenAI-compatible API agents, model priority is: `--model` > agent-specific model env var > `OPENAI_DEFAULT_MODEL`.
See `docs/model_override.md` for per-agent details.
`--reasoning-effort` is a unified per-run reasoning/thinking control.
See `docs/reasoning_effort.md` for per-agent options and mappings.
Exit code reference: `docs/exit_codes.md`.
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
- `cakit_exit_code` (cakit strict result code)
- `command_exit_code` (raw coding agent CLI process exit code)
- `output_path` (path to a `.log` file containing raw output from the coding agent CLI)
- `raw_output` (captured raw output from the coding agent CLI)
- `trajectory_path` (path to a formatted, human-readable trace file for the run; no truncation)

Strict success semantics:
- If command execution succeeds but any critical stats field is missing/invalid (`response`, non-empty `models_usage`, `llm_calls >= 1`, `tool_calls >= 0`, `trajectory_path`), `cakit run` exits non-zero.

Telemetry:
- Claude Code / Codex: exported via OpenTelemetry (OTEL, requires an OTEL endpoint); `telemetry_log` is set to that endpoint
- Copilot CLI: local logs in `~/.copilot/logs/` by default (cakit uses `--log-dir` when running)
- Gemini CLI: local log `~/.gemini/telemetry.log`
- Crush: local log `<run_data_dir>/logs/crush.log` (run-local `--data-dir`)
- Auggie CLI: run-local log `<tmp_run_dir>/auggie.log` (cakit passes `--log-file`)
- Qwen Code: local log `~/.qwen/telemetry.log`
- Qoder CLI: local log `~/.qoder/logs/qodercli.log`

Image and video input support:

| Agent | Image Input | Video Input | Notes |
| --- | --- | --- | --- |
| claude | ✓ | ✗ | `--image` + `Read` tool |
| codex | ✓ | ✗ | `--image` (multi-image) |
| codebuddy | ✓ | ✗ | `--image` is mapped to headless `stream-json` image blocks (`type: image`, base64); no documented `--video` input |
| aider | ✓ | ✗ | `--image` is mapped to Aider positional image files (`aider <image-file> ...`); model/provider dependent |
| cursor | ✗ | ✗ |  |
| copilot | ✓ | ✗ | `--image` uses natural-language file-path injection |
| gemini | ✓ | ✓ | symbolic local-path injection (`@{path}`); verified with `--model gemini-2.5-pro` (model-dependent) |
| crush | ✗ | ✗ | `crush run` has no `--image` / `--video` flags |
| opencode | ✓ | ✗ | native `--file` mapping works for `--image`; local `--video` is currently rejected as binary by upstream Read handling (opencode 1.2.6) |
| factory | ✓ | ✗ | `--image` uses natural-language local-path injection + `Read` tool; no documented generic `--video` flag |
| auggie | ✓ | ✗ | native `--image`; no documented `--video` flag |
| continue | ✗ | ✗ | `cn` has no documented `--image` / `--video` flags in headless mode |
| goose | ✓ | ✓ | natural-language local-path injection + built-in `developer` processors |
| kilocode | ✓ | ✗ | native `--attach`; no documented `--video` flag |
| openclaw | ✗ | ✗ | `openclaw agent` has no documented `--image` / `--video` flags |
| deepagents | ✗ | ✗ | `deepagents` non-interactive CLI has no documented `--image` / `--video` flags |
| kimi | ✓ | ✓ | `ReadMediaFile` + model capability (`image_in`/`video_in`) |
| trae-cn | ✗ | ✗ | `traecli` has no `--image` / `--video` flags |
| qwen | ✓ | ✓ | `@{path}` injection; depends on model capabilities |
| qoder | ✓ | ✗ | native `--attachment` mapping for `--image`; no `--video` support in cakit |
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

Installs (Linux only): `rg`, `fd`, `fzf`, `jq`, `yq`, `ast-grep`, `bat`, `git`, `git-lfs`, `git-delta`, `gh`, and Playwright Chromium (including runtime deps).

## Environment Variables

See `.env.template` for the full, up-to-date environment variable documentation.

## Test Coverage Matrix

This project is not fully tested. ✓ = tested, ✗ = not supported, blank = untested.

| Agent | OAuth | API | Image Input | Video Input | MCP | Skills | Telemetry | Web Access | Test Version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 2.1.44 |
| codex | ✓ | ✓ | ✓ | ✗ |  |  |  | ✓ | 0.101.0 |
| codebuddy |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 2.50.5 |
| aider | ✗ | ✓ | ✓ | ✗ |  |  |  | ✓ | 0.86.2 |
| cursor |  |  | ✗ | ✗ |  |  |  |  |  |
| copilot | ✓ | ✗ | ✓ | ✗ |  |  |  | ✓ | 0.0.410 |
| gemini |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 0.28.2 |
| crush |  | ✓ | ✗ | ✗ |  |  |  | ✓ | 0.43.0 |
| opencode |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 1.2.6 |
| factory |  |  |  | ✗ |  |  |  |  | 0.57.17 |
| auggie |  |  |  | ✗ |  |  | ✓ |  | 0.16.1 |
| continue |  | ✓ | ✗ | ✗ |  |  | ✓ | ✓ | 1.5.43 |
| goose |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 1.24.0 |
| kilocode |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 1.0.22 |
| openclaw |  | ✓ | ✗ | ✗ |  |  |  | ✓ | 2026.2.15 |
| deepagents | ✗ | ✓ | ✗ | ✗ |  |  |  | ✓ | 0.0.21 |
| kimi |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 1.12.0 |
| trae-cn | ✗ |  | ✗ | ✗ |  |  |  |  | 0.111.5 |
| qwen |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 0.10.3 |
| qoder |  | ✗ |  | ✗ |  |  |  |  | 0.1.28 |
| openhands | ✗ | ✓ | ✗ | ✗ |  |  |  | ✓ | 1.12.1 |
| swe-agent | ✗ |  | ✗ | ✗ |  |  |  |  | 1.1.0 |
| trae-oss | ✗ |  | ✗ | ✗ |  |  |  |  | 0.1.0 |

## Todo

- [ ] Add `cakit run` flag: disable web search vs fully disable network
- [ ] Support network on/off toggle
- [ ] Support `--timeout` in `cakit run` and return partial run artifacts on timeout
- [x] Support skills
- [ ] Support `AGENTS.md`
- [ ] For all agents, create an isolated run-specific `HOME` under `/tmp` and write run-specific config on every `cakit run`, to avoid cross-run session conflicts and guarantee stats match current run artifacts; remove the need for `cakit configure` (configuration should be fully managed by `cakit run`)
- [ ] Add a command to build a Docker image containing cakit, with selectable base image
- [ ] Namespace agent config/cache paths (e.g. `KIMI_SHARE_DIR`) to avoid conflicts with host agents
- [ ] Support MCP
- [ ] Support balanced mode
- [x] Support installing specific versions
- [x] Validate Kimi token accounting semantics (including subagent aggregation)

Note: currently only supports Linux amd64.
