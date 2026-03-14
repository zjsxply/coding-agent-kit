# Coding Agent Kit (cakit)

Coding Agent Kit is a lightweight CLI for academic experiments. It installs and runs mainstream coding agents with a unified interface and returns structured stats (token usage, tool calls, runtime, version, etc.). “Coding agent” here means the CLI itself (including `cursor-agent` and `copilot`), not IDEs or IDE plugins (e.g., Cursor IDE or Copilot IDE extensions).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/zjsxply/coding-agent-kit/main/install.sh | sh
# or
uv tool install git+https://github.com/zjsxply/coding-agent-kit
# or
pip install git+https://github.com/zjsxply/coding-agent-kit
```

`install.sh` bootstraps `uv` when needed and installs `cakit` into a stable tool/bin location (`/usr/local/bin` for root, otherwise a user-local bin dir). In fresh container environments, this is the recommended path.

## Commands

### Install an agent

The default is unrestricted mode (YOLO).

```bash
cakit install [<agent|all|*>] [--scope user|global] [--version <value>]
```

By default, `--scope user` installs npm-based agents under `~/.npm-global` (no sudo). Ensure `~/.npm-global/bin` is on `PATH`.
For npm-based agents, use `--scope global` to run system-level install commands (may require sudo).
Some agents try an official shell installer first and only fall back to npm if that path fails; for those agents, `--scope` only matters when the npm fallback is used.
For Python/uv-based agents, `--scope` is currently ignored; cakit uses the agent installer's default behavior.
`all` and `*` install all supported agents (`*` should be quoted to avoid shell expansion).
If `<agent>` is omitted, it defaults to `all`.
For `all` / `*`, cakit installs targets in parallel and reports failed agents together in the final aggregate output instead of stopping at the first failure.
When `--version` is omitted, `cakit install` always installs the latest upstream release available at install time.
Use `--version` to install a specific version or reference:
- `codex` / `codebuddy` / `gemini` / `qwen` / `qoder` / `continue` / `crush` / `auggie` / `kilocode` / `kimi`: npm package version or tag (for example `0.98.0`, `2026.2.15`, `1.9.0`).
- `claude`: Claude Code install-script selector (for example `stable` or an exact Claude Code version supported by Anthropic's installer). cakit tries the official install script first and falls back to the deprecated npm package `@anthropic-ai/claude-code` if the script path fails.
- `copilot`: Copilot installer `VERSION` value (for example `1.0.3`). cakit tries the official installer first and falls back to the npm package `@github/copilot` if the script path fails.
- `openclaw`: OpenClaw install-script `--version` value (for example `1.0.0`). cakit tries the official installer first and falls back to the npm package `openclaw` if the script path fails.
- `opencode`: OpenCode install-script `--version` value (for example `0.0.8`). cakit tries the official installer first and falls back to the npm package `opencode-ai` if the script path fails.
- `aider`: `aider-chat` package version (for example `0.88.0`).
- `cursor`: Cursor build ID (for example `2026.01.28-fd13201`).
- `goose`: Goose CLI release version (for example `v1.2.3` or `1.2.3`).
- `deepagents`: `deepagents-cli` package version (for example `0.0.21`).
- `factory`: Factory CLI release version (for example `0.57.15`).
- `trae-cn`: TRAE CLI version (for example `0.111.5`).
- `openhands`: `openhands` package version (for example `1.12.1`).
- `swe-agent`: upstream git ref / release tag (for example `v1.1.0`).
- `trae-oss`: git ref (tag / branch / commit).

For a per-agent install-method and runtime-dependency matrix, see `docs/runtime_dependencies_compatibility.md`.

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
| swe-agent | [SWE-agent](https://swe-agent.com) | [CLI](https://swe-agent.com/latest/usage/cli/) | [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | cakit installs upstream git tags via `uv tool` and reads `.traj` outputs when `--output_dir` is supported |
| trae-oss | [Trae Agent](https://github.com/bytedance/trae-agent) | [README](https://github.com/bytedance/trae-agent#readme) | [bytedance/trae-agent](https://github.com/bytedance/trae-agent) | OSS Trae Agent to distinguish from other Trae products |

#### Login

For OAuth, use the official CLI login. For API keys, copy `.env.template` to `.env`, then run `set -a; source .env; set +a` in the current shell (and rerun it after changing `.env`).
Some coding agents with OpenAI-compatible API mode support shared fallback vars:
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_DEFAULT_MODEL`
Support is agent-specific. Use the documented cakit-managed `*_BASE_URL` vars as the preferred names; when an agent doc explicitly says shared `OPENAI_BASE_URL` fallback is supported, cakit preserves that fallback.
Model priority for those agents is: `--model` > agent-specific model env var > `OPENAI_DEFAULT_MODEL`.

- claude: run `claude`, then `/login` in the interactive UI; `ANTHROPIC_AUTH_TOKEN` is also supported
- codex: `codex login`, or API via `CODEX_API_KEY` (+ optional `CODEX_BASE_URL`; shared `OPENAI_*` fallback applies)
- codebuddy: OAuth via `codebuddy login`, or API via `CODEBUDDY_API_KEY` (+ `CODEBUDDY_BASE_URL` / `CODEBUDDY_MODEL` / `CODEBUDDY_INTERNET_ENVIRONMENT` as needed)
- aider: API only via `AIDER_OPENAI_API_KEY` + `AIDER_MODEL` (+ optional `AIDER_OPENAI_BASE_URL`; shared `OPENAI_*` fallback applies)
- cursor: `cursor-agent login`, or API via `CURSOR_API_KEY` (+ optional `CURSOR_BASE_URL`; shared `OPENAI_*` fallback applies, and cakit maps the resolved value to Cursor's `--endpoint`)
- copilot: run `copilot`, then `/login`; `GH_TOKEN`/`GITHUB_TOKEN` are also supported
- gemini: run `gemini` and choose Login with Google
- crush: OAuth via `crush login` (for example `crush login hyper`), or API via `CRUSH_OPENAI_API_KEY` + `CRUSH_OPENAI_BASE_URL` + `CAKIT_CRUSH_MODEL`
- opencode: OAuth via `opencode auth login`, or API via `CAKIT_OPENCODE_OPENAI_API_KEY` + `CAKIT_OPENCODE_MODEL` (+ optional `CAKIT_OPENCODE_OPENAI_BASE_URL`; if model is bare, set `CAKIT_OPENCODE_PROVIDER`; for custom API models you can declare multimodal input capabilities via `CAKIT_OPENCODE_MODEL_CAPABILITIES=image,video`; provider list: `opencode models`)
- factory: OAuth via `droid` then `/login`, or API via `FACTORY_API_KEY` (+ optional `FACTORY_BASE_URL`, which cakit maps to Droid's internal `FACTORY_API_BASE_URL` when set); BYOK custom models are supported via `CAKIT_FACTORY_BYOK_API_KEY` + `CAKIT_FACTORY_BYOK_BASE_URL` + `CAKIT_FACTORY_MODEL` (optional `CAKIT_FACTORY_BYOK_PROVIDER`; `OPENAI_*` fallback applies for BYOK)
- auggie: OAuth via `auggie login`, or API via `AUGMENT_API_TOKEN` + `AUGMENT_API_URL` (optional `AUGMENT_SESSION_AUTH`)
- continue: OAuth via `cn login`, or API via `CAKIT_CONTINUE_OPENAI_API_KEY` + `CAKIT_CONTINUE_OPENAI_MODEL` + `cakit configure continue`
- goose: API via `CAKIT_GOOSE_PROVIDER` + `CAKIT_GOOSE_MODEL` + `CAKIT_GOOSE_OPENAI_API_KEY` (+ `CAKIT_GOOSE_OPENAI_BASE_URL` for OpenAI-compatible endpoints)
- kilocode: API via `KILO_OPENAI_API_KEY` + `KILO_OPENAI_MODEL_ID` (+ optional `KILO_OPENAI_BASE_URL`; `cakit configure kilocode` only persists legacy-compatible local config)
- openclaw: API via `CAKIT_OPENCLAW_API_KEY` + `CAKIT_OPENCLAW_BASE_URL` + `CAKIT_OPENCLAW_MODEL` + `cakit configure openclaw`
- deepagents: API only via `DEEPAGENTS_OPENAI_API_KEY` + `DEEPAGENTS_OPENAI_MODEL`
- kimi: OAuth via `kimi` then `/login`, or API via `KIMI_API_KEY` + `cakit configure kimi`
- trae-cn: OAuth via `traecli` then `/login`, or API via `CAKIT_TRAE_CN_API_KEY` + `cakit configure trae-cn`
- qwen: run `qwen` and follow the browser login flow, or API via `QWEN_OPENAI_API_KEY` (+ optional `QWEN_OPENAI_BASE_URL` / `QWEN_OPENAI_MODEL`; shared `OPENAI_*` fallback applies)
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
If `CAKIT_CONFIGURE_POST_COMMAND` is set, cakit runs that `bash -lc` command after a target writes a config file and exposes `CAKIT_CONFIGURE_AGENT`, `CAKIT_CONFIG_PATH`, and `CAKIT_CONFIG_DIR` to the hook.
If the post-config command exits non-zero, `cakit configure` fails for that target.
This hook is usually agent-specific; prefer exporting it ad hoc before `cakit configure <agent>` instead of saving it permanently in `.env`.
Example: disable Codex web search after `cakit configure codex`:

```bash
export CAKIT_CONFIGURE_POST_COMMAND='if [ "$CAKIT_CONFIGURE_AGENT" = "codex" ]; then printf "\nweb_search = \"disabled\"\n" >> "$CAKIT_CONFIG_PATH"; fi'
cakit configure codex
```

For Codex specifically, `cakit run codex` currently invokes `codex exec --dangerously-bypass-approvals-and-sandbox`, so sandbox keys such as `[sandbox_workspace_write].network_access = false` are written to config but are not enforced by `cakit run codex`.

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
- Qwen Code: run-unique local log `~/.qwen/telemetry/cakit-<timestamp>-<ns>-<id>.log`
- Qoder CLI: local log `~/.qoder/logs/qodercli.log`

Image and video input support:

| Agent | Image Input | Video Input | Notes |
| --- | --- | --- | --- |
| claude | ✓ | ✗ | `--image` + `Read` tool |
| codex | ✓ | ✗ | `--image` (multi-image) |
| codebuddy | ✓ | ✗ | `--image` is mapped to headless `stream-json` image blocks (`type: image`, base64); no documented `--video` input |
| aider | ✓ | ✗ | `--image` is mapped to Aider positional image files (`aider <image-file> ...`); cakit also writes run-local vision metadata for unknown OpenAI-compatible `kimi-*` models |
| cursor | ✗ | ✗ |  |
| copilot | ✓ | ✗ | `--image` uses natural-language file-path injection |
| gemini | ✓ | ✓ | symbolic local-path injection (`@{path}`); verified with `--model gemini-2.5-pro` (model-dependent) |
| crush | ✗ | ✗ | `crush run` has no `--image` / `--video` flags |
| opencode | ✓ | ✗ | native `--file` mapping works for `--image`; local `--video` is currently rejected as binary by upstream Read handling (opencode 1.2.24) |
| factory | ✓ | ✗ | `--image` uses natural-language local-path injection + `Read` tool; no documented generic `--video` flag |
| auggie | ✓ | ✗ | native `--image`; no documented `--video` flag |
| continue | ✗ | ✗ | `cn` has no documented `--image` / `--video` flags in headless mode |
| goose | ✓ | ✓ | natural-language local-path injection + built-in `developer` processors |
| kilocode | ✓ | ✗ | native `--attach`; no documented `--video` flag |
| openclaw | ✗ | ✗ | `openclaw agent` has no documented `--image` / `--video` flags |
| deepagents | ✗ | ✗ | `deepagents` non-interactive CLI has no documented `--image` / `--video` flags |
| kimi | ✓ | ✓ | native `ReadMediaFile` / model capability (`image_in`/`video_in`) only; when provider metadata is incomplete, set `KIMI_MODEL_CAPABILITIES` explicitly |
| trae-cn | ✗ | ✗ | `traecli` has no `--image` / `--video` flags |
| qwen | ✓ |  | `@{path}` injection; best verified with Qwen OAuth / DashScope-compatible vision setup, while generic OpenAI-compatible API mode remains provider-dependent |
| qoder | ✓ | ✗ | native `--attachment` mapping for `--image`; no `--video` support in cakit |
| openhands | ✗ | ✗ | headless CLI has no documented `--image` / `--video` flags |
| swe-agent | ✗ | ✗ | upstream multimodal path supports issue-image URLs (`swe_bench_multimodal`), but `sweagent run` has no generic `--image` / `--video` flags |
| trae-oss | ✗ | ✗ | `trae-cli run` has no `--image` / `--video` flags |

Swarm-like multiagent spawn:
- For the current status and enablement notes for Kimi / Claude / Codex / OpenClaw / Goose, see `docs/swarm_like_spawn.md`.

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
Supported package managers: `apt-get`, `apk`, `dnf`, `microdnf`, `yum`, `zypper`, and `pacman`.
On apt-based distros, cakit also attempts to install Playwright Chromium runtime deps and the browser. On other Linux distros, Playwright Chromium may be skipped while the rest of the toolchain still installs.
Successful steps stay quiet; if a tool install fails, cakit continues with the remaining tools and reports `installed` / `skipped` / `failed` in the final JSON output.

## Environment Variables

See `.env.template` for the full, up-to-date environment variable documentation.

## Test Coverage Matrix

This project is not fully tested. ✓ = tested, ✗ = not supported, blank = untested.

| Agent | OAuth | API | Image Input | Video Input | MCP | Skills | Telemetry | Web Access | Test Version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 2.1.72 |
| codex | ✓ | ✓ | ✓ | ✗ |  |  |  | ✓ | 0.114.0 |
| codebuddy |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 2.58.0 |
| aider | ✗ | ✓ | ✓ | ✗ |  |  |  | ✓ | 0.86.2 |
| cursor |  |  | ✗ | ✗ |  |  |  |  | 2026.02.27-e7d2ef6 |
| copilot | ✓ | ✗ | ✓ | ✗ |  |  |  | ✓ | 1.0.4 |
| gemini |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 0.33.0 |
| crush |  | ✓ | ✗ | ✗ |  |  |  | ✓ | 0.47.2 |
| opencode |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 1.2.24 |
| factory |  |  |  | ✗ |  |  |  |  | 0.72.0 |
| auggie |  |  |  | ✗ |  |  | ✓ |  | 0.16.1 |
| continue |  | ✓ | ✗ | ✗ |  |  | ✓ | ✓ | 1.5.45 |
| goose |  | ✓ | ✓ | ✓ |  |  |  | ✓ | 1.27.2 |
| kilocode |  | ✓ | ✓ | ✗ |  |  |  | ✓ | 7.0.44 |
| openclaw |  | ✓ | ✗ | ✗ |  |  |  | ✓ | 2026.3.8 |
| deepagents | ✗ | ✓ | ✗ | ✗ |  |  |  | ✓ | 0.0.31 |
| kimi |  | ✓ | ✓ |  |  |  |  | ✓ | 1.12.0 |
| trae-cn | ✗ |  | ✗ | ✗ |  |  |  |  | 0.111.5 |
| qwen |  | ✓ | ✓ |  |  |  |  | ✓ | 0.12.3 |
| qoder |  | ✗ |  | ✗ |  |  |  |  | 0.1.28 |
| openhands | ✗ | ✓ | ✗ | ✗ |  |  |  | ✓ | 1.12.1 |
| swe-agent | ✗ |  | ✗ | ✗ |  |  |  |  | 1.1.0 |
| trae-oss | ✗ |  | ✗ | ✗ |  |  |  |  | 0.1.0 |

## Todo

- [ ] Add `cakit run` flag: disable web search vs fully disable network
- [ ] Add an API mock server to simplify testing
- [ ] Support `--timeout` in `cakit run` and return partial run artifacts on timeout
- [ ] Support `AGENTS.md`
- [ ] For all agents, create an isolated run-specific `HOME` under `/tmp` and write run-specific config on every `cakit run`, to avoid cross-run session conflicts and guarantee stats match current run artifacts
- [ ] Add a command to build a Docker image containing cakit, with selectable base image
- [ ] `cakit` should no longer need the `configure` command (configuration should be fully managed automatically by `cakit run`)
- [ ] Support MCP
- [ ] Support balanced mode
- [ ] Expand Ubuntu and Debian coverage to releases from the last ten years and ensure tests pass
- [ ] Support Playwright in `cakit tools` on other Linux distributions
- [ ] Support ARM
- [x] Support multiagent
- [ ] Record versions once per month and verify CI test functionality
- [x] Write an install script `.sh`, then add test points that start Docker containers (including Ubuntu, Debian, etc.) to ensure the install script can install cakit successfully in arbitrary Docker image environments
- [x] Support additional setup scripts
- [x] Support skills
- [x] Support installing specific versions
- [x] Validate Kimi token accounting semantics (including subagent aggregation)

Note: currently only supports Linux amd64.
