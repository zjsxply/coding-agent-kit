# OpenHands Agent（cakit）

本文说明 cakit 如何运行 OpenHands CLI 并提取运行统计信息。

**安装版本**
- `cakit install openhands --version <pip_version>` 会安装 `openhands==<version>`（优先 `uv tool install`，不可用时回退到 `pip install`）。

**数据来源**
- `openhands --headless --json --override-with-envs -t ...` 的 stdout/stderr。
- `~/.openhands/conversations/<conversation_id>/`（或 `OPENHANDS_CONVERSATIONS_DIR`）下的会话产物：
  - `base_state.json`
  - `events/event-*.json`

**鉴权**
- cakit 当前对 OpenHands 采用 API 模式。
- 必需环境变量：
  - `LLM_API_KEY`
  - `LLM_MODEL`
- 可选环境变量：
  - `LLM_BASE_URL`
- cakit 会为 OpenHands 按 LiteLLM 路由规则归一化模型格式：
  - `provider:model` 会改写为 `provider/model`。
  - 裸模型名（例如 `kimi-k2.5`）会改写为 `openai/<model>`。

**图像/视频输入**
- OpenHands headless CLI 未提供已文档化的 `--image` / `--video` 运行参数。
- `cakit run openhands --image/--video` 视为不支持。

**字段映射**
- `agent_version`：来自 `openhands --version`。
- `runtime_seconds`：`openhands` 进程的墙钟耗时。
- `models_usage`：
  - 模型名：`base_state.stats.usage_to_metrics.agent.model_name`。
  - token：`base_state.stats.usage_to_metrics.agent.accumulated_token_usage.prompt_tokens` 与 `completion_tokens`。
  - `total_tokens = prompt_tokens + completion_tokens`。
- `llm_calls`：`len(base_state.stats.usage_to_metrics.agent.token_usages)`。
- `tool_calls`：统计 `events/event-*.json` 中 `tool_name` 非空的 `ActionEvent` 数量。
- `total_cost`：`base_state.stats.usage_to_metrics.agent.accumulated_cost`。
- `response`：
  - 优先：`ObservationEvent` 中最新 `FinishObservation` 的文本内容。
  - 回退：最新 assistant `MessageEvent` 的文本（`llm_message.role == "assistant"`）。
  - 若两者都不可用，则返回 `None`。
  - 理由：OpenHands 成功结束时存在两种合法事件形态。走工具 `finish` 路径时产出 `FinishObservation`，直接回复路径可能只产出 assistant `MessageEvent`。
  - 该顺序是固定且格式感知的，用于覆盖这两种官方结构，不引入字段别名或松散回退解析。
- `output_path`/`raw_output`：运行时捕获的 OpenHands stdout/stderr。
- `trajectory_path`：基于会话产物转换出的 YAML 格式可读轨迹；若会话产物缺失，则回退为原始输出的格式化轨迹。

**退出码规则**
- cakit 会将 OpenHands run 置为失败（`exit_code` 非 0），当出现任一情况：
  - OpenHands 进程本身非 0 退出；
  - 出现 `ConversationErrorEvent` 或 `AgentErrorEvent`；
  - 看似成功但缺失关键字段（`models_usage`、`llm_calls`、`tool_calls`、`response`）。
