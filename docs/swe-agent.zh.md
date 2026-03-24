# SWE-agent（cakit）

本文说明 `cakit` 如何运行 SWE-agent CLI 并提取统计字段。

## 鉴权

- 仅 API 模式。
- 必需环境变量：
  - `SWE_AGENT_API_KEY`（回退：`OPENAI_API_KEY`）
  - `SWE_AGENT_BASE_URL`（回退：`OPENAI_BASE_URL`）
  - `SWE_AGENT_MODEL`（回退：`OPENAI_DEFAULT_MODEL`）

## 安装

- `cakit install swe-agent` 会先解析上游最新 release tag，再用 `uv tool install` 安装该 git ref。
- cakit 会把上游 CLI 安装到 Python 3.12 的 `uv tool` 环境中，并预装 `pip`、`tree-sitter==0.21.3`、`tree-sitter-languages`，以保证上游官方 `edit_anthropic` bundle 在 local deployment 模式下可运行。
- 若本机没有 `uv`，cakit 会回退为对同一 git ref 执行 `pip install`。
- `cakit install swe-agent --version <tag_or_plain_version>` 也走同样流程，只是固定安装指定上游 git tag。像 `1.1.0` 这样的纯 semver 会在内部规范化为上游 `v1.1.0` tag。
- cakit 会额外准备运行资源到 `~/.cache/cakit/swe-agent-assets/<resolved_tag>`（`config/`、`tools/`、`trajectories/`），并传递：
  - `SWE_AGENT_CONFIG_DIR`
  - `SWE_AGENT_TOOLS_DIR`
  - `SWE_AGENT_TRAJECTORY_DIR`

## 运行行为

- cakit 以本地部署模式运行 `sweagent run`：
  - `--env.deployment.type=local`
  - `--env.repo.type=local`
  - `--problem_statement.text <prompt>`
- 如果当前安装的 `sweagent run` 支持 `--output_dir`，cakit 会传入每次运行独立的输出目录，并从其中读取 `.traj` 文件。
- 模型优先级为：`--model` > `SWE_AGENT_MODEL` > `OPENAI_DEFAULT_MODEL`。
- cakit 在生成运行配置时会深拷贝上游官方 `config/default.yaml` 的 agent 默认配置，再注入解析后的模型/API 设置，并把 tool bundle 路径重写到 cakit 管理的运行时资源目录。
- 若 `--cwd` 位于一个干净的 git 仓库中，cakit 会把仓库根目录传给 SWE-agent，而不是只传当前子目录。
- 若 `--cwd` 位于一个 dirty git 仓库中，cakit 会先把仓库 clone 到 `/tmp`，再覆盖当前未提交的工作树改动，生成一个临时 snapshot commit，然后让 SWE-agent 基于这个干净 snapshot 运行。
- 若 `--cwd` 不是 git 仓库，cakit 会在 `/tmp` 创建临时 git 仓库后再运行。
- cakit 会写入 `~/.config/sweagent/config.yaml` 并通过 `--config` 显式使用。
- 若配置了 base URL，cakit 会把它写入 `agent.model.api_base`，并同时通过 `OPENAI_BASE_URL` 传给子进程。

## 统计提取

- 严格来源：当已安装 CLI 支持该参数时，读取 run `--output_dir` 中写出的 `.traj` 文件。
- `models_usage`：
  - `prompt_tokens = info.model_stats.tokens_sent`
  - `completion_tokens = info.model_stats.tokens_received`
  - `total_tokens = prompt + completion`
- `llm_calls`：`info.model_stats.api_calls`
- `tool_calls`：`trajectory` 中非空 `action` 数量（retry 场景汇总 `attempts[*].trajectory`）。
- `response`：
  - 优先取最后一个非 `submit` 轨迹 step 里的最新非空文本，优先级为 `observation`，再到 `response`、`thought`
  - 回退 `info.submission`
  - 再回退 stdout 最后一行非空文本
- `trajectory_path`：将轨迹文件转为 YAML 可读格式；若轨迹不可用则回退格式化原始输出。

## 退出码策略

- cakit 使用严格校验：
  - `models_usage` 非空
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - `response` 非空
  - `trajectory_path` 非空
- 若命令进程成功但关键字段缺失，cakit 返回非 0 `exit_code`。

## 多模态

- `sweagent run` 不支持通用 `--image` / `--video`。
