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
| `CAKIT_OPENCLAW_API_KEY` | API key for OpenClaw custom provider onboarding (fallback: `OPENAI_API_KEY`) | yes |
| `CAKIT_OPENCLAW_BASE_URL` | OpenAI-compatible base URL (fallback: `OPENAI_BASE_URL`) | yes |
| `CAKIT_OPENCLAW_MODEL` | Model ref (`provider/model` or bare `model`, fallback: `OPENAI_DEFAULT_MODEL`) | yes |
| `CAKIT_OPENCLAW_PROVIDER_ID` | Optional custom provider id | no |
| `CAKIT_OPENCLAW_CONTEXT_WINDOW` | Optional minimum `contextWindow` patched into custom-provider models; must be a positive integer | no |
| `CAKIT_OPENCLAW_MAX_TOKENS` | Optional minimum `maxTokens` patched into custom-provider models; must be a positive integer | no |

If either cakit-only limit override is set to a non-positive or non-integer value, `cakit configure openclaw` / `cakit run openclaw` fails clearly instead of silently falling back.

## Run

`cakit run openclaw "<prompt>"` executes:

```bash
openclaw onboard --non-interactive ... --custom-model-id <resolved_model> --json
openclaw agent --local --agent main --session-id <generated_id> --message "<prompt>" --json
```

Run behavior notes:
- cakit creates an isolated temporary `OPENCLAW_HOME` per run, so parallel runs do not share session/config state.
- cakit runs non-interactive onboarding before `openclaw agent` so `--model` override is applied to the active custom model.
- cakit also aligns `gateway.remote.token` with `gateway.auth.token` in the generated config so local `sessions_spawn` subagent calls can authenticate against the run-local gateway.
- Local OpenClaw subagent spawning still depends on a healthy gateway process for the configured port. If another `openclaw-gateway` is already listening with a different token, `sessions_spawn` can still fail even though cakit's run-local config is correct.
- Model priority is: `--model` > `CAKIT_OPENCLAW_MODEL` > `OPENAI_DEFAULT_MODEL`.

Reasoning effort mapping:
- `cakit run openclaw ... --reasoning-effort off|minimal|low|medium|high`
- cakit forwards it to `openclaw agent --thinking <value>`.

## Stats Extraction

`cakit run openclaw` extracts stats strictly from:

1. Session-family transcripts inside the isolated temporary `OPENCLAW_HOME` (primary source):
   - `<temporary OPENCLAW_HOME>/.openclaw/agents/*/sessions/*.jsonl`
   - cakit aggregates every transcript in that run-local state, so spawned subagents are included
   - `models_usage`: sum assistant `message.usage` across the full transcript family
   - `llm_calls`: count assistant transcript messages with valid usage across the family
   - `tool_calls`: count assistant tool-use occurrences (`content[].type == "toolCall"`) across the family
   - model name comes from assistant `message.provider` + `message.model`, with `model_change` / `model-snapshot`
     transcript events used as fallback context when the assistant message omits them
2. CLI JSON envelope (`payloads` + `meta.agentMeta`) fallback:
   - `response` from `payloads[*].text`
   - fallback usage from `meta.agentMeta.usage` (`total` + `output`)
   - fallback model name from `meta.agentMeta.provider` + `meta.agentMeta.model`
   - used only when transcript family data is unavailable

If required stats cannot be parsed, cakit returns non-zero.

`trajectory_path` follows the same session-family rule: when transcript family files exist in the isolated
temporary `OPENCLAW_HOME`, cakit writes a family-aware YAML trace containing CLI stdout plus every transcript
in that run-local family.
