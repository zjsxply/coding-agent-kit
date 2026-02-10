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
- 运行并输出 JSON 统计：`cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video]`
- 通用可用性测试流程：`python tests/availability_test.py <agent...>`
- 安装 Fast Shell Power Tools（推荐）：`cakit tools`

## Agent 可用性测试流程
- 优先执行统一脚本：
  - `source .venv/bin/activate`
  - `set -a; source .env; set +a`
  - `python tests/availability_test.py <agent...>`
- 若需要手工逐项验证，再在同一个 shell 中按以下顺序执行：
  1. `source .venv/bin/activate`
  2. `set -a; source .env; set +a`
  3. `cakit run <agent> "Reply with exactly this text and nothing else: CAKIT_HEALTHCHECK_OK" > /tmp/cakit-<agent>-basic.json`（基础回复检查，期望返回 `CAKIT_HEALTHCHECK_OK`）
  4. `cakit run <agent> "这幅图片的内容是什么？有什么文字？" --image tests/image1.png > /tmp/cakit-<agent>-image.json`（图像输入检查）
  5. `cakit run <agent> "这个视频里发生了什么？有什么可见文字？" --video tests/video.mp4 > /tmp/cakit-<agent>-video.json`（视频输入检查，使用本地小体积 mp4）
  6. `cakit run <agent> "访问 https://github.com/algorithmicsuperintelligence/openevolve，并简要说明页面内容。" > /tmp/cakit-<agent>-web.json`（联网访问检查）
- 各项通过与否以返回内容是否正确为准，不能只看命令是否启动。
- 必须校验 JSON 中统计字段提取结果：
  1. `response`：字段存在，且为非空文本。
  2. `models_usage`：字段存在，且在成功运行时必须是非空 object，并包含整数 token 字段。
  3. `llm_calls`：字段存在，且在成功运行时必须是整数（`>= 1`）。
  4. `tool_calls`：字段存在，且在成功运行时必须是整数（`>= 0`）。
- 成功运行时若 `models_usage` 为 `{}`，或 `llm_calls`/`tool_calls` 缺失或为 `null`，按提取失败处理。
- 统计字段提取不到时不要写猜测值；必须保留 `None`（JSON 中为 `null`），不要用 `0` 占位。
- 使用 session/log 回退提取时，必须做精确匹配（例如按 `session_id` 精确匹配对应路径），禁止按 mtime 或“最近文件”做模糊匹配。
- `models_usage` 中的模型名必须来自本次运行产物（stdout payload/session 日志），不能从配置、环境变量或 `--model` 输入回填。
- 提取逻辑必须严格按格式读取：仅解析明确、文档化字段；结构异常时应立即返回 `None`，不要叠加多层 fallback 解析器。
- 发生提取失败时必须排查：
  1. `cakit run` 输出中的 `output_path` / `raw_output`。
  2. 上游 coding agent 的日志与会话文件（例如 Kimi：`~/.kimi/logs`、`~/.kimi/sessions/*/*/wire.jsonl`、`~/.kimi/sessions/*/*/context.jsonl`）。
  3. `src/agents/<agent>.py` 中的提取逻辑，并修复解析代码。
- 测试后必须更新 `README.md` 与 `README.zh.md` 的测试覆盖矩阵，并从 `cakit run` 输出中的 `agent_version` 记录 `测试版本`。

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
- 调试时产生的临时文件请放在 `/tmp`，不要写进项目目录。
- 不做输出截断（无需 `_preview`）；输出字段为 `raw_output`。
- `get_version` 不做 fallback。
- 代码中不要为环境变量设置硬编码默认值（例如避免 `os.environ.get("X") or "default"`）。环境变量应按原值读取；若必填项缺失，应明确失败或跳过写配置。
- 所有被原始 coding agent 采用的环境变量名称都保持原样；如有在不同 coding agent 里重复的，则加上 coding agent 前缀以消歧。
- 所有只在 cakit 里定义、用于 cakit 的环境变量都加上 `CAKIT_` 前缀。

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
