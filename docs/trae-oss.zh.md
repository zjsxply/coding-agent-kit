# Trae OSS（cakit）

本文说明 `cakit` 如何运行 `trae-cli` 并提取统计字段。

## 鉴权

- 仅 API 模式。
- 必需环境变量：
  - `TRAE_AGENT_API_KEY`（回退：`OPENAI_API_KEY`）
  - `TRAE_AGENT_BASE_URL`（回退：`OPENAI_BASE_URL`）
  - `TRAE_AGENT_MODEL`（回退：`OPENAI_DEFAULT_MODEL`）

## 安装

- `cakit install trae-oss` 默认安装执行时可获得的上游最新引用。
- `cakit install trae-oss --version <git_ref>` 从 `bytedance/trae-agent` 安装。
- 对已安装版本的回报，cakit 会从 uv 的 `uv-receipt.toml` 读取精确 git revision，并返回这个 git ref，而不是 `trae-cli --version` 打印的 Trae 包版本字符串。
- 为满足上游运行时导入依赖，cakit 会额外安装：
  - `docker`
  - `pexpect`
  - `unidiff`

## 配置与运行

- cakit 写配置到 `~/.config/trae/config.yaml`。
- `cakit run trae-oss` 会调用：
  - `trae-cli run <prompt>`
  - `--working-dir <cwd>`
  - `--trajectory-file <path>`
  - `--config-file ~/.config/trae/config.yaml`（若存在）
  - 配置了模型时追加 `--model <...>`
- 生成配置时的 provider 选择规则：
  - 若设置了 `CAKIT_TRAE_AGENT_PROVIDER`，优先使用它
  - `api.openai.com` 识别为 `openai`
  - `*.openrouter.ai` 识别为 `openrouter`
  - 其他自定义网关默认识别为 `doubao`，保持 Trae 走兼容 chat completions 的路径
- `--trajectory-file` 的路径来源：
  - 设置了 `CAKIT_TRAE_TRAJECTORY` 时使用该值（支持 `~` 展开）
  - 未设置时回退为 run 唯一路径 `/tmp/cakit-trae-<uuid>.json`
- 模型优先级为：`--model` > `TRAE_AGENT_MODEL` > `OPENAI_DEFAULT_MODEL`。
- cakit 会把解析后的共享 OpenAI 兼容 base URL 通过 `OPENAI_BASE_URL` 传给子进程。
- 如果某个自定义网关完整支持 Trae 所需的 OpenAI Responses API 路径，可显式设置 `CAKIT_TRAE_AGENT_PROVIDER=openai`；否则保持默认即可。
- cakit 会在生成的 Trae 配置里写入 `max_retries: 5`，让临时性的上游失败仍然会重试，但不会变成近乎无界的长时间等待。

## 统计提取

- 严格来源：trajectory JSON 文件。
- `models_usage`：
  - 汇总 `llm_interactions[*].response.usage.input_tokens` 为 `prompt_tokens`
  - 汇总 `llm_interactions[*].response.usage.output_tokens` 为 `completion_tokens`
  - `total_tokens = prompt + completion`
- `llm_calls`：`len(llm_interactions)`
- `tool_calls`：`agent_steps[*].tool_calls` 长度求和（step 中缺失则按 0）
- 模型名：trajectory 顶层 `model`
- `response`：
  - 优先 `final_result`
  - 回退为最新非空 `agent_steps[*].llm_response.content`
  - 回退为最新非空 `llm_interactions[*].response.content`
- `trajectory_path`：轨迹文件转 YAML 可读格式；若轨迹不可用则回退格式化原始输出。

## 退出码策略

- 命令成功时，cakit 对以下字段做严格校验：
  - `models_usage` 非空
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - `response` 非空
  - `trajectory_path` 非空
- 关键字段缺失会返回非 0 `exit_code`。

## 多模态

- `trae-cli run` 无通用 `--image` / `--video` 参数。
