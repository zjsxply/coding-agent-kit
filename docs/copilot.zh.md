# Copilot

## 鉴权

- OAuth：运行 `copilot`，然后执行 `/login`。
- `GH_TOKEN` / `GITHUB_TOKEN` 可作为 GitHub 鉴权 token（需具备 Copilot Requests 权限），但在 cakit 中 Copilot 不按“API 模式 agent”归类。

## 模型选择

- `cakit run copilot --model <name>` 可按次覆盖模型。
- 也支持环境变量 `COPILOT_MODEL`。

## 多模态输入

- `cakit run copilot --image <path>` 通过“自然语言路径注入”方式支持。
- cakit 会把本地图片绝对路径注入到 prompt 中，并提示 Copilot 用可用工具读取文件。
- `cakit run copilot --video <path>` 按不支持处理。

## 统计提取

- cakit 以 `--log-level debug` 运行 Copilot，并从本次 `--log-dir` 日志中解析 model-call payload。
- `models_usage`、`llm_calls`、`tool_calls` 均从这些 payload 提取。
- 若命令成功但必需统计缺失，cakit 会返回非零运行状态。
