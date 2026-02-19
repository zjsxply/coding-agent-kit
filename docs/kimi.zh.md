# Kimi Agent（cakit）

本文说明 cakit 如何安装和配置 Kimi Code CLI。

## 安装

`cakit install kimi` 默认使用 Kimi 官方安装脚本：

```bash
curl -LsSf https://code.kimi.com/install.sh | bash
```

上游安装脚本会处理运行时依赖初始化（包括在缺少 `uv` 时自动安装）。

如需安装指定 Kimi CLI 版本：

```bash
cakit install kimi --version <kimi_cli_version>
```

传入 `--version` 后，cakit 会安装 `kimi-cli==<version>`（优先使用 `uv tool install --python 3.13`，若本机无 `uv` 则回退到 `pip install`）。

## API 配置（`cakit configure kimi`）

当设置了 `KIMI_API_KEY` 时，cakit 会按 Kimi CLI 的 provider/model 配置格式写入 `~/.kimi/config.toml`。

环境变量映射如下：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `KIMI_API_KEY` | Provider API Key（回退：`OPENAI_API_KEY`） | 必填 |
| `KIMI_BASE_URL` | Provider base URL（回退：`OPENAI_BASE_URL`） | 必填 |
| `KIMI_MODEL_NAME` | 上游模型名（`model`），用于运行时 `--model`（回退：`OPENAI_DEFAULT_MODEL`） | 可选 |
| `CAKIT_KIMI_PROVIDER_TYPE` | Kimi 配置中的 provider `type` | 必填（`kimi`、`openai_legacy`、`openai_responses`） |

若上表中的必填变量有任意缺失，或 `CAKIT_KIMI_PROVIDER_TYPE` 不在允许集合中，`cakit configure kimi` 会返回 `config_path: null`，并且不会写配置文件。

cakit 仅写 provider 配置：
- provider key：`kimi`
- `cakit configure kimi` 不写 `default_model`，也不写 `[models.*]` 区块

参考：
- 环境变量覆盖：https://moonshotai.github.io/kimi-cli/zh/configuration/overrides.html#%E7%8E%AF%E5%A2%83%E5%8F%98%E9%87%8F%E8%A6%86%E7%9B%96

## 图像输入

`cakit run kimi --image <path>` 已支持。

- cakit 使用 print mode 的 `--prompt` 输入，并在 prompt 中注入图片绝对路径，供 Kimi 读取文件。
- cakit 会在提示中要求 Kimi 使用 `ReadMediaFile` 打开图片路径后再回答。
- 是否能真正读图仍取决于所选模型能力。若模型不支持图像输入，Kimi 可能失败或直接返回不支持读图。

## 视频输入

`cakit run kimi --video <path>` 已支持。

- 有视频场景：cakit 使用 print mode 的 `--prompt` 输入，并在 prompt 中注入视频绝对路径。
- cakit 会在提示中要求 Kimi 使用 `ReadMediaFile` 打开视频路径后再回答。
- 是否能真正读视频仍取决于所选模型能力。若模型不支持视频输入，Kimi 可能失败或直接返回不支持读视频。

## Agent Swarm

Kimi 支持 Agent Swarm 风格流程，可直接通过 prompt 触发，例如：

- `Can you launch multiple subagents to solve this and summarize the results?`

## 运行时模型与更新行为

- cakit 会把解析后的模型同时通过两种方式传给 Kimi CLI：
  - 命令行参数：`kimi ... --model <resolved_model>`
  - 环境变量：`KIMI_MODEL_NAME=<resolved_model>`
- `cakit run kimi --model <name>` 在该次运行中优先。
- 若未传 `--model`，cakit 会先读取 `KIMI_MODEL_NAME`，再回退到 `OPENAI_DEFAULT_MODEL`。
- cakit 在运行 Kimi 时始终设置 `KIMI_CLI_NO_AUTO_UPDATE=1`。

## SearchWeb 与 FetchURL 行为

根据 Kimi CLI 对 provider 的说明：

- 原生 Kimi provider 模式（`type = "kimi"`）：`SearchWeb` 和 `FetchURL` 都由 Kimi 服务支持。
- 第三方 OpenAI 兼容模式（`type = "openai_legacy"` 或 `type = "openai_responses"`）：`SearchWeb` 不支持；`FetchURL` 仍可用（本地 URL 抓取）。

参考：
- 搜索与抓取服务行为：https://moonshotai.github.io/kimi-cli/zh/configuration/providers.html#%E6%90%9C%E7%B4%A2%E5%92%8C%E6%8A%93%E5%8F%96%E6%9C%8D%E5%8A%A1

## 统计字段提取

`cakit run kimi` 对 `response`、`models_usage`、`llm_calls`、`tool_calls` 采用严格解析，顺序如下：

1. cakit 每次运行生成 UUID 并通过 `--session` 传入，然后按 `work_dir` + Kimi metadata 计算出的精确路径读取 `wire.jsonl`：
   - `~/.kimi/sessions/<kaos_or_md5>/<session_id>/wire.jsonl`
2. 从 session 的 `wire.jsonl` 中读取：
   - `StatusUpdate.payload.token_usage` -> token usage（`models_usage`）
   - `SubagentEvent.event.type == "StatusUpdate"` 的 token usage 也会聚合进总量
   - `StatusUpdate` + subagent `StatusUpdate` 条数 -> `llm_calls`
   - `ToolCall` + subagent `ToolCall` 条数 -> `tool_calls`
   - 如存在 `payload.model` 则读取模型名
3. 若 session 数据仍不完整，再解析 stdout `stream-json`，且只读取明确字段（仅用于 usage/response）。
4. 若 session wire 里有 usage 但没有模型字段，再按精确 `session_id` 在 `~/.kimi/logs/kimi.log` 中定位 `Created new session:` / `Switching to session:` / `Session ... not found` 区段，并读取同区段的 `Using LLM model: ... model='...'`。
5. 不写模型名占位值；若运行产物中提取不到模型名，`models_usage` 保持为空对象。

模型名仅从本次运行产物提取（session wire / session 日志），不从配置或输入参数回填。
`prompt_tokens` 由 Kimi 的输入 usage 字段（`input_other`、`input_cache_read`、`input_cache_creation`）汇总得到，并对单项负值做截断以避免负增量。
若上游这些字段返回 `0`，则 `prompt_tokens` 可能为 `0`。

若提取异常，优先排查 `output_path` / `raw_output` 以及 Kimi 的 session/log 文件。
`trajectory_path` 指向格式化的人类可读轨迹文件：由 `output_path` / `raw_output` 转为结构化 YAML 格式输出（Unicode 不转义，多行文本用 `|` 块，不做截断）。

## 推理强度参数映射

在 `cakit run kimi ... --reasoning-effort <value>` 中：

- `thinking` -> 追加 `--thinking`
- `none` -> 追加 `--no-thinking`
