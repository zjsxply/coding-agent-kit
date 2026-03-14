# OpenClaw

## 安装

```bash
cakit install openclaw
```

指定版本安装：

```bash
cakit install openclaw --version <openclaw_version>
```

`cakit install openclaw` 默认安装 npm 包 `openclaw`（user scope）。

## API 配置（`cakit configure openclaw`）

`cakit configure openclaw` 会以非交互模式运行 OpenClaw onboarding：

```bash
openclaw onboard \
  --non-interactive \
  --accept-risk \
  --mode local \
  --auth-choice custom-api-key \
  --custom-base-url <base_url> \
  --custom-model-id <model_id> \
  --custom-api-key <api_key> \
  --skip-channels --skip-skills --skip-health --skip-ui --skip-daemon --json
```

cakit 使用的环境变量：

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `CAKIT_OPENCLAW_API_KEY` | OpenClaw custom provider onboarding 的 API key（回退：`OPENAI_API_KEY`） | 是 |
| `CAKIT_OPENCLAW_BASE_URL` | OpenAI 兼容 base URL（回退：`OPENAI_BASE_URL`） | 是 |
| `CAKIT_OPENCLAW_MODEL` | 模型引用（`provider/model` 或裸 `model`，回退：`OPENAI_DEFAULT_MODEL`） | 是 |
| `CAKIT_OPENCLAW_PROVIDER_ID` | 可选 custom provider id | 否 |
| `CAKIT_OPENCLAW_CONTEXT_WINDOW` | 可选：写入 custom-provider model 的最小 `contextWindow`；必须为正整数 | 否 |
| `CAKIT_OPENCLAW_MAX_TOKENS` | 可选：写入 custom-provider model 的最小 `maxTokens`；必须为正整数 | 否 |

若这两个 cakit 专用 limit override 中任一值不是正整数，`cakit configure openclaw` / `cakit run openclaw` 会明确报错，而不是静默回退。

## 运行

`cakit run openclaw "<prompt>"` 实际执行：

```bash
openclaw onboard --non-interactive ... --custom-model-id <resolved_model> --json
openclaw agent --local --agent main --session-id <generated_id> --message "<prompt>" --json
```

运行行为说明：
- cakit 每次运行会创建隔离的临时 `OPENCLAW_HOME`，并行运行不会共享会话/配置状态。
- cakit 在 `openclaw agent` 前执行一次非交互 onboarding，以确保 `--model` 覆盖能作用到当前 custom model。
- cakit 还会把生成配置中的 `gateway.remote.token` 对齐为 `gateway.auth.token`，以便本地 `sessions_spawn` 子 agent 调用能通过 run-local gateway 鉴权。
- 但 OpenClaw 本地子 agent spawn 仍依赖目标端口上有健康的 gateway 进程。如果系统里已经有另一个 token 不一致的 `openclaw-gateway` 在监听，即使 cakit 的 run-local 配置正确，`sessions_spawn` 仍可能失败。
- 模型优先级为：`--model` > `CAKIT_OPENCLAW_MODEL` > `OPENAI_DEFAULT_MODEL`。

推理强度映射：
- `cakit run openclaw ... --reasoning-effort off|minimal|low|medium|high`
- cakit 会转发为 `openclaw agent --thinking <value>`。

## 统计提取

`cakit run openclaw` 会严格从以下来源提取统计：

1. 隔离临时 `OPENCLAW_HOME` 里的整棵 session-family transcript（主来源）：
   - `<临时 OPENCLAW_HOME>/.openclaw/agents/*/sessions/*.jsonl`
   - cakit 会聚合同一次 run-local 状态中的所有 transcript，因此会把 spawn 的 subagent 一并计入
   - `models_usage`：聚合整棵 transcript family 中 assistant `message.usage`
   - `llm_calls`：统计整棵 family 中带有效 usage 的 assistant transcript 消息数
   - `tool_calls`：统计整棵 family 中 assistant `content[].type == "toolCall"` 的块数量
   - 模型名来自 assistant 的 `message.provider` + `message.model`；若消息自身缺失，则回退到 transcript 中的
     `model_change` / `model-snapshot` 事件上下文
2. CLI JSON 返回（`payloads` + `meta.agentMeta`）兜底：
   - `response` 来自 `payloads[*].text`
   - 兜底 usage 来自 `meta.agentMeta.usage`（`total` + `output`）
   - 兜底模型名来自 `meta.agentMeta.provider` + `meta.agentMeta.model`
   - 仅在 transcript family 数据不可用时启用

若关键统计无法解析，cakit 会返回非零退出码。

`trajectory_path` 也遵循同样的 session-family 规则：当隔离临时 `OPENCLAW_HOME` 里存在 transcript
family 文件时，cakit 会写入 family-aware 的 YAML 轨迹，包含 CLI stdout 以及该 run-local family 中的全部 transcript。
