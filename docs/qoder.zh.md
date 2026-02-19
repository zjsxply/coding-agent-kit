# Qoder Agent（cakit）

本文说明 cakit 如何运行 Qoder CLI 并提取运行统计信息。

**安装版本**
- `cakit install qoder --version <npm_version_or_tag>` 会安装 `@qoder-ai/qodercli@<version>`。

**鉴权**
- OAuth：运行 `qodercli /login`。
- cakit 的 token 鉴权变量：
  - `QODER_PERSONAL_ACCESS_TOKEN`（推荐）
- 上游 `qodercli` 不支持自定义 OpenAI 兼容 API（`api_key`/`base_url`）鉴权。

**运行行为**
- cakit 执行命令：
  - `qodercli -q -p "<prompt>" --output-format stream-json --dangerously-skip-permissions`
- 当你传入 `cakit run qoder --model <name>`，或环境中设置了 `CAKIT_QODER_MODEL`，cakit 会追加 `--model <name>`。
- 图像输入通过 Qoder 原生参数透传：
  - 每个 `--image` 转为一个 `--attachment <image_path>`。

**图像/视频输入**
- `cakit run qoder --image <path>`：支持（Qoder 原生 `--attachment`）。
- `cakit run qoder --video <path>`：cakit 中不支持。

**字段映射**
- `agent_version`：来自 `qodercli --version`。
- `runtime_seconds`：`qodercli` 进程墙钟耗时。
- `telemetry_log`：存在时为 `~/.qoder/logs/qodercli.log`。
- `output_path`/`raw_output`：捕获的 Qoder CLI stdout/stderr。
- `trajectory_path`：由原始输出转换得到的 YAML 人类可读轨迹。

**统计提取**
- cakit 从 stdout 读取 stream JSON，并按严格、格式感知的方式解析：
  - `qoder_message` 结构：
    - `models_usage`：`message.usage.total_prompt_tokens` / `total_completed_tokens` / `total_tokens`。
    - 模型名：`message.response_meta.model_name`。
    - `llm_calls`：`message.response_meta.request_id` 去重计数。
    - `tool_calls`：`message.tool_calls` 长度累计。
    - `response`：最后一条非空 assistant `message.content`。
  - message-stream 结构（`message_start`/`message_stop`）：
    - `models_usage`：`message_start.message.usage.input_tokens` + `cache_read_tokens`，以及 `message_delta.usage.output_tokens`。
    - 模型名：`message_start.message.model`。
    - `llm_calls`：`message_start.message.id` 去重计数。
    - `tool_calls`：统计 `content_block_start` 且 `content_block.type == "tool_use"` 的次数。
    - `response`：累计文本块（`content_block_start` + `content_block_delta`）得到最终回复。

若输出结构与预期 schema 不完全一致，cakit 会返回空统计（`None`/`{}`），并由严格校验使 `cakit run` 以非零退出。
