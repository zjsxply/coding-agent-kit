# Crush Agent（cakit）

本文说明 cakit 如何运行 Crush 并提取运行统计信息。

**安装版本**
- `cakit install crush --version <npm_version_or_tag>` 会安装 `@charmland/crush@<version>`。

**鉴权**
- OAuth：使用 `crush login`（例如 `crush login hyper` 或 `crush login copilot`）。
- cakit 的 API 模式变量：
  - `CRUSH_OPENAI_API_KEY`
  - `CRUSH_OPENAI_BASE_URL`
  - `CAKIT_CRUSH_MODEL`
- 当上述 API 变量齐全时，`cakit configure crush` 会写入 `~/.config/crush/crush.json`。
- `cakit run crush` 在 API 模式下使用运行时临时配置；OAuth 模式沿用你已有的 Crush 配置/登录状态。

**运行行为**
- cakit 执行命令：
  - `crush --cwd <run_cwd> --data-dir <tmp_dir> run --quiet <prompt>`
- 为了稳定性，cakit 运行时始终设置 `CRUSH_DISABLE_PROVIDER_AUTO_UPDATE=1`。
- `--data-dir` 每次运行都使用 `/tmp` 下的独立目录，便于精确匹配该次会话统计。

**模型选择**
- `cakit run crush --model <name>` 优先级高于 `CAKIT_CRUSH_MODEL`。
- API 模式：cakit 会按该模型生成本次运行的 Crush 临时配置。
- OAuth 模式：当选择模型时，cakit 会同时向 `crush run` 传 `--model <name>` 和 `--small-model <name>`。

**图像/视频输入**
- `cakit run crush --image/--video` 按不支持处理。

**字段映射**
- `agent_version`：来自 `crush --version`。
- `response`：来自 Crush stdout。
- `models_usage`：来自 `<data-dir>/crush.db`：
  - token 统计：`sessions.prompt_tokens`、`sessions.completion_tokens`
  - 模型名：`messages.model` 中非 summary assistant 消息的唯一模型
- `llm_calls`：`messages` 表中非 summary assistant 消息数量。
- `tool_calls`：`messages.parts` JSON 内 `tool_call` 项计数（`json_each` + `$.type == "tool_call"`）。
- `telemetry_log`：`<data-dir>/logs/crush.log`。
- `trajectory_path`：基于运行数据库产物（`session` + `messages`）生成 YAML 人类可读轨迹。

**解析与校验规则**
- cakit 仅解析 Crush 运行产物中的精确字段（`crush.db` 的固定表/列）。
- 会话匹配为精确匹配：运行时 `--data-dir` 中唯一根会话。
- 若命令成功但关键统计缺失/无效（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`），cakit 会返回非零 `exit_code`。
