# SWE-agent（cakit）

本文说明 `cakit` 如何运行 SWE-agent CLI 并提取统计字段。

## 鉴权

- 仅 API 模式。
- 必需环境变量：
  - `SWE_AGENT_API_KEY`
  - `SWE_AGENT_API_BASE`
  - `SWE_AGENT_MODEL`

## 安装

- `cakit install swe-agent --version <tag>` 安装上游 release tarball。
- cakit 会额外准备运行资源到 `~/.cache/cakit/swe-agent-assets/<tag>`（`config/`、`tools/`、`trajectories/`），并传递：
  - `SWE_AGENT_CONFIG_DIR`
  - `SWE_AGENT_TOOLS_DIR`
  - `SWE_AGENT_TRAJECTORY_DIR`

## 运行行为

- cakit 以本地部署模式运行 `sweagent run`：
  - `--env.deployment.type=local`
  - `--env.repo.type=local`
  - `--problem_statement.text <prompt>`
- 若 `--cwd` 不是 git 仓库，cakit 会在 `/tmp` 创建临时 git 仓库后再运行。
- cakit 会写入 `~/.config/sweagent/config.yaml` 并通过 `--config` 显式使用。

## 统计提取

- 严格来源：run 输出目录中最新的 `.traj` 文件。
- `models_usage`：
  - `prompt_tokens = info.model_stats.tokens_sent`
  - `completion_tokens = info.model_stats.tokens_received`
  - `total_tokens = prompt + completion`
- `llm_calls`：`info.model_stats.api_calls`
- `tool_calls`：`trajectory` 中非空 `action` 数量（retry 场景汇总 `attempts[*].trajectory`）。
- `response`：
  - 优先取轨迹 step 里最新非空文本（`response` / `thought` / `observation`）
  - 回退 `info.submission`
  - 再回退 stdout 最后一行非空文本
- `trajectory_path`：将轨迹文件转为 YAML 可读格式；若轨迹不可用则回退格式化原始输出。

## 退出码策略

- cakit 使用严格校验：
  - `models_usage` 非空
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - `response` 非空
- 若命令进程成功但关键字段缺失，cakit 返回非 0 `exit_code`。

## 多模态

- `sweagent run` 不支持通用 `--image` / `--video`。

