# AGENTS.md

## 项目定位
- Coding Agent Kit 是面向学术实验的轻量 CLI，用统一方式安装和运行主流 coding agent，并输出结构化统计信息。
- “coding agent”指 CLI 本体（包括 `cursor-agent`），不包含 IDE 或 IDE 插件。
- 本仓库为独立项目，仓库根目录即项目根目录。
- 开源 coding agent 的仓库链接可在 `README.zh.md` 的「支持的 Agent」表格中查看。

## 环境与依赖
- 使用 `uv` 管理 Python 环境与依赖。
- 安装依赖：`uv sync`
- 执行任何 Python 命令前，先激活环境：`source .venv/bin/activate`
- API 鉴权请使用 `.env.template` 生成 `.env`，并在当前 shell 执行 `set -a; source .env; set +a`。
- 在 agent 实现代码中，cakit 受管控环境变量请直接从 `os.environ` 读取（不要从 `base_env` 读取受管控变量）。
- `--env-file` 用于传递 `.env.template` 未管理的额外变量；受管控变量应来自当前 shell 环境（例如通过 `.env` + `source`）。
- 不要在 cakit 代码里实现“agent 专用环境变量回退到通用 `LLM_*` 环境变量”的兼容逻辑。若测试需要使用 `LLM_*`，应在测试 shell/命令中做环境变量重定向，而不是把回退逻辑写入产品代码。

## 常用命令
- 生成 `.env` 模板：`cakit env --output .env`
- 安装并配置 agent：`cakit install <agent>`（默认无限制模式/YOLO）
- 运行并输出 JSON 统计：`cakit run <agent> "<prompt>" [--cwd /path/to/repo] [--image /path/to/image] [--video /path/to/video]`
- 通用可用性测试流程：`python tests/availability_test.py <agent...>`
- 安装 Fast Shell Power Tools（推荐）：`cakit tools`

## Agent 可用性测试流程
- 优先执行统一脚本：
  - `source .venv/bin/activate`
  - `set -a; source .env; set +a`
  - `python tests/availability_test.py <agent...>`
- Agent 可用性测试耗时可能较长；请使用 10 分钟超时（`--timeout-seconds 600`）以降低中途被打断的风险。
- 请并行执行多个 coding agent 调用任务，以节约总测试时间并降低预期超时风险。
- 若并行执行引入竞态问题（例如并发安装），应先修复代码，再采信测试结果。
- 默认不要求做稳定性重复跑；若单次运行成功，且响应语义与必需统计字段都正确，即可判定该能力可用。
- 不要为 coding agent 可用性或统计提取新增代码级单元/集成测试点。统一使用 `tests/availability_test.py`，并结合真实输出做主观人工判读。
- 不要把脚本自动 pass/fail 当作唯一依据；必须人工阅读响应内容并判断是否正确。
- 若需要手工逐项验证，再在同一个 shell 中按以下顺序执行：
  1. `source .venv/bin/activate`
  2. `set -a; source .env; set +a`
  3. `cakit run <agent> "Reply with exactly this text and nothing else: CAKIT_HEALTHCHECK_OK" > /tmp/cakit-<agent>-basic.json`（基础回复检查，期望返回 `CAKIT_HEALTHCHECK_OK`）
  4. `cakit run <agent> "这幅图片的内容是什么？有什么文字？" --image tests/image1.png > /tmp/cakit-<agent>-image.json`（图像输入检查）
  5. `cakit run <agent> "这个视频里发生了什么？有什么可见文字？" --video tests/video.mp4 > /tmp/cakit-<agent>-video.json`（视频输入检查，使用本地小体积 mp4）
  6. `cakit run <agent> "访问 https://github.com/algorithmicsuperintelligence/openevolve，并简要说明页面内容。" > /tmp/cakit-<agent>-web.json`（联网访问检查）
- 必须补充“prompt 路径多模态检查”：在不传 `--image`/`--video` 的情况下，仅把本地图片/视频路径写进 prompt，验证该 coding agent 是否能通过可用工具自主读取，并记录实际表现。
- 做图像/视频能力检查时，所用基础模型必须原生支持对应模态。若当前模型不支持图像/视频输入（例如纯文本模型），应先切换到支持该模态的模型，再判断该能力是否支持。
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
- 字段名必须精确且稳定。不要对同一信号尝试多个字段名或回退链；必需字段缺失时直接返回 `None`。
- 用量统计必须基于源码确认。若 coding agent CLI 有开源仓库，应先检查 `/tmp` 下是否已有该仓库：若已存在则先进入仓库执行 `git pull` 更新；若不存在再 clone 到 `/tmp` 后进行本地阅读。确认 usage 产生方式后再实现或调整 token 统计逻辑。校验范围必须包含 `llm_calls`、token usage 与 `tool_calls` 的行为。若环境阻止 clone，则给出精确的 `git clone ... /tmp/<repo>` 命令并要求用户在本机执行，然后继续本地检查。
- Token usage 定义为 agent 运行过程中所有 LLM call 的 prompt tokens 与 completion tokens 的总和（包含 subagents 时一并计入）。
- 代码与文档必须保持一致。行为有变更时需在同一提交/修改中同步更新文档，且文档应与实现完全一致（不要出现不匹配的 fallback 或字段描述）。
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
- 媒体 prompt 注入通用能力统一放在 `src/agents/base.py`：
  - 自然语言本地路径注入：`_build_natural_media_prompt`（用于依赖工具读文件的流程）
  - 符号路径注入：`_build_symbolic_media_prompt`（用于 `@{path}` 风格）
- 若 `cakit run --image` / `--video` 通过向 prompt 注入本地路径并由 coding agent 依赖可用工具/模型能力直接读取目标媒体，则计入支持，并在 README 说明具体行为。
- 对视频而言，若仅能先抽帧再按图片读取，不计入正式 `--video` 支持。

## 行为约束
- `cakit run` 若发现未安装对应 agent，需要自动执行 `cakit install <agent>` 并提示。
- 预期成功的命令必须返回 0；usage 解析失败或关键信息缺失必须返回非 0。
- `cakit install` 需自动安装缺失的运行时依赖（如 Node.js、uv），并兼容无 `sudo` 或 root 环境。
- 默认安装行为必须始终指向上游 latest：未传 `--version` 时，代码中不得写死固定默认版本。
- `cakit tools` 仅支持 Linux；需处理无 `sudo` 或 root 环境；在非 `x86_64/amd64` 上给出清晰提示并跳过。
- 调试时产生的临时文件请放在 `/tmp`，不要写进项目目录。
- 不做输出截断（无需 `_preview`）；输出字段为 `raw_output`。
- `get_version` 不做 fallback。
- 代码中不要为环境变量设置硬编码默认值（例如避免 `os.environ.get("X") or "default"`）。环境变量应按原值读取；若必填项缺失，应明确失败或跳过写配置。
- `--model` 覆盖不得修改当前进程的 `os.environ`。
- `cakit run` 的模型选择优先级必须是：先 `--model`，再 `os.environ`。
- `base_env` 仅用于子进程环境透传；不要依赖把 model override 写进 `base_env` 来做模型决策。
- 所有被原始 coding agent 采用的环境变量名称都保持原样；如有在不同 coding agent 里重复的，则加上 coding agent 前缀以消歧。
- OpenHands 仅使用上游环境变量 `LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`。禁止新增或兼容 `OPENHANDS_*` 别名。
- 所有只在 cakit 里定义、用于 cakit 的环境变量都加上 `CAKIT_` 前缀。

## 鉴权与统计输出要求
- 必须同时支持 OAuth 与 API 两种鉴权方式，并在 README 中说明各 agent 的登录方式。
- 统计输出需包含：
  - `agent`, `agent_version`
  - `runtime_seconds`
  - `models_usage`（按模型拆分，包含 token usage）
  - `tool_calls`、`llm_calls`、`total_cost`（若可获取）
  - `telemetry_log`（若启用，返回日志路径或 OTEL endpoint）
  - `response`, `exit_code`, `output_path`, `raw_output`, `trajectory_path`
- `trajectory_path` 必填，且必须指向基于运行产物生成的“格式化、人类可读、无截断”轨迹文件。
- 轨迹转换规则：
  - 运行产物必须转换为结构化的 YAML 格式人类可读输出。
  - 先识别实际数据结构再做转换（除非确实无法转换，否则不能直接保留机器 JSON 原文）。
- 能支持的 agent 必须支持图像输入；Codex 支持多图。若不支持，需在 README 中明确标注。

## 文档与配置同步
- 新增或修改 agent 时，需同步更新：
  - `README.md`、`README.zh.md`
  - `.env.template`
  - `docs/<agent>.md`（例如 `docs/codex.md`）
  - `docs/<agent>.zh.md`（例如 `docs/codex.zh.md`）
  - 支持的 Agent 列表、登录方式说明、测试覆盖矩阵、Todo
- 修改 `AGENTS.md` 时，也需要同步更新 `AGENTS.zh.md`。

## 新 Agent 接入流程
- 新增 coding agent 支持时，必须实现安装与可用性验证，并且同时验证“不指定版本安装”和“指定 `--version` 安装”。
- 必须更新 `README.md` 与 `README.zh.md` 中该 coding agent 的支持列表/表格以及测试覆盖矩阵。
- 新增与修改文件应仿照项目现有实现模式，保持结构、命名和严格解析行为一致。
- 可用性测试时可使用 `.env` 中的 `LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`，但应在测试 shell/命令中重定向到新 coding agent 的环境变量名；禁止在 cakit 代码中新增对 `LLM_*` 的兼容回退。
- 当仓库内有其他 codex 并行修改时，应接纳现有变更并避免干扰与当前任务无关的工作。
