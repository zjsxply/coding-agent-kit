# Gemini Agent（cakit）

本文说明 cakit 如何运行 Gemini CLI 并提取运行统计信息。

**安装版本**
- `cakit install gemini --version <npm_version_or_tag>` 会安装 `@google/gemini-cli@<version>`。

**数据来源**
- `gemini -p ... --output-format json --approval-mode yolo` 的 stdout/stderr。
- 本地遥测日志：`~/.gemini/telemetry.log`。
- 运行时环境变量：`GEMINI_API_KEY`、`GOOGLE_API_KEY`、`GOOGLE_GEMINI_BASE_URL`、`GOOGLE_CLOUD_PROJECT`、`GEMINI_MODEL`。

**图像/视频输入**
- 支持 `cakit run gemini --image/--video`。
- cakit 会先把媒体文件复制到 `<run_cwd>/.cakit-media/`。
- cakit 会在 prompt 前注入 staged 的 `@<path>` 引用，让 Gemini CLI 通过内置 `@` 文件注入流程（`read_many_files`）读取这些媒体。
- 复制机制仅在使用 `--image`/`--video` 时生效。
- 若仅在 prompt 中写本地路径（不使用 `--image`/`--video`），cakit 不会复制文件。
- 若仅在 prompt 中写本地路径且路径位于当前 run workspace 之外，Gemini 可能因 workspace 路径限制拒绝读取。

**字段映射**
- `agent_version`：来自 `gemini --version`。
- `runtime_seconds`：`gemini` 进程墙钟耗时。
- `response`：顶层 JSON 字段 `response`。
- `models_usage`：`stats.models[model].tokens.prompt` / `candidates` / `total`。
- `llm_calls`：`stats.models[model].api.totalRequests` 求和。
- `tool_calls`：`stats.tools.totalCalls`。
- `output_path`/`raw_output`：捕获的 Gemini CLI stdout/stderr。
- `trajectory_path`：由原始输出格式化得到的人类可读轨迹文件。

**解析与校验规则**
- cakit 只解析 stdout 中最后一个 JSON 值，并严格使用上述固定字段名。
- `models_usage` 不会从配置或环境变量回填模型名。
- 若 Gemini 命令退出码为 `0` 但关键字段缺失/无效（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`、非空 `trajectory_path`），cakit 会返回非零 `exit_code`。
