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
cakit install <agent> [--scope user|global] [--version <value>]
```

默认 `--scope user` 会把 npm 类 agent 安装到 `~/.npm-global`（无需 sudo），请确保 `~/.npm-global/bin` 在 `PATH` 中。
如需全局安装，使用 `--scope global`（等价于 `npm install -g`，可能需要 sudo）。
可使用 `--version` 指定安装版本/引用：
- `codex` / `claude` / `copilot` / `gemini` / `qwen`：npm 版本号或 tag（例如 `0.98.0`）。
- `cursor`：Cursor 构建号（例如 `2026.01.28-fd13201`）。
- `kimi`：`kimi-cli` 包版本（例如 `1.9.0`）。
- `openhands`：`openhands` 包版本（例如 `1.12.1`）。
- `swe-agent`：上游 release tag（例如 `v1.0.0`）。
- `trae-oss`：git 引用（tag / branch / commit）。

#### 支持的 Agent

| 名称 | 官网 | 文档 | 开源仓库 | 备注 |
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
| trae-oss | [Trae Agent](https://github.com/bytedance/trae-agent) | [README](https://github.com/bytedance/trae-agent#readme) | [bytedance/trae-agent](https://github.com/bytedance/trae-agent) | OSS 版 Trae Agent，用于与其他 Trae 产品区分 |

#### 登录方式

OAuth 登录请使用对应 CLI 的登录命令。API 登录请按 `.env.template` 写 `.env`，然后在当前 shell 执行 `set -a; source .env; set +a`（修改 `.env` 后也需要重新执行一次）。

- claude：运行 `claude`，在交互界面输入 `/login`，也支持 `ANTHROPIC_AUTH_TOKEN` 环境变量
- codex：`codex login`
- cursor：`cursor-agent login`
- copilot：运行 `copilot`，输入 `/login`；也支持 `GH_TOKEN`/`GITHUB_TOKEN`
- gemini：运行 `gemini`，按提示选择 Login with Google
- kimi：OAuth 方式为运行 `kimi` 后输入 `/login`；API 方式为设置 `KIMI_API_KEY` 并执行 `cakit configure kimi`
- qwen：运行 `qwen`，按提示完成浏览器登录
- openhands：仅 API（`LLM_API_KEY` + `LLM_MODEL`，见 `.env.template`）
- swe-agent：仅 API（见 `.env.template`）
- trae-oss：仅 API（见 `.env.template`）

### 生成 .env 模板

```bash
cakit env --output .env
```

用于生成环境变量模板文件，便于配置 API Key 与端点。

### 配置 agent

```bash
cakit configure <agent>
```

用于根据当前环境变量重新生成 agent 配置。
如更新了环境变量，请先重新执行 `set -a; source .env; set +a`，再执行 `cakit configure <agent>`。
若某个 agent 不需要配置文件，`cakit configure` 可能返回 `"config_path": null` 但仍表示成功。
注：Claude Code 直接读取环境变量，`cakit configure claude` 是空操作（不会写入配置文件）。

### 运行并输出 JSON 统计

```bash
cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video] [--model <base_llm_model>] [--reasoning-effort <value>] [--env-file /path/to/extra.env]
# 多图：重复传 --image 或用逗号分隔多个路径
```

若未安装对应 agent，会自动执行 `cakit install <agent>`（user scope）并提示。
`--model` 会覆盖当前 run 的基础模型（通过各 agent 的模型环境变量和/或模型命令行参数）。
具体到每个 agent 的覆盖方式见 `docs/model_override.zh.md`。
`--reasoning-effort` 是统一的按次运行推理强度/思考开关参数。
各 agent 的可选值与映射见 `docs/reasoning_effort.zh.md`。
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
- `exit_code`
- `output_path`（写入的 `.log` 文件路径，内容为 coding agent CLI 的原始输出）
- `raw_output`（本次运行捕获到的 coding agent CLI 原始输出）
- `trajectory_path`（本次运行的格式化、人类可读轨迹文件路径，不做截断）

遥测支持：
- Claude Code / Codex：通过 OpenTelemetry（OTEL）导出（需配置 OTEL endpoint），日志地址为 OTEL endpoint
- Copilot CLI：默认日志目录 `~/.copilot/logs/`（cakit 会传 `--log-dir`）
- Gemini CLI：本地日志 `~/.gemini/telemetry.log`
- Qwen Code：本地日志 `~/.qwen/telemetry.log`

图像/视频输入支持：

| Agent | 图像输入 | 视频输入 | 说明 |
| --- | --- | --- | --- |
| claude | ✓ | ✗ | `--image` + `Read` 工具 |
| codex | ✓ | ✗ | `--image`（支持多图） |
| cursor | ✗ | ✗ |  |
| copilot | ✓ | ✗ | `--image` 通过自然语言路径注入实现 |
| gemini | ✓ | ✓ | staged 媒体 + `@{path}` 注入 |
| kimi | ✓ | ✓ | `ReadMediaFile` + 模型能力（`image_in`/`video_in`） |
| qwen | ✓ | ✓ | `@{path}` 注入；是否有效取决于模型能力 |
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

安装以下常用工具（仅 Linux）：`rg`, `fd`, `fzf`, `jq`, `yq`, `ast-grep`, `bat`, `git`, `git-delta`, `gh`。

## 环境变量

- 完整列表见 `.env.template`。
- `CAKIT_OUTPUT_DIR`：覆盖日志输出目录。
- `CAKIT_TRAE_TRAJECTORY`：覆盖 Trae trajectory 输出路径。
- `CAKIT_NPM_PREFIX`：覆盖 npm 类 agent 的用户安装前缀（默认 `~/.npm-global`）。
- `CAKIT_CODEX_USE_OAUTH`：若设置（如 `1`），Codex 使用 OAuth 登录而非 API Key。
- `CAKIT_CLAUDE_USE_OAUTH`：若设置（如 `1`）且 Claude 的 API key/token 同时存在时，优先使用 OAuth token。
- `CAKIT_KIMI_PROVIDER_TYPE`：Kimi provider `type`（`kimi`、`openai_legacy`、`openai_responses`）。
- `GOOGLE_API_KEY`：Gemini CLI 使用的上游 Gemini/Vertex API key。
- `CAKIT_QWEN_GOOGLE_API_KEY`：Qwen 专用的 cakit 覆盖变量，用于避免 `GOOGLE_API_KEY` 冲突。

## 测试覆盖矩阵

本项目尚未完成全面测试。✓ 表示已测试，✗ 表示不支持，✗* 表示在 `cakit run` 中所采用的 headless 模式里不支持但交互/GUI 支持，⚠ 表示测试失败或因鉴权/配置/运行时前置条件缺失而阻塞，留空表示未测试。

| Agent | OAuth | API | 图像输入 | 视频输入 | MCP | Skills | 遥测 | 联网 | 测试版本 |
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

## 待办（Todo）

- [ ] `cakit run` 增加参数：禁用联网搜索 / 完全禁用联网
- [ ] 支持开关联网
- [ ] `cakit run` 支持 `--timeout`，并在超时时返回半成品运行产物
- [x] 支持 skills
- [ ] 支持 `AGENTS.md`
- [ ] 调整所有 agent 配置/数据路径（如 `KIMI_SHARE_DIR`），避免与主机其他 agent 冲突
- [ ] 支持 MCP
- [ ] 支持 balanced 模式
- [x] 支持安装指定版本
- [x] 校验 Kimi token 统计口径（含 subagent 聚合）

说明：目前仅支持 Linux amd64。
