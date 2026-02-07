# Model Override (`cakit run --model`)

This document describes how `--model` is applied for each coding agent.

`cakit run <agent> ... --model <name>` sets model-related environment variables for the selected agent for the current run process, then restores them after the run.

| Agent | Env keys overridden by `--model` | Extra model CLI flag passed by cakit |
| --- | --- | --- |
| `codex` | `CODEX_MODEL` | `--model <name>` |
| `claude` | `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL` | `--model <name>` (from `ANTHROPIC_MODEL`) |
| `copilot` | `COPILOT_MODEL` | `--model <name>` |
| `gemini` | `GEMINI_MODEL`, `GOOGLE_GEMINI_MODEL` | `--model <name>` |
| `kimi` | `KIMI_MODEL_NAME` | None |
| `qwen` | `QWEN_OPENAI_MODEL`, `QWEN_MODEL` | `--model <name>` |
| `openhands` | `OPENHANDS_LLM_MODEL`, `LLM_MODEL` | None |
| `swe-agent` | `SWE_AGENT_MODEL` | `--agent.model.name <name>` |
| `trae-oss` | `TRAE_AGENT_MODEL` | None |
| `cursor` | `CURSOR_MODEL` | `--model <name>` |

