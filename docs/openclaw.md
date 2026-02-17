# OpenClaw

## Install

```bash
cakit install openclaw
```

Install a specific version:

```bash
cakit install openclaw --version <openclaw_version>
```

`cakit install openclaw` installs npm package `openclaw` (user scope by default).

## API Configuration (`cakit configure openclaw`)

`cakit configure openclaw` runs OpenClaw onboarding in non-interactive mode:

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

Environment variables used by cakit:

| Variable | Description | Required |
| --- | --- | --- |
| `CAKIT_OPENCLAW_API_KEY` | API key for OpenClaw custom provider onboarding | yes |
| `CAKIT_OPENCLAW_BASE_URL` | OpenAI-compatible base URL | yes |
| `CAKIT_OPENCLAW_MODEL` | Model ref (`provider/model` or bare `model`) | yes |
| `CAKIT_OPENCLAW_PROVIDER_ID` | Optional custom provider id | no |

## Run

`cakit run openclaw "<prompt>"` executes:

```bash
openclaw agent --local --agent main --session-id <generated_id> --message "<prompt>" --json
```

Reasoning effort mapping:
- `cakit run openclaw ... --reasoning-effort off|minimal|low|medium|high`
- cakit forwards it to `openclaw agent --thinking <value>`.

## Stats Extraction

`cakit run openclaw` extracts stats strictly from:

1. CLI JSON envelope (`payloads` + `meta.agentMeta`):
   - `response`
   - `provider/model`
   - usage (`input`, `output`, `cacheRead`, `cacheWrite`, `total`)
2. Session transcript:
   - `~/.openclaw/agents/main/sessions/<session_id>.jsonl`
   - `llm_calls`: assistant messages with valid usage
   - `tool_calls`: tool-use blocks in transcript messages

If required stats cannot be parsed, cakit returns non-zero.
