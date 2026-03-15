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

cakit 会把 `--version` 映射为 `GOOSE_VERSION=<value>`，仍调用同一官方安装脚本；像 `1.22.0` 这样的纯 semver 会规范化为上游 `v1.22.0` tag 形式。

在 Linux 上，除了 `bzip2`、`tar` 这类解包工具外，cakit 还会把当前 Goose 发行二进制实际依赖的系统运行库 `libxcb`、`libgomp` 也建模为运行时依赖。
由于官方脚本会直接从所选 release tag 下载当前平台归档，只有该 tag 仍然发布了当前平台对应资产时，指定版本安装才会成功。

## 配置

`cakit configure goose` 当前是空操作（返回 `config_path: null`）。

你可以通过环境变量让 cakit 运行 Goose，也可以在 cakit 外部使用 Goose 自带交互配置（`goose configure`）。

## API 环境变量

cakit 管理的 Goose API 变量如下：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `CAKIT_GOOSE_PROVIDER` | provider 名称（例如 `openai`） | cakit API 模式必填 |
| `CAKIT_GOOSE_MODEL` | Goose 运行模型名（回退：`OPENAI_DEFAULT_MODEL`） | cakit API 模式必填（可被 `--model` 单次覆盖） |
| `CAKIT_GOOSE_OPENAI_API_KEY` | OpenAI 兼容 API key（回退：`OPENAI_API_KEY`） | provider 为 `openai` 时必填 |
| `CAKIT_GOOSE_OPENAI_BASE_URL` | OpenAI 兼容 base URL（例如 `https://host/v1`；回退：`OPENAI_BASE_URL`） | 可选 |
| `CAKIT_GOOSE_OPENAI_BASE_PATH` | 可选 API path 覆盖（例如 `v1/chat/completions`） | 可选 |

当共享 `OPENAI_*` 变量已设置且 Goose provider 未设置时，cakit 会默认 provider 为 `openai`。

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
- 模型优先级为：`--model` > `CAKIT_GOOSE_MODEL`/`GOOSE_MODEL` > `OPENAI_DEFAULT_MODEL`。
- 支持 `cakit run goose --image/--video`，实现方式为自然语言本地路径注入。

## 统计提取

`cakit run goose` 会把本次 run 的临时 Goose HOME 视为 swarm/subagent 统计的权威来源：

1. cakit 每次运行都会创建隔离的临时 `HOME`/`XDG_*` 目录。
2. 统计严格从这份 run-local 状态提取：
   - session 数据库：
     - `<临时 HOME>/data/goose/sessions/sessions.db`
   - request 日志：
     - `<临时 HOME>/state/goose/logs/llm_request.*.jsonl`
   - 主会话导出（仅用于 `response`）：
     - `goose session export --session-id <id> --format json`
3. 解析固定字段：
   - `models_usage`：
     - 把 SQLite 里所有 session 行（包括 `sub_agent`）的
       `accumulated_input_tokens` / `accumulated_output_tokens` / `accumulated_total_tokens` 求和
   - 模型名：
     - 每个 session 的 `model_config_json.model_name`
   - `tool_calls`：
     - 统计所有 run-local session 的 assistant `content_json` 中
       `type == "toolRequest"` 或 `type == "frontendToolRequest"` 的块数量
   - `llm_calls`：
     - 仅当 `llm_request.*.jsonl` 的 usage 求和与 session usage 求和完全一致时，
       才把日志文件数作为精确 `llm_calls`；否则返回 `null`，不做猜测
   - `response`：
     - 主会话导出里的最后一条 assistant 文本内容

若 Goose 命令本身成功，但上述关键统计字段缺失或无效，cakit 会返回非零 `exit_code`。

`trajectory_path` 指向 family-aware 的 YAML 轨迹，包含 CLI stdout、主会话导出、run-local Goose
SQLite 中所有 session/message 的快照，以及该临时 HOME 下可用的 `llm_request.*.jsonl` 日志。
