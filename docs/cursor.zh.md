# Cursor Agent（cakit）

本文说明 cakit 如何安装并运行 Cursor Agent CLI（`cursor-agent`）。

## 安装

`cakit install cursor` 使用 Cursor 官方安装脚本：

```bash
curl -fsS https://cursor.com/install | bash
```

- 默认安装（不传 `--version`）会安装上游最新构建。
- 支持指定版本安装：

```bash
cakit install cursor --version <cursor_build_id>
```

指定版本时，cakit 会下载对应的 Cursor agent 包，并更新 `~/.local/bin/cursor-agent` 软链接。

## 配置

`cakit configure cursor` 为 no-op（`config_path: null`）。

## 运行行为

`cakit run cursor "<prompt>"` 实际执行：

```bash
cursor-agent -p "<prompt>" --print --output-format stream-json --force
```

- 可选模型覆盖：`cakit run cursor --model <model>`
- 模型优先级：`--model` > `CURSOR_MODEL` > `OPENAI_DEFAULT_MODEL`
- 可选端点覆盖：`CURSOR_API_BASE`（回退：`OPENAI_BASE_URL`）
- API key：`CURSOR_API_KEY`（回退：`OPENAI_API_KEY`）

Cursor 在 cakit 中不支持图像/视频参数（`--image` / `--video` 会返回不支持）。

## 统计提取

cakit 按严格事件路径解析 stream-json 输出：
- `response`：
  - 主路径：最后一个 `type == "result"` payload 的 `result`
  - 回退：最后一个 `type == "assistant"` payload 的 `message.content[*].text`
- `tool_calls`：
  - 主路径：统计 `type == "tool_call"` 且 `subtype == "started"` 的唯一 `call_id`
  - 回退：统计全部 `type == "tool_call"` payload 的唯一 `call_id`
- `llm_calls`：
  - 主路径：统计 `type == "assistant"` 与 `type == "tool_call"` payload 中唯一 `model_call_id`
  - 回退：`type == "assistant"` payload 数量
- `models_usage`：
  - usage 仅从精确字段读取：`usage`、`message.usage`、`result.usage`
  - 支持 usage 结构：`input_tokens` + `output_tokens`（可选 `total_tokens`）或 `prompt_tokens` + `completion_tokens`（可选 `total_tokens`）
  - 模型名仅从运行产物读取（`type == "system"`、`subtype == "init"`、字段 `model`）
  - 不会用 `--model` 或环境变量回填模型名

`trajectory_path` 指向由运行输出转换得到的 YAML 人类可读轨迹文件。
