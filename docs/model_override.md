# Model Override (`cakit run --model`)

This document describes how `--model` is applied for each coding agent.

`cakit run <agent> ... --model <name>` applies model-related overrides only in the cakit-managed child process environment for that run. It does not mutate the current process `os.environ`.

| Agent | Env keys overridden by `--model` | Extra model CLI flag passed by cakit |
| --- | --- | --- |
| `claude` | `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL` | `--model <name>` (from `ANTHROPIC_MODEL`) |
| `codex` | `CODEX_MODEL` | `--model <name>` |
| `cursor` | `CURSOR_MODEL` | `--model <name>` |
| `copilot` | `COPILOT_MODEL` | `--model <name>` |
| `gemini` | `GEMINI_MODEL` | `--model <name>` |
| `crush` | `CAKIT_CRUSH_MODEL` | API mode: generated runtime config (large/small both set to the selected model); OAuth mode: `--model <name>` + `--small-model <name>` |
| `auggie` | `CAKIT_AUGGIE_MODEL` | `--model <name>` |
| `continue` | `CAKIT_CONTINUE_OPENAI_MODEL` | generated runtime config (`cn -p --config`) |
| `goose` | `CAKIT_GOOSE_MODEL` | `--model <name>` |
| `kilocode` | none (runtime config model is generated from `--model`) | `--model <name>` |
| `openclaw` | `CAKIT_OPENCLAW_MODEL` | None (`openclaw agent` has no per-run `--model` flag) |
| `deepagents` | `DEEPAGENTS_OPENAI_MODEL` | `--model <name>` |
| `kimi` | `KIMI_MODEL_NAME` | `--model <name>` |
| `trae-cn` | `CAKIT_TRAE_CN_MODEL` | None |
| `qwen` | `QWEN_OPENAI_MODEL` | `--model <name>` |
| `openhands` | `LLM_MODEL` | None |
| `swe-agent` | `SWE_AGENT_MODEL` | `--agent.model.name <name>` |
| `trae-oss` | `TRAE_AGENT_MODEL` | None |
