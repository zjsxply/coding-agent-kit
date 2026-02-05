# AGENTS.md

## 项目定位
- Coding Agent Kit 是面向学术实验的轻量 CLI，用统一方式安装和运行主流 coding agent，并输出结构化统计信息。
- “coding agent”指 CLI 本体（包括 `cursor-agent`），不包含 IDE 或 IDE 插件。
- 本仓库为独立项目，仓库根目录即项目根目录。

## 环境与依赖
- 使用 `uv` 管理 Python 环境与依赖。
- 安装依赖：`uv sync`
- 执行任何 Python 命令前，先激活环境：`source .venv/bin/activate`
- API 鉴权请使用 `.env.template` 生成 `.env`，并在当前 shell 执行 `set -a; source .env; set +a`。

## 常用命令
- 生成 `.env` 模板：`cakit env --output .env`
- 安装并配置 agent：`cakit install <agent>`（默认无限制模式/Yolo）
- 运行并输出 JSON 统计：`cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image]`
- 安装 Fast Shell Power Tools（推荐）：`cakit tools`
- 冒烟测试：`scripts/test_agents.sh [agent ...]`

## 代码结构与风格
- `src/agents/`：每个 agent 一个文件、一个 class。所有 agent-specific 逻辑（安装、运行、usage 提取等）必须放在对应 class 内。
- `src/utils.py`：仅放必要的通用工具函数；一行能解决的操作不要封装成函数。
- 使用标准库解析 JSON；若必须自定义解析，放到 `src/utils.py`。
- 术语统一使用 “coding agent”。
- 命名使用 `trae-oss` 以区分其他 Trae 产品。

## 行为约束
- `cakit run` 若发现未安装对应 agent，需要自动执行 `cakit install <agent>` 并提示。
- 预期成功的命令必须返回 0；usage 解析失败或关键信息缺失必须返回非 0。
- `cakit install` 需自动安装缺失的运行时依赖（如 Node.js、uv），并兼容无 `sudo` 或 root 环境。
- `cakit tools` 仅支持 Linux；需处理无 `sudo` 或 root 环境；在非 `x86_64/amd64` 上给出清晰提示并跳过。
- 不做输出截断（无需 `_preview`）；输出字段为 `raw_output`。
- `get_version` 不做 fallback。

## 鉴权与统计输出要求
- 必须同时支持 OAuth 与 API 两种鉴权方式，并在 README 中说明各 agent 的登录方式。
- 统计输出需包含：
  - `agent`, `agent_version`
  - `runtime_seconds`
  - `models_usage`（按模型拆分，包含 token usage）
  - `tool_calls`、`llm_calls`、`total_cost`（若可获取）
  - `telemetry_log`（若启用，返回日志路径或 OTEL endpoint）
  - `response`, `exit_code`, `output_path`, `raw_output`
- 能支持的 agent 必须支持图像输入；Codex 支持多图。若不支持，需在 README 中明确标注。

## 文档与配置同步
- 新增或修改 agent 时，需同步更新：
  - `README.md`、`README.zh.md`
  - `.env.template`
  - `docs/<agent>.md`（例如 `docs/codex.md`）
  - `docs/<agent>.zh.md`（例如 `docs/codex.zh.md`）
  - 支持的 Agent 列表、登录方式说明、测试覆盖矩阵、Todo
- 修改 `AGENTS.md` 时，也需要同步更新 `AGENTS.zh.md`。
