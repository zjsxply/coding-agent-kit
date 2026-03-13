# Qwen Agent（cakit）

本文说明 cakit 如何运行 Qwen Code 并提取运行统计信息。

**安装版本**
- `cakit install qwen --version <npm_version_or_tag>` 会安装 `@qwen-code/qwen-code@<version>`。

**数据来源**
- `qwen -p ... --output-format json --approval-mode yolo` 的 stdout/stderr。
- run 唯一本地遥测日志：`~/.qwen/telemetry/cakit-<timestamp>-<ns>-<id>.log`。
- 运行时环境变量映射：
  - `QWEN_OPENAI_API_KEY` -> `OPENAI_API_KEY`（支持从共享 `OPENAI_API_KEY` 回退）
  - `QWEN_OPENAI_BASE_URL` -> `OPENAI_BASE_URL`（支持从共享 `OPENAI_BASE_URL` 回退）
  - `QWEN_OPENAI_MODEL` -> `OPENAI_MODEL` 与 `--model`（支持从共享 `OPENAI_DEFAULT_MODEL` 回退）
  - `CAKIT_QWEN_GOOGLE_API_KEY` -> `GOOGLE_API_KEY`
  - `GOOGLE_SEARCH_ENGINE_ID`、`TAVILY_API_KEY`

**运行行为**
- 当存在 `QWEN_OPENAI_API_KEY` 时，cakit 会传递 `--auth-type openai`。
- 模型优先级为：`--model` > `QWEN_OPENAI_MODEL` > `OPENAI_DEFAULT_MODEL`。
- cakit 会传入 run 唯一的 `--telemetry-outfile` 路径，避免并发运行互相覆盖。
- `cakit configure qwen` 会对 `~/.qwen/settings.json` 做读-改-写合并更新，而不是整文件覆盖。

**图像/视频输入**
- 支持 `cakit run qwen --image/--video`，实现方式为 prompt 注入。
- cakit 会先把媒体文件复制到 `<run_cwd>/.cakit-media/`，再在 prompt 开头注入：`@{.cakit-media/<file>}`。
- 是否能正确理解媒体内容取决于所选基础模型能力；文本模型可能无法给出正确图像/视频描述。
- 上游在 Qwen OAuth / DashScope 兼容配置下，对视觉输入和联网工具的支持通常比通用 OpenAI 兼容 API 模式更稳定。API 模式下，即使 prompt 中已有 `@{path}`，真正的工具执行和媒体理解仍取决于 provider。
- 复制机制仅在使用 `--image`/`--video` 时生效。
- 若仅在 prompt 中写本地路径（不使用 `--image`/`--video`），cakit 不会复制文件；当路径在当前 run workspace 之外时，Qwen 可能因 workspace 路径限制拒绝读取。

**字段映射**
- `agent_version`：来自 `qwen --version`。
- `runtime_seconds`：`qwen` 进程墙钟耗时。
- `response`：来自 `result.result`；若缺失则回退到 JSON 输出中最后一条 assistant 文本块。
- `models_usage`：`result.stats.models[model].tokens.prompt` / `candidates` / `total`。
- `llm_calls`：`result.stats.models[model].api.totalRequests` 求和。
- `tool_calls`：`result.stats.tools.totalCalls`。
- `output_path`/`raw_output`：捕获的 Qwen CLI stdout/stderr。
- `trajectory_path`：由原始输出格式化得到的人类可读轨迹文件。

**解析与校验规则**
- cakit 仅解析 stdout 中最后一个 JSON 值，再从中选取最后一个 `type == "result"` 负载。
- `models_usage` 不会从配置或环境变量回填模型名。
- 若 Qwen 命令退出码为 `0` 但关键字段缺失/无效（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`、非空 `trajectory_path`），cakit 会返回非零 `exit_code`。
