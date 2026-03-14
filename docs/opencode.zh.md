# OpenCode Agent（cakit）

本文说明 cakit 如何运行 OpenCode 并提取运行统计信息。

**安装方式**
- `cakit install opencode` 会运行 OpenCode 官方安装脚本，但 cakit 会先通过宿主机包管理器补齐缺失的 `which` 运行时依赖，并通过 `--no-modify-path` 禁止上游修改 PATH 配置文件。
- `cakit install opencode --version <version>` 会在同样的前提下传入 `--no-modify-path --version <value>`。
- 实际调用的上游入口为：
  - `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path`
  - `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path --version <version>`
- cakit 会先尝试这条脚本路径；如果脚本路径失败，再回退到 `npm install -g opencode-ai`。
- `--scope user|global` 对主脚本路径不生效；只有在 cakit 触发 npm 回退时才会影响安装位置。
- cakit 会刻意禁止上游安装器改写 shell rc/profile 文件；如果你想在 cakit 之外的登录 shell 里直接使用它，需要自行把 `~/.opencode/bin` 暴露到 PATH。

**鉴权与配置**
- OAuth：使用上游 CLI 执行 `opencode auth login`。
- API 模式（OpenAI 兼容）请设置：
  - `CAKIT_OPENCODE_OPENAI_API_KEY`
  - `CAKIT_OPENCODE_MODEL`（使用 `provider/model` 或 `provider:model`；若使用裸模型名，需要同时设置 `CAKIT_OPENCODE_PROVIDER`）
  - `CAKIT_OPENCODE_PROVIDER`（当 `CAKIT_OPENCODE_MODEL` 已带 provider 时可不填；provider 列表可用 `opencode models` 查看）
  - `CAKIT_OPENCODE_OPENAI_BASE_URL`（可选）
  - `CAKIT_OPENCODE_MODEL_CAPABILITIES`（可选，用于自定义 API 模型；填写输入模态能力，逗号分隔，取值来自 `text,audio,image,video,pdf`，例如 `image,video`）
- 当 agent 专属变量未设置时，支持共享回退：
  - `OPENAI_API_KEY` -> `CAKIT_OPENCODE_OPENAI_API_KEY`
  - `OPENAI_BASE_URL` -> `CAKIT_OPENCODE_OPENAI_BASE_URL`
  - `OPENAI_DEFAULT_MODEL` -> `CAKIT_OPENCODE_MODEL`（需要时默认 provider 为 `openai`）
- `cakit configure opencode` 是空操作；cakit 使用按次运行环境注入。

**运行命令**
- cakit 实际执行：
  - `opencode run --format json [--model <provider/model>] [--file <path> ...] -- <prompt>`
- API 模式下，cakit 会把 XDG 路径隔离到 `/tmp/cakit-opencode-*`。
- 若设置 `CAKIT_OPENCODE_OPENAI_BASE_URL`，cakit 通过 `OPENCODE_CONFIG_CONTENT` 注入 provider `baseURL`。
- 若设置 `CAKIT_OPENCODE_MODEL_CAPABILITIES`，cakit 会在 `OPENCODE_CONFIG_CONTENT` 注入 `modalities.input`/`modalities.output`，让 OpenCode 按声明识别自定义 API 模型的多模态能力。
- 模型优先级为：`--model` > `CAKIT_OPENCODE_MODEL` > `OPENAI_DEFAULT_MODEL`。

**图像与视频输入**
- cakit 会把本地媒体文件映射为重复的 `opencode run --file <path>` 参数。
- 图像输入在所选模型/provider 支持图片附件时可用。
- OpenCode `1.2.24` 下本地视频文件当前无法作为多模态附件透传（上游 Read 逻辑会把二进制视频文件拒绝）。

**统计提取（严格模式）**
- cakit 先从 OpenCode `--format json` 输出中读取本次运行的 `sessionID`。
- 再调用 `opencode export <sessionID>`，仅解析该精确会话。
- `agent_version`：来自 `opencode --version`。
- `response`：来自运行 JSON 事件中最后一个文本块（`type == "text"` 且 `part.type == "text"`）。
- `models_usage`：
  - 来源：导出会话里的 assistant 消息（`messages[].info.role == "assistant"`）。
  - 模型名：`providerID/modelID`。
  - token 来源为 `info.tokens`：
    - prompt tokens: `input + cache.read + cache.write`
    - completion tokens: `output + reasoning`（若存在 `reasoning` 则计入）
    - total tokens：优先取 `info.tokens.total`，缺失时才回退为 `prompt_tokens + completion_tokens`
    - 历史 OpenCode step 日志里出现过上游 `info.tokens.total` 与 cakit prompt/completion 拆分不相等的情况；cakit 会保留这个上游 total
- `llm_calls`：导出会话中 assistant 消息数量。
- `tool_calls`：导出会话中 assistant 消息里 `type == "tool"` 的 part 数量。
- `total_cost`：assistant `info.cost` 求和。
- `output_path` / `raw_output`：本次运行捕获的 stdout/stderr。
- `trajectory_path`：基于原始输出生成的 YAML 结构化轨迹（不截断）。
