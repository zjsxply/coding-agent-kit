# Codex Agent（cakit）

本文说明 cakit 如何收集 Codex CLI 的统计与元信息。

**安装版本**
- `cakit install codex --version <npm_version_or_tag>` 会安装 `@openai/codex@<version>`。

**数据来源**
- `codex exec --json` 的 stdout（JSONL 事件）。
- `codex exec --output-last-message <path>` 输出的响应文件（写入 `CAKIT_OUTPUT_DIR`，默认 `~/.cache/cakit`）。
- 会话 rollout JSONL：`$CODEX_HOME/sessions/**/rollout-*.jsonl`。
  cakit 会根据 `session_meta.payload.id` 与 `session_meta.payload.source.subagent.thread_spawn.parent_thread_id`
  构建精确 thread family，因此会把运行时 spawn 的子 agent 一起计入同一次 run 的统计。
- 环境变量，例如 `CODEX_MODEL`、`CODEX_API_BASE`、`CAKIT_CODEX_USE_OAUTH`、`CODEX_OTEL_ENDPOINT`、`OTEL_EXPORTER_OTLP_ENDPOINT`。
- 当 agent 专属变量未设置时，支持共享回退：
  - `OPENAI_API_KEY` -> `CODEX_API_KEY`
  - `OPENAI_BASE_URL` -> `CODEX_API_BASE`
  - `OPENAI_DEFAULT_MODEL` -> `CODEX_MODEL`

**图像输入**
- `cakit run codex --image <path>`：直接传给 Codex CLI 的 `--image` 参数（支持多图）。

**视频输入**
- Codex CLI 文档未描述视频输入；按不支持处理。

**字段映射**
- `agent_version`：来自 `codex --version`。
- `runtime_seconds`：`codex exec` 进程的墙钟耗时。
- `response`：`--output-last-message` 输出文件的内容。
- `models_usage`：
  - 主来源是主线程及其子线程的精确 rollout family。
  - 对每个线程，读取最后一个非空 `token_count` 事件里的 `event_msg.payload.info.total_token_usage`。
  - 每个快照必须字段：`input_tokens`、`cached_input_tokens`、`output_tokens`。
  - `prompt_tokens = input_tokens + cached_input_tokens`，`completion_tokens = output_tokens`。
  - 模型名来自 rollout 的 `turn_context.payload.model`。
  - 若 rollout family 无法解析，再回退到 CLI stdout 的 `turn.completed.usage` 聚合。
- `tool_calls`：
  - 主来源：精确 thread family 中 `response_item.payload.type == "function_call"` 的条数。
  - 回退：CLI JSON 事件中的 `response_item` function call；再不行才回退到旧的 tool item id 计数。
- `llm_calls`：
  - 主来源：对每个线程按累计 `token_count` 快照变化次数计数，再在精确 thread family 上求和。
  - 这样既能覆盖 spawn 的子 agent，也能避免同一累计值重复发出的 `token_count` 被重复计数。
  - 回退：CLI stdout 中 `turn.completed` 的数量。
- `telemetry_log`：若设置了 `CODEX_OTEL_ENDPOINT` 或 `OTEL_EXPORTER_OTLP_ENDPOINT`，则返回该 endpoint。
- `output_path`/`raw_output`：本次运行捕获的 stdout/stderr。
- `trajectory_path`：
  - 若 rollout family 可用，cakit 会写入 family-aware 的 YAML 轨迹，包含 CLI stdout 以及主线程和 spawn 子线程的全部精确 rollout 文件
  - 否则回退到格式化后的 stdout/stderr 轨迹

**备注**
- 若设置了 `CAKIT_CODEX_USE_OAUTH`，cakit 会要求 `${CODEX_HOME}/auth.json`（由 `codex login` 生成）。
- 若使用 API Key 模式，请设置 `CODEX_API_KEY`，并在需要时设置 `CODEX_API_BASE`。
- 若 `cakit configure codex` 之后还需要补充 cakit 尚未覆盖的自定义 Codex 设置，可设置 `CAKIT_CONFIGURE_POST_COMMAND`；该 hook 会收到 `CAKIT_CONFIG_PATH` 以便后处理 `config.toml`。
- 如果想通过这个 hook 在简单 fresh-config 场景里顺手打开 upstream multi-agent，可这样做：

```bash
export CAKIT_CONFIGURE_POST_COMMAND='if [ "$CAKIT_CONFIGURE_AGENT" = "codex" ]; then printf "\n[features]\nmulti_agent = true\n" >> "$CAKIT_CONFIG_PATH"; fi'
cakit install codex
```

- 如果你的配置里已经存在 `[features]`，应直接编辑已有段落，而不是再追加第二个同名 section。
- `cakit run codex` 当前会调用 `codex exec --dangerously-bypass-approvals-and-sandbox`，所以写入 `config.toml` 的沙箱配置键（例如 `[sandbox_workspace_write].network_access = false`）不会在 `cakit run codex` 中被强制执行；但 `web_search = "disabled"` 这类顶层配置仍然会生效。
- 模型优先级为：`--model` > `CODEX_MODEL` > `OPENAI_DEFAULT_MODEL`。
- 为避免意外的鉴权路径选择：当启用 OAuth 时，cakit 会从 Codex CLI 子进程环境中移除 `OPENAI_API_KEY` 与 `CODEX_API_KEY`。
- 若请求 API Key 模式但未设置 `CODEX_API_KEY`，cakit 会避免向 Codex 传递 `OPENAI_API_KEY`/`CODEX_API_KEY`（这样在已登录 OAuth 的情况下仍可工作）。
- 目前尚未测试仅支持 Chat Completions 且不支持 Responses 的 API Base。
