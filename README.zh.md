# Coding Agent Kit（cakit）

Coding Agent Kit 是面向学术实验的轻量 CLI，用统一方式安装和运行主流 coding agent，并返回结构化统计信息（token 用量、工具调用次数、运行时间、版本等）。这里的 coding agent 指 CLI，不包括 IDE/IDE 插件（如 Cursor IDE 或 Copilot 的 IDE 插件），但包括 cursor-agent 和 copilot CLI。

## 安装

```bash
pip install git+https://github.com/zjsxply/coding-agent-kit
# 或
uv tool install git+https://github.com/zjsxply/coding-agent-kit
```

## 命令

### 安装 agent

默认无限制模式（Yolo）。

```bash
cakit install [<agent|all|*>] [--scope user|global] [--version <value>]
```

默认 `--scope user` 会把 npm 类 agent 安装到 `~/.npm-global`（无需 sudo），请确保 `~/.npm-global/bin` 在 `PATH` 中。
对于 npm 类 agent，如需全局安装，使用 `--scope global`（会执行系统级安装命令，可能需要 sudo）。
对于 Python/uv 类 agent，`--scope` 当前会被忽略，安装行为按对应 agent 安装器默认逻辑执行。
`all` 和 `*` 可用于安装全部已支持 agent（`*` 需加引号，避免被 shell 展开）。
省略 `<agent>` 时，默认等同于 `all`。
未传 `--version` 时，`cakit install` 始终安装执行当下可获得的上游最新版本（latest）。
可使用 `--version` 指定安装版本/引用：
- `codex` / `codebuddy` / `claude` / `copilot` / `gemini` / `qwen` / `qoder` / `continue` / `crush` / `opencode` / `auggie` / `kilocode` / `openclaw` / `kimi`：npm 包版本号或 tag（例如 `0.98.0`、`2026.2.15`、`1.9.0`）。
- `aider`：`aider-chat` 包版本（例如 `0.88.0`）。
- `cursor`：Cursor 构建号（例如 `2026.01.28-fd13201`）。
- `goose`：Goose CLI release 版本（例如 `v1.2.3` 或 `1.2.3`）。
- `deepagents`：`deepagents-cli` 包版本（例如 `0.0.21`）。
- `factory`：Factory CLI release 版本（例如 `0.57.15`）。
- `trae-cn`：TRAE CLI 版本（例如 `0.111.5`）。
- `openhands`：`openhands` 包版本（例如 `1.12.1`）。
- `swe-agent`：上游 release tag（例如 `v1.0.0`）。
- `trae-oss`：git 引用（tag / branch / commit）。

#### 支持的 Agent

| 名称 | 官网 | 文档 | 开源仓库 | 备注 |
| --- | --- | --- | --- | --- |
| claude | [Claude](https://www.anthropic.com/claude) | [Claude Code](https://docs.anthropic.com/en/docs/claude-code/quickstart) | — | — |
| codex | [OpenAI Codex](https://openai.com/codex) | [Codex CLI](https://developers.openai.com/codex/cli) | [openai/codex](https://github.com/openai/codex) | — |
| codebuddy | [CodeBuddy](https://www.codebuddy.ai/) | [Docs](https://cnb.cool/codebuddy/codebuddy-code/-/blob/main/docs) | [codebuddy/codebuddy-code](https://cnb.cool/codebuddy/codebuddy-code) | 开源仓库主要发布文档/示例；npm 包内置 CLI 运行时 |
| aider | [Aider](https://aider.chat/) | [Usage](https://aider.chat/docs/usage.html) | [Aider-AI/aider](https://github.com/Aider-AI/aider) | cakit 运行 `aider --message` 并对 analytics-log 做严格解析 |
| cursor | [Cursor](https://cursor.com) | [CLI](https://docs.cursor.com/en/cli/using) | — | — |
| copilot | [GitHub Copilot CLI](https://github.com/github/copilot-cli) | [Using Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli) | — | — |
| gemini | [Gemini CLI](https://google-gemini.github.io/gemini-cli/) | [Auth](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html) | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | — |
| crush | [Crush](https://github.com/charmbracelet/crush) | [README](https://github.com/charmbracelet/crush#readme) | [charmbracelet/crush](https://github.com/charmbracelet/crush) | 原项目为 opencode（`opencode-ai/opencode`） |
| opencode | [OpenCode](https://opencode.ai/) | [Docs](https://opencode.ai/docs) | [anomalyco/opencode](https://github.com/anomalyco/opencode) | cakit 运行 `opencode run --format json`，并基于精确 `opencode export <sessionID>` 严格提取统计 |
| factory | [Factory](https://factory.ai/) | [Droid Exec](https://docs.factory.ai/cli/droid-exec/overview) | [Factory-AI/factory](https://github.com/Factory-AI/factory) | cakit 运行 `droid exec --output-format json`，并严格解析 `~/.factory/sessions` 下的精确会话产物 |
| auggie | [Auggie](https://github.com/augmentcode/auggie) | [CLI Overview](https://docs.augmentcode.com/cli/overview) | [augmentcode/auggie](https://github.com/augmentcode/auggie) | 开源仓库主要发布文档/示例；npm 包内置 CLI 运行时 |
| continue | [Continue](https://www.continue.dev/) | [Continue CLI](https://github.com/continuedev/continue/tree/main/extensions/cli) | [continuedev/continue](https://github.com/continuedev/continue) | CLI 可执行名为 `cn` |
| goose | [Goose](https://block.github.io/goose/) | [Goose CLI Commands](https://block.github.io/goose/docs/guides/goose-cli-commands) | [block/goose](https://github.com/block/goose) | cakit 以 headless `run` 模式运行 goose，并对 session export 做严格解析 |
| kilocode | [Kilo Code](https://kilo.ai) | [README](https://github.com/Kilo-Org/kilocode#readme) | [Kilo-Org/kilocode](https://github.com/Kilo-Org/kilocode) | cakit 安装 `@kilocode/cli`，并严格解析运行产物 |
| openclaw | [OpenClaw](https://openclaw.ai/) | [Getting Started](https://docs.openclaw.ai/start/getting-started) | [openclaw/openclaw](https://github.com/openclaw/openclaw) | cakit 运行 `openclaw agent --local --json`，并严格解析会话 transcript |
| deepagents | [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) | [Deep Agents CLI](https://docs.langchain.com/oss/python/deepagents/cli) | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | cakit 安装 `deepagents-cli`，并严格解析会话 checkpoint |
| kimi | [Kimi Code](https://www.kimi.com/code) | [Kimi CLI Docs](https://moonshotai.github.io/kimi-cli/en/) | [moonshotai/kimi-cli](https://github.com/moonshotai/kimi-cli) | — |
| trae-cn | [TRAE](https://www.trae.cn/) | [TRAE CLI Docs](https://docs.trae.cn/cli) | — | 来自 trae.cn 的官方 TRAE CLI |
| qwen | [Qwen Code](https://qwenlm.github.io/qwen-code-docs/) | [Auth](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/) | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | — |
| qoder | [Qoder](https://qoder.com) | [Qoder CLI Quick Start](https://docs.qoder.com/cli/quick-start) | — | cakit 以非交互 print 模式运行 `qodercli`，并严格解析 stream JSON |
| openhands | [OpenHands](https://openhands.dev) | [Headless Mode](https://docs.openhands.dev/openhands/usage/cli/headless) | [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | — |
| swe-agent | [SWE-agent](https://swe-agent.com) | [CLI](https://swe-agent.com/latest/usage/cli/) | [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | — |
| trae-oss | [Trae Agent](https://github.com/bytedance/trae-agent) | [README](https://github.com/bytedance/trae-agent#readme) | [bytedance/trae-agent](https://github.com/bytedance/trae-agent) | OSS 版 Trae Agent，用于与其他 Trae 产品区分 |

#### 登录方式

OAuth 登录请使用对应 CLI 的登录命令。API 登录请按 `.env.template` 写 `.env`，然后在当前 shell 执行 `set -a; source .env; set +a`（修改 `.env` 后也需要重新执行一次）。
对于支持 OpenAI 兼容 API 模式的 coding agent，也支持以下共享回退变量：
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_DEFAULT_MODEL`
当该 agent 的专属 API/base/model 变量未设置时，cakit 会把这三个共享变量映射到该 agent 对应变量。
这些 agent 的模型优先级为：`--model` > agent 专属模型环境变量 > `OPENAI_DEFAULT_MODEL`。

- claude：运行 `claude`，在交互界面输入 `/login`，也支持 `ANTHROPIC_AUTH_TOKEN` 环境变量
- codex：`codex login`
- codebuddy：OAuth 方式使用 `codebuddy login`；API 方式为 `CODEBUDDY_API_KEY`（按需补充 `CODEBUDDY_BASE_URL` / `CODEBUDDY_MODEL` / `CODEBUDDY_INTERNET_ENVIRONMENT`）
- aider：仅 API，使用 `AIDER_OPENAI_API_KEY` + `AIDER_MODEL`
- cursor：`cursor-agent login`
- copilot：运行 `copilot`，输入 `/login`；也支持 `GH_TOKEN`/`GITHUB_TOKEN`
- gemini：运行 `gemini`，按提示选择 Login with Google
- crush：OAuth 方式使用 `crush login`（例如 `crush login hyper`）；API 方式为 `CRUSH_OPENAI_API_KEY` + `CRUSH_OPENAI_BASE_URL` + `CAKIT_CRUSH_MODEL`
- opencode：OAuth 方式使用 `opencode auth login`；API 方式为 `CAKIT_OPENCODE_OPENAI_API_KEY` + `CAKIT_OPENCODE_MODEL`（可选 `CAKIT_OPENCODE_OPENAI_BASE_URL`；若模型名不含 provider，请额外设置 `CAKIT_OPENCODE_PROVIDER`；对于自定义 API 模型可通过 `CAKIT_OPENCODE_MODEL_CAPABILITIES=image,video` 声明输入多模态能力；provider 列表可用 `opencode models` 查看）
- factory：OAuth 方式为运行 `droid` 后 `/login`；API 方式为 `FACTORY_API_KEY`。支持 BYOK 自定义模型：`CAKIT_FACTORY_BYOK_API_KEY` + `CAKIT_FACTORY_BYOK_BASE_URL` + `CAKIT_FACTORY_MODEL`（可选 `CAKIT_FACTORY_BYOK_PROVIDER`，也支持 `OPENAI_*` 回退）
- auggie：OAuth 方式使用 `auggie login`；API 方式为 `AUGMENT_API_TOKEN` + `AUGMENT_API_URL`（可选 `AUGMENT_SESSION_AUTH`）
- continue：OAuth 方式使用 `cn login`；API 方式为 `CAKIT_CONTINUE_OPENAI_API_KEY` + `CAKIT_CONTINUE_OPENAI_MODEL` + `cakit configure continue`
- goose：API 方式为 `CAKIT_GOOSE_PROVIDER` + `CAKIT_GOOSE_MODEL` + `CAKIT_GOOSE_OPENAI_API_KEY`（OpenAI 兼容端点可再配 `CAKIT_GOOSE_OPENAI_BASE_URL`）
- kilocode：API 方式为 `KILO_OPENAI_API_KEY` + `KILO_OPENAI_MODEL_ID` + `cakit configure kilocode`
- openclaw：API 方式为 `CAKIT_OPENCLAW_API_KEY` + `CAKIT_OPENCLAW_BASE_URL` + `CAKIT_OPENCLAW_MODEL` + `cakit configure openclaw`
- deepagents：仅 API，使用 `DEEPAGENTS_OPENAI_API_KEY` + `DEEPAGENTS_OPENAI_MODEL`
- kimi：OAuth 方式为运行 `kimi` 后输入 `/login`；API 方式为设置 `KIMI_API_KEY` 并执行 `cakit configure kimi`
- trae-cn：OAuth 方式为运行 `traecli` 后输入 `/login`；API 方式为设置 `CAKIT_TRAE_CN_API_KEY` 并执行 `cakit configure trae-cn`
- qwen：运行 `qwen`，按提示完成浏览器登录
- qoder：OAuth 方式使用 `qodercli /login`；Qoder token 方式使用 `QODER_PERSONAL_ACCESS_TOKEN`（不支持自定义 OpenAI 兼容 API 鉴权）
- openhands：仅 API（`LLM_API_KEY` + `LLM_MODEL`，或 `OPENAI_API_KEY` + `OPENAI_DEFAULT_MODEL` 回退；见 `.env.template`）
- swe-agent：仅 API（见 `.env.template`）
- trae-oss：仅 API（见 `.env.template`）

### 生成 .env 模板

```bash
cakit env --output .env [--lang en|zh]
```

用于生成环境变量模板文件，便于配置 API Key 与端点。
`--lang en` 使用 `.env.template`；`--lang zh` 使用 `.env.template.zh`。

### 配置 agent

```bash
cakit configure [<agent|all|*>]
```

用于根据当前环境变量重新生成 agent 配置。
省略 `<agent>` 时，默认等同于 `all`。
如更新了环境变量，请先重新执行 `set -a; source .env; set +a`，再执行 `cakit configure [<agent|all|*>]`。
若某个 agent 不需要配置文件，`cakit configure` 可能返回 `"config_path": null` 但仍表示成功。
注：Claude Code 直接读取环境变量，`cakit configure claude` 是空操作（不会写入配置文件）。

### 运行并输出 JSON 统计

```bash
cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video] [--model <base_llm_model>] [--reasoning-effort <value>] [--env-file /path/to/extra.env]
# 多图：重复传 --image 或用逗号分隔多个路径
```

若未安装对应 agent，会自动执行 `cakit install <agent>`（user scope）并提示。
`--model` 会覆盖当前 run 的基础模型（通过各 agent 的模型环境变量和/或模型命令行参数）。
对 OpenAI 兼容 API agent，模型优先级为：`--model` > agent 专属模型环境变量 > `OPENAI_DEFAULT_MODEL`。
具体到每个 agent 的覆盖方式见 `docs/model_override.zh.md`。
`--reasoning-effort` 是统一的按次运行推理强度/思考开关参数。
各 agent 的可选值与映射见 `docs/reasoning_effort.zh.md`。
退出码说明见 `docs/exit_codes.zh.md`。
环境传递说明：
- cakit 只会把它“受管控”的环境变量传给 coding agent（即 `.env.template` 里的变量以及 cakit 显式设置的值）。
- 当前 shell 的其他环境变量不会被继承给 coding agent 进程。
- 如需额外变量，请写入文件并使用 `--env-file` 传入。
输出字段包括：
- `agent`, `agent_version`
- `runtime_seconds`
- `response`（Coding agent 的最终回复消息）
- `models_usage`（按模型拆分，包含 `prompt_tokens`/`completion_tokens`/`total_tokens`，若可用）
- `total_cost`（若 agent 提供）
- `llm_calls`
- `tool_calls`（若 agent 提供）
- `telemetry_log`（若启用）
- `cakit_exit_code`（cakit 严格结果码）
- `command_exit_code`（coding agent CLI 进程原始退出码）
- `output_path`（写入的 `.log` 文件路径，内容为 coding agent CLI 的原始输出）
- `raw_output`（本次运行捕获到的 coding agent CLI 原始输出）
- `trajectory_path`（本次运行的格式化、人类可读轨迹文件路径，不做截断）

严格成功语义：
- 若命令本身执行成功，但关键统计字段缺失/无效（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`、`trajectory_path`），`cakit run` 仍返回非零退出码。

遥测支持：
- Claude Code / Codex：通过 OpenTelemetry（OTEL）导出（需配置 OTEL endpoint），日志地址为 OTEL endpoint
- Copilot CLI：默认日志目录 `~/.copilot/logs/`（cakit 会传 `--log-dir`）
- Gemini CLI：本地日志 `~/.gemini/telemetry.log`
- Crush：本地日志 `<run_data_dir>/logs/crush.log`（来自 run 级 `--data-dir`）
- Auggie CLI：run 临时目录日志 `<tmp_run_dir>/auggie.log`（cakit 传 `--log-file`）
- Qwen Code：本地日志 `~/.qwen/telemetry.log`
- Qoder CLI：本地日志 `~/.qoder/logs/qodercli.log`

图像/视频输入支持：

| Agent | 图像输入 | 视频输入 | 说明 |
| --- | --- | --- | --- |
| claude | ✓ | ✗ | `--image` + `Read` 工具 |
| codex | ✓ | ✗ | `--image`（支持多图） |
| codebuddy | ✓ | ✗ | `--image` 映射到 headless `stream-json` 图片块（`type: image` + base64）；无已文档化 `--video` 输入 |
| aider | ✓ | ✗ | `--image` 映射为 Aider 位置参数图片文件（`aider <image-file> ...`）；能力依赖模型/提供方 |
| cursor | ✗ | ✗ |  |
| copilot | ✓ | ✗ | `--image` 通过自然语言路径注入实现 |
| gemini | ✓ | ✓ | 通过符号化本地路径注入（`@{path}`）；已用 `--model gemini-2.5-pro` 验证（能力依赖模型） |
| crush | ✗ | ✗ | `crush run` 无 `--image` / `--video` 参数 |
| opencode | ✓ | ✗ | 原生 `--file` 映射可用于 `--image`；本地 `--video` 当前会被上游 Read 逻辑按二进制拒绝（opencode 1.2.6） |
| factory | ✓ | ✗ | `--image` 通过自然语言本地路径注入 + `Read` 工具；无已文档化通用 `--video` 参数 |
| auggie | ✓ | ✗ | 原生 `--image`；未文档化 `--video` 参数 |
| continue | ✗ | ✗ | `cn` 的 headless 模式无已文档化 `--image` / `--video` 参数 |
| goose | ✓ | ✓ | 通过自然语言本地路径注入 + 内置 `developer` 处理器实现 |
| kilocode | ✓ | ✗ | 原生 `--attach`；无已文档化 `--video` 参数 |
| openclaw | ✗ | ✗ | `openclaw agent` 无已文档化 `--image` / `--video` 参数 |
| deepagents | ✗ | ✗ | `deepagents` 非交互 CLI 无已文档化 `--image` / `--video` 参数 |
| kimi | ✓ | ✓ | `ReadMediaFile` + 模型能力（`image_in`/`video_in`） |
| trae-cn | ✗ | ✗ | `traecli` 无 `--image` / `--video` 参数 |
| qwen | ✓ | ✓ | `@{path}` 注入；是否有效取决于模型能力 |
| qoder | ✓ | ✗ | `--image` 通过原生 `--attachment` 映射；cakit 中无 `--video` 支持 |
| openhands | ✗ | ✗ | headless CLI 未提供已文档化的 `--image` / `--video` 参数 |
| swe-agent | ✗ | ✗ | 上游多模态仅支持 `swe_bench_multimodal` 的 issue 图片 URL；`sweagent run` 无通用 `--image` / `--video` 参数 |
| trae-oss | ✗ | ✗ | `trae-cli run` 无 `--image` / `--video` 参数 |

Kimi Agent Swarm：
- Kimi 支持在一次 run 中启动多个 subagents。
- 在 prompt 中使用类似 `launch multiple subagents` 的表述即可（例如：“Can you launch multiple subagents to solve this task and summarize the results?”）。
- 对 Kimi 而言，在 session 日志可用时，`models_usage`/`llm_calls`/`tool_calls` 会聚合 subagent 事件。
注意：经测试，Kimi CLI 在并发多会话时可能出现竞态导致失败，建议避免同时运行多个 Kimi 会话。

### Skills（技能）

Skills 是可复用的 coding agent 技能包/指令集（见 [agentskills.io](https://agentskills.io)）。安装某个 skill 仓库请使用：

```bash
npx skills add <skills> -g [-a <agent1> <agent2> ...]
```

建议使用 `-g`/`--global` 以便跨项目复用。示例：

```bash
npx skills add vercel-labs/agent-skills -g -a claude-code codex
```

注意：`skills` 使用的“coding agent”命名可能与 `cakit` 的 agent 命名不一致（例如 `claude-code` vs `cakit` 的 `claude`）。如有问题可运行 `npx skills -h` 查看帮助。

`npx skills` 文档： [skills.sh](https://skills.sh/) 和 [vercel-labs/skills](https://github.com/vercel-labs/skills)。

在脚本/CI 中建议显式指定参数并加 `-y` 以避免交互，例如：

```bash
npx skills add --skill <skills> -g --agent '*' -y
```

`cakit` 也提供一个透传封装：`cakit skills ...`（等价于执行 `npx skills ...`）。

### 安装 Fast Shell Power Tools（推荐）

```bash
cakit tools
```

安装以下常用工具（仅 Linux）：`rg`, `fd`, `fzf`, `jq`, `yq`, `ast-grep`, `bat`, `git`, `git-delta`, `gh`，以及 Playwright Chromium（含运行依赖）。

## 环境变量

详见 `.env.template`，其中有完整且最新的环境变量说明。

## 测试覆盖矩阵

本项目尚未完成全面测试。✓ 表示已测试，✗ 表示不支持，留空表示未测试。

| Agent | OAuth | API | 图像输入 | 视频输入 | MCP | Skills | 遥测 | 联网 | 测试版本 |
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

## 待办（Todo）

- [ ] `cakit run` 增加参数：禁用联网搜索 / 完全禁用联网
- [ ] 支持开关联网
- [ ] `cakit run` 支持 `--timeout`，并在超时时返回半成品运行产物
- [x] 支持 skills
- [ ] 支持 `AGENTS.md`
- [ ] 对所有 agent，每次 `cakit run` 都在 `/tmp` 创建独立的本次运行 `HOME` 并写入该次运行专用配置，避免跨 run 会话冲突并保证统计匹配到本次运行产物；`cakit` 不再需要 `configure` 命令（默认由 `run` 自动配置并完全托管）
- [ ] 新增构建 Docker 镜像指令：构建包含 cakit 的镜像，并可指定 base image
- [ ] 调整所有 agent 配置/数据路径（如 `KIMI_SHARE_DIR`），避免与主机其他 agent 冲突
- [ ] 支持 MCP
- [ ] 支持 balanced 模式
- [x] 支持安装指定版本
- [x] 校验 Kimi token 统计口径（含 subagent 聚合）

说明：目前仅支持 Linux amd64。
