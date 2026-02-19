# Continue Agent（cakit）

本文说明 cakit 如何运行 Continue CLI（`cn`）并提取运行统计字段。

**版本安装**
- `cakit install continue` 安装 `@continuedev/cli`。
- `cakit install continue --version <npm_version_or_tag>` 安装 `@continuedev/cli@<version>`。

## API 配置（`cakit configure continue`）

当必需环境变量存在时，cakit 会写入 `~/.continue/config.yaml`（OpenAI 兼容 API 模式）。

| 环境变量 | 用途 | 是否必填 |
| --- | --- | --- |
| `CAKIT_CONTINUE_OPENAI_API_KEY` | Continue 模型配置使用的 API Key（回退：`OPENAI_API_KEY`） | 必填 |
| `CAKIT_CONTINUE_OPENAI_MODEL` | 基础聊天模型名（回退：`OPENAI_DEFAULT_MODEL`） | 必填 |
| `CAKIT_CONTINUE_OPENAI_BASE_URL` | OpenAI 兼容 base URL（回退：`OPENAI_BASE_URL`） | 可选 |

Continue 的模型/鉴权解析优先级为：
- 本次运行的 `--model` 覆盖优先
- 然后读取 `CAKIT_CONTINUE_OPENAI_*`
- 再回退到共享 `OPENAI_DEFAULT_MODEL`（模型）/`OPENAI_API_KEY`/`OPENAI_BASE_URL`

若缺少必填值，`cakit configure continue` 返回 `config_path: null`，且不会写入文件。

## 运行行为

`cakit run continue "<prompt>"` 以 headless 方式调用 Continue CLI：
- 命令：`cn -p --auto --config <runtime_config> <prompt>`
- 每次运行都会使用独立的 `CONTINUE_GLOBAL_DIR`：`/tmp/cakit-continue-<uuid>/`
- cakit 会按本次解析到的模型/API 变量生成 run-local `config.yaml`

## 图像/视频输入

- `cakit run continue --image ...` / `--video ...` 不支持。
- Continue CLI 的 headless 模式没有已文档化的通用 `--image` / `--video` 参数。
- Prompt-path 多模态检查结果：
  - 仅在 prompt 文本里写图片路径：Continue 会明确表示无法直接读取图片二进制内容。
  - 仅在 prompt 文本里写视频路径：Continue 可通过工具读取文件元数据（如 `ffprobe`/shell），但这不属于正式 `--video` 多模态支持。

## 统计字段提取

`cakit run continue` 对 `response`、`models_usage`、`llm_calls`、`tool_calls` 采用严格解析：

1. 先从以下文件读取会话 ID：
   - `<CONTINUE_GLOBAL_DIR>/sessions/sessions.json`（取最后一项的 `sessionId`）
2. 再精确读取对应会话文件：
   - `<CONTINUE_GLOBAL_DIR>/sessions/<session_id>.json`
3. 解析 `history[].message`：
   - `models_usage`：聚合 `usage.model` + `usage.prompt_tokens` / `usage.completion_tokens` / `usage.total_tokens`
   - `llm_calls`：含有效 `usage` 的 assistant 消息数量
   - `tool_calls`：assistant `message.toolCalls` 总数
4. `response` 优先取 stdout；若为空再回退到 session history 里最后一条 assistant 消息内容。

模型名只从运行产物读取（`history[].message.usage.model`），不会用配置或环境变量回填。

## 遥测与轨迹

- `telemetry_log`：`<CONTINUE_GLOBAL_DIR>/logs/cn.log`
- `trajectory_path`：基于 `raw_output` 转换的 YAML 格式化人类可读轨迹（不截断）
