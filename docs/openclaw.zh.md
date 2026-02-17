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
| `CAKIT_OPENCLAW_API_KEY` | OpenClaw custom provider onboarding 的 API key | 是 |
| `CAKIT_OPENCLAW_BASE_URL` | OpenAI 兼容 base URL | 是 |
| `CAKIT_OPENCLAW_MODEL` | 模型引用（`provider/model` 或裸 `model`） | 是 |
| `CAKIT_OPENCLAW_PROVIDER_ID` | 可选 custom provider id | 否 |

## 运行

`cakit run openclaw "<prompt>"` 实际执行：

```bash
openclaw agent --local --agent main --session-id <generated_id> --message "<prompt>" --json
```

推理强度映射：
- `cakit run openclaw ... --reasoning-effort off|minimal|low|medium|high`
- cakit 会转发为 `openclaw agent --thinking <value>`。

## 统计提取

`cakit run openclaw` 会严格从以下来源提取统计：

1. CLI JSON 返回（`payloads` + `meta.agentMeta`）：
   - `response`
   - `provider/model`
   - usage（`input`、`output`、`cacheRead`、`cacheWrite`、`total`）
2. 会话 transcript：
   - `~/.openclaw/agents/main/sessions/<session_id>.jsonl`
   - `llm_calls`：带有效 usage 的 assistant 消息数
   - `tool_calls`：transcript 消息中的 tool-use 块计数

若关键统计无法解析，cakit 会返回非零退出码。
