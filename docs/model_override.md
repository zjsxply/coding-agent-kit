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
| `kimi` | `KIMI_MODEL_NAME` | `--model <name>` |
| `qwen` | `QWEN_OPENAI_MODEL` | `--model <name>` |
| `openhands` | `LLM_MODEL` | None |
| `swe-agent` | `SWE_AGENT_MODEL` | `--agent.model.name <name>` |
| `trae-oss` | `TRAE_AGENT_MODEL` | None |
