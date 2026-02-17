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
- 可选端点覆盖：`CURSOR_API_BASE`
- API key：`CURSOR_API_KEY`

Cursor 在 cakit 中不支持图像/视频参数（`--image` / `--video` 会返回不支持）。

## 统计提取

cakit 从 stream JSON 输出中提取：
- `models_usage`：从 payload 的 usage 字段或等价字段解析
- `tool_calls`：统计 payload 中工具调用样式事件
- `response`：从 assistant/final 字段提取（必要时回退 stdout）

`trajectory_path` 指向由运行输出转换得到的 YAML 人类可读轨迹文件。
