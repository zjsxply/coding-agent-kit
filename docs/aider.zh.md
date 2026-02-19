# Aider Agent（cakit）

本文说明 cakit 如何安装与运行 Aider CLI。

## 安装

安装最新版本：

```bash
cakit install aider
```

安装指定版本：

```bash
cakit install aider --version <aider_chat_version>
```

cakit 默认使用 `uv tool install` 安装 `aider-chat`（带 `--force`）；若本机无 `uv`，回退到 `pip install`。

## 配置

`cakit configure aider` 为无操作（`config_path: null`）。

`cakit run aider` 的环境变量映射：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `AIDER_OPENAI_API_KEY` | OpenAI 兼容端点 API Key（回退：`OPENAI_API_KEY`） | 必填 |
| `AIDER_OPENAI_API_BASE` | OpenAI 兼容 base URL（回退：`OPENAI_BASE_URL`） | 可选 |
| `AIDER_MODEL` | 基础模型（回退：`OPENAI_DEFAULT_MODEL`） | 必填 |

模型选择规则：
- 单次运行中 `--model` 优先级最高。
- 若未传 `--model`，cakit 先用 `AIDER_MODEL`，再回退到 `OPENAI_DEFAULT_MODEL`。
- 若模型不带 provider 前缀，cakit 归一化为 `openai/<model>`。
- 若模型是 `provider:model`，cakit 归一化为 `provider/model`。

## 图像与视频输入

- `cakit run aider --image ...` 支持。
- `cakit run aider --video ...` 不支持。

实现方式：
- cakit 会把每个 `--image` 文件映射为 Aider 位置参数文件（等价于 `aider <image-file> ...`），将图片加入 chat 上下文。
- 图像能力依赖模型本身，所选模型必须支持 vision。

## 联网能力

- `cakit run aider` 保持 Aider 默认的 URL 检测开启状态。
- 当提示词中包含 URL 时，Aider 可在上游实现与当前运行环境允许的前提下抓取网页内容并注入聊天上下文。

## 统计字段提取

`cakit run aider` 以单消息模式运行 Aider，并在 `/tmp/cakit-aider-*` 写入运行产物，包括：
- `analytics.jsonl`
- `chat.history.md`
- `llm.history.log`

严格提取流程：
1. 精确解析 `analytics.jsonl` 的 JSONL 事件。
2. `models_usage`：按 `message_send` 的 `properties.main_model` 聚合，并累加 `prompt_tokens`、`completion_tokens`、`total_tokens`。
3. `llm_calls`：`message_send` 事件数量。
4. `tool_calls`：`event` 以 `command_` 开头的事件数量（`--message` 场景通常为 `0`）。
5. `total_cost`：最后一条 `message_send` 的 `properties.total_cost`。
6. `response`：优先取 `llm.history.log` 中最后一个 `LLM RESPONSE` 块；失败时回退到 chat history/输出解析。

若关键统计无法提取，cakit 会返回非零退出码。
