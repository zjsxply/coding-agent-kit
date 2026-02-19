# Auggie Agent（cakit）

本文说明 cakit 如何运行 Auggie 并提取运行统计信息。

**安装版本**
- `cakit install auggie --version <npm_version_or_tag>` 会安装 `@augmentcode/auggie@<version>`。

**鉴权**
- OAuth：运行 `auggie login`。
- cakit 的 API 模式变量：
  - `AUGMENT_API_TOKEN`
  - `AUGMENT_API_URL`
  - 可选：`AUGMENT_SESSION_AUTH`

**运行行为**
- cakit 执行命令：
  - `auggie --print --quiet --output-format json --workspace-root <run_cwd> --instruction <prompt> --log-file <tmp_log> --log-level debug`
- 为了稳定性，cakit 运行时设置 `AUGMENT_DISABLE_AUTO_UPDATE=1`。
- 图像输入走 Auggie 原生参数（`--image <path>`）。

**模型选择**
- `cakit run auggie --model <name>` 优先级最高。
- 未传 `--model` 时，cakit 会读取 `CAKIT_AUGGIE_MODEL`，若存在则传递 `--model <name>`。

**图像/视频输入**
- `cakit run auggie --image <path>` 已支持（Auggie 原生参数）。
- `cakit run auggie --video <path>` 不支持。

**字段映射**
- `agent_version`：来自 `auggie --version`。
- `response`：来自结果载荷字段 `result`。
- `models_usage`：来自 `result.stats.models[<model>].tokens`：
  - `prompt_tokens` <- `prompt`
  - `completion_tokens` <- `candidates`
  - `total_tokens` <- `total`
- `llm_calls`：`result.stats.models[<model>].api.totalRequests` 的总和。
- `tool_calls`：`result.stats.tools.totalCalls`。
- `telemetry_log`：通过 `--log-file` 传入的 run 级日志路径。
- `trajectory_path`：由原始 CLI 输出转换得到的 YAML 人类可读轨迹。

**解析与校验规则**
- cakit 仅解析 `type == "result"` 的 JSON 结果载荷，且字段名必须精确匹配上文。
- 若命令成功但缺少关键统计（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`），cakit 会返回非零 `exit_code`。
