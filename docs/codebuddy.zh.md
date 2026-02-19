# CodeBuddy Agent（cakit）

本文说明 cakit 如何运行 CodeBuddy Code 并提取运行统计信息。

**安装版本**
- `cakit install codebuddy --version <npm_version_or_tag>` 会安装 `@tencent-ai/codebuddy-code@<version>`。

**鉴权**
- OAuth：运行 `codebuddy login`。
- cakit 的 API 模式变量：
  - `CODEBUDDY_API_KEY`
  - 可选：`CODEBUDDY_AUTH_TOKEN`
  - 可选：`CODEBUDDY_BASE_URL`
  - 可选：`CODEBUDDY_INTERNET_ENVIRONMENT`（中国版用 `internal`，iOA 用 `ioa`）
- 当 agent 专属变量未设置时，支持 OpenAI 兼容共享回退：
  - `OPENAI_API_KEY` -> `CODEBUDDY_API_KEY`
  - `OPENAI_BASE_URL` -> `CODEBUDDY_BASE_URL`
  - `OPENAI_DEFAULT_MODEL` -> `CODEBUDDY_MODEL`

**运行行为**
- cakit 执行命令：
  - `codebuddy -p --output-format stream-json -y "<prompt>"`
- 传 `--image` 时，cakit 执行：
  - `codebuddy -p --input-format stream-json --output-format stream-json -y`
  - 并通过 stdin 传入 `{"type":"image","source":{"type":"base64","media_type":"...","data":"..."}}` 图片块
- 若传 `--model`，cakit 会追加：
  - `--model <name>`

**模型选择**
- `cakit run codebuddy --model <name>` 优先级最高。
- 未传 `--model` 时，cakit 先读取 `CODEBUDDY_MODEL`，再回退到 `OPENAI_DEFAULT_MODEL`。

**图像/视频输入**
- `cakit run codebuddy --image <path>` 通过 headless stream-json 图片块支持（能力依赖模型）。
- `cakit run codebuddy --video <path>` 不支持。
- Prompt 路径检查（不传 `--image`）：实测可用；在 prompt 中写本地图片路径时，CodeBuddy 能读取并描述图片内容。

**字段映射**
- `agent_version`：来自 `codebuddy --version`。
- `response`：当 `result.subtype == "success"` 时取 `result.result`；否则回退为 assistant/error 文本。
- `models_usage`：按 `assistant.message.model` 聚合 `assistant.message.usage`：
  - `prompt_tokens` <- `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
  - `completion_tokens` <- `output_tokens`
  - `total_tokens` <- `prompt_tokens + completion_tokens`
- `llm_calls`：可解析 `assistant` 消息条数。
- `tool_calls`：`assistant.message.content` 中 `tool_use` 块数量。
- `total_cost`：`result.total_cost_usd`。
- `trajectory_path`：由原始 CLI 输出转换得到的 YAML 人类可读轨迹。

**解析与校验规则**
- cakit 仅解析 `stream-json` 中已文档化的消息类型：`system/init`、`assistant`、`result`。
- 对结果子类型做严格判断：若 `result.is_error == true`（例如 `error_during_execution`）且命令退出码为 `0`，cakit 仍判定失败。
- 若命令成功但缺少关键统计（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`），cakit 会返回非零 `exit_code`。
