# Deep Agents Agent（cakit）

本文说明 cakit 如何安装与运行 Deep Agents CLI。

## 安装

安装最新版本：

```bash
cakit install deepagents
```

安装指定版本：

```bash
cakit install deepagents --version <deepagents_cli_version>
```

cakit 默认使用 `uv tool install` 安装 `deepagents-cli`（带 `--force`）；若本机无 `uv`，回退到 `pip install`。

## 配置

`cakit configure deepagents` 为无操作（`config_path: null`）。

`cakit run deepagents` 的环境变量映射：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `DEEPAGENTS_OPENAI_API_KEY` | OpenAI 兼容端点 API Key | 必填 |
| `DEEPAGENTS_OPENAI_BASE_URL` | OpenAI 兼容 base URL | 可选 |
| `DEEPAGENTS_OPENAI_MODEL` | 基础模型 | 必填 |

模型选择规则：
- 单次运行中 `--model` 优先级最高。
- 若模型是 `provider/model`，cakit 会改写为 Deep Agents 可识别的 `provider:model`。
- 若模型不带 provider 前缀，cakit 归一化为 `openai:<model>`。

## 图像与视频输入

- `cakit run deepagents --image ...` 不支持。
- `cakit run deepagents --video ...` 不支持。

Deep Agents 的非交互 CLI 未文档化通用 `--image` / `--video` 参数。

## 统计字段提取

`cakit run deepagents` 采用严格提取流程：

1. 运行 `deepagents -n ... --no-stream`，从输出中精确解析 `Thread: <id>`。
2. 读取 `~/.deepagents/sessions.db`，按精确 `thread_id` 选取最新 `checkpoints` 记录。
3. 使用 Deep Agents 工具运行时中的 LangGraph `JsonPlusSerializer` 解码 checkpoint。
4. 从 `channel_values.messages` 聚合：
   - `llm_calls`：`AIMessage` 条数。
   - `models_usage`：按精确 `response_metadata.model_name` 聚合 `usage_metadata.input_tokens` + `usage_metadata.output_tokens`。
   - `tool_calls`：所有 `AIMessage.tool_calls` 长度之和。
   - `response`：最后一条非空 assistant 文本。
5. 若关键统计字段无法按上述精确字段提取，cakit 对该次 run 返回非零退出状态。

`trajectory_path` 指向由原始 CLI 输出格式化后的可读轨迹文件。
