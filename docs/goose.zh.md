# Goose Agent（cakit）

本文说明 cakit 如何安装并运行 Goose CLI。

## 安装

`cakit install goose` 使用 Goose 官方安装脚本，并关闭交互配置：

```bash
curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash
```

安装指定版本：

```bash
cakit install goose --version <goose_version>
```

cakit 会把 `--version` 映射为 `GOOSE_VERSION=<value>`，仍调用同一官方安装脚本。

## 配置

`cakit configure goose` 当前是空操作（返回 `config_path: null`）。

你可以通过环境变量让 cakit 运行 Goose，也可以在 cakit 外部使用 Goose 自带交互配置（`goose configure`）。

## API 环境变量

cakit 管理的 Goose API 变量如下：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `CAKIT_GOOSE_PROVIDER` | provider 名称（例如 `openai`） | cakit API 模式必填 |
| `CAKIT_GOOSE_MODEL` | Goose 运行模型名 | cakit API 模式必填（可被 `--model` 单次覆盖） |
| `CAKIT_GOOSE_OPENAI_API_KEY` | OpenAI 兼容 API key | provider 为 `openai` 时必填 |
| `CAKIT_GOOSE_OPENAI_BASE_URL` | OpenAI 兼容 base URL（例如 `https://host/v1`） | 可选 |
| `CAKIT_GOOSE_OPENAI_BASE_PATH` | 可选 API path 覆盖（例如 `v1/chat/completions`） | 可选 |

当设置 `CAKIT_GOOSE_OPENAI_BASE_URL` 时，cakit 会推导 Goose 上游 OpenAI 变量：
- `OPENAI_HOST`
- `OPENAI_BASE_PATH`

## 运行行为

cakit 以 headless + stream JSON 方式运行 Goose：

```bash
goose run -t "<prompt>" --name <unique_name> --output-format stream-json
```

- cakit 运行时固定设置 `GOOSE_MODE=auto`（非交互）。
- `cakit run goose --model <name>` 会传递 `--model <name>`，并在子进程设置 `GOOSE_MODEL`。
- 媒体参数不支持（`--image` / `--video`）。

## 统计提取

`cakit run goose` 采用严格字段解析：

1. 运行时使用唯一会话名（`--name`）。
2. 按该名称精确导出会话：
   - `goose session export --name <unique_name> --format json`
3. 解析固定字段：
   - `models_usage`：
     - 首选 `accumulated_input_tokens` / `accumulated_output_tokens` / `accumulated_total_tokens`
     - 必要时回退到非累计 `input_tokens` / `output_tokens` / `total_tokens`
   - 模型名：
     - 优先 `stream-json` 的 `model_change.model`，其次 session 的 `model_config.model_name`
   - `llm_calls`：`conversation.messages` 中 `role == "assistant"` 的消息数
   - `tool_calls`：assistant 消息 `content` 中 `type` 为 `toolRequest` 或 `frontendToolRequest` 的条数
   - `response`：会话里最后一条 assistant 文本内容

若 Goose 命令本身成功，但上述关键统计字段缺失或无效，cakit 会返回非零 `exit_code`。

`trajectory_path` 指向由 Goose 原始输出转换得到的 YAML 人类可读轨迹文件。
