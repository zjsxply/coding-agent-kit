# TRAE CLI（trae.cn）

本文说明 `cakit` 如何运行来自 `trae.cn` 的官方 `traecli`，以及统计字段提取方式。

## 鉴权

- 支持 OAuth 或 API。
- API 模式环境变量：
  - `CAKIT_TRAE_CN_API_KEY`（回退：`OPENAI_API_KEY`）
  - `CAKIT_TRAE_CN_BASE_URL`（回退：`OPENAI_BASE_URL`）
  - `CAKIT_TRAE_CN_MODEL`（回退：`OPENAI_DEFAULT_MODEL`）
  - 可选：`CAKIT_TRAE_CN_MODEL_NAME`（默认 `cakit-openai`）
  - 可选：`CAKIT_TRAE_CN_BY_AZURE`（`1/true` 时按 Azure 兼容请求）

## 安装

- `cakit install trae-cn`：
  - 从 `trae-cli_latest_version.txt` 获取最新版本
  - 从 `lf-cdn.trae.com.cn` 下载 `trae-cli_<version>_<os>_<arch>.tar.gz`
  - 安装到 `~/.local/share/cakit/trae-cn/<version>/trae-cli`
  - 创建软链 `~/.local/bin/traecli`
- `cakit install trae-cn --version <value>` 安装指定版本。

## 配置与运行

- cakit 将配置写入：
  - `~/.config/cakit/trae-cn/trae_cli/trae_cli.yaml`
- 运行时使用隔离配置根目录：
  - `XDG_CONFIG_HOME=~/.config/cakit/trae-cn`
- `cakit run trae-cn` 调用：
  - `traecli --print --json --yolo <prompt>`
- 模型优先级为：`--model` > `CAKIT_TRAE_CN_MODEL` > `OPENAI_DEFAULT_MODEL`。

## 统计提取

- 严格来源：`--print --json` 输出的 JSON。
- `models_usage`：
  - 仅读取顶层 `token_usage.prompt_tokens`、`token_usage.completion_tokens`、`token_usage.total_tokens`
- `llm_calls`：
  - `agent_states[*].messages[*]` 中 `role=assistant` 的消息数
- `tool_calls`：
  - `agent_states[*].messages[*].tool_calls` 长度求和
- 模型名：
  - 顶层 `model`
- `response`：
  - 优先 `agent_states[*].messages[*]` 中最后一个非空 assistant `content`
  - 回退 stdout 最后一行非空文本

## 退出码策略

- cakit 对成功命令做严格校验：
  - `models_usage` 非空
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - `response` 非空
  - `trajectory_path` 非空
- 关键字段缺失会返回非 0 `exit_code`。

## 多模态

- `traecli` 无通用 `--image` / `--video` 参数。
