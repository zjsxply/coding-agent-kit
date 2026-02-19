# KiloCode Agent（cakit）

本文说明 cakit 如何安装并运行 Kilo Code CLI（`kilocode`）。

## 安装

`cakit install kilocode` 会安装 npm 包 `@kilocode/cli`。

- 默认安装（不传 `--version`）会安装执行当下可获得的上游最新版本（latest）。
- 支持指定版本安装：

```bash
cakit install kilocode --version <npm_version_or_tag>
```

## 配置

`cakit configure kilocode` 会写入：

- `~/.kilocode/cli/config.json`

环境变量映射：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `KILO_OPENAI_API_KEY` | OpenAI 兼容 API key（回退：`OPENAI_API_KEY`） | 必填 |
| `KILO_OPENAI_MODEL_ID` | 上游模型 ID（回退：`OPENAI_DEFAULT_MODEL`） | 必填 |
| `KILO_OPENAI_BASE_URL` | OpenAI 兼容 base URL（回退：`OPENAI_BASE_URL`） | 可选 |

若必填的 key/model 缺失，cakit 不会写配置，且运行返回非零。

## 运行行为

cakit 会在运行时检测 KiloCode 主版本，并按版本选择命令：

```bash
# 0.x
kilocode --auto --json --yolo --workspace <cwd> --nosplash [--attach <image>] [--model <name>] "<prompt>"

# 1.x
kilocode run --auto --format json [--file <image>] [--model openai/<name>] "<prompt>"
```

- cakit 每次运行会在 `/tmp` 下创建独立 HOME，并写入该次运行专用配置。
- 这样可避免跨 run 的会话冲突，并保证统计匹配到本次运行产物。
- `cakit run kilocode --model <name>` 在该次运行优先。
- 若未传 `--model`，cakit 会先读取 `KILO_OPENAI_MODEL_ID`，再回退到 `OPENAI_DEFAULT_MODEL`。
- cakit 不支持视频输入（`--video` 会返回不支持）。

## 图像输入

`cakit run kilocode --image <path>` 已支持。

- cakit 使用原生 `--attach <path>` 传入图片。
- 是否能真正读图取决于所选模型能力。

## 统计提取

`cakit run kilocode` 采用严格的版本分流解析：

### KiloCode 0.x

运行产物：

1. `~/.kilocode/cli/global/global-state.json`
2. `~/.kilocode/cli/global/tasks/<task_id>/ui_messages.json`
3. `~/.kilocode/cli/global/tasks/<task_id>/api_conversation_history.json`

严格规则如下：
- `models_usage`：
  - 从 `ui_messages.json` 中 `type="say"` 且 `say="api_req_started"` 的条目读取
  - 解析其 `text` JSON，累计 `tokensIn` + `tokensOut`
  - 模型名仅来自运行产物：
    - 优先 `taskHistory.apiConfigName` -> `listApiConfigMeta[].modelId`
    - 回退 `api_conversation_history.json` 中 `<model>...</model>` 标签
- `llm_calls`：`ui_messages.json` 中 `api_req_started` 条目数量
- `tool_calls`：`api_conversation_history.json` 中 assistant `tool_use` 条目数量
- `response`：
  - 优先 `ui_messages.json` 的 `completion_result`/`text`
  - 其次 `api_conversation_history.json` 的 assistant 文本
  - 最后回退到 stream JSON/stdout
- `total_cost`：来自 `taskHistory.totalCost`

### KiloCode 1.x

运行产物：

1. `kilocode run --format json` 的 stdout 事件流（包含精确 `sessionID`）
2. `kilocode export <sessionID>` 导出的 JSON（`info` + `messages`）

严格规则如下：
- `models_usage`：
  - 从 export 的 assistant `info.tokens.input` 与 `info.tokens.output` 汇总
  - 模型名来自 export 的 assistant `providerID` + `modelID`
- `llm_calls`：统计 export 中 assistant 消息条数（排除 `summary == true`）
- `tool_calls`：统计 assistant `parts` 中 `type == "tool"` 且 `state.status` 为 `completed` 或 `error` 的条目
- `response`：
  - 优先 run 事件流里最后一条 `text` 事件
  - 其次 export 里最后一条 assistant 文本 part
  - 再回退 run 事件流中的 `type == "error"` 消息
- `total_cost`：汇总 export 中 assistant `info.cost`

若命令本身成功但关键字段缺失/无效（`response`、非空 `models_usage`、`llm_calls >= 1`、`tool_calls >= 0`、非空 `trajectory_path`），cakit 会返回非零 `exit_code`。

`trajectory_path` 指向由运行产物转换得到的 YAML 人类可读轨迹文件（不截断）。
