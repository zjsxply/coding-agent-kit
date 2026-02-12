# 模型覆盖（`cakit run --model`）

本文说明 `--model` 在各 coding agent 上的生效方式。

执行 `cakit run <agent> ... --model <name>` 时，cakit 仅在该次运行的子进程环境里覆盖模型相关变量，不会修改当前进程的 `os.environ`。

| Agent | `--model` 覆盖的环境变量 | cakit 额外传递的模型命令行参数 |
| --- | --- | --- |
| `claude` | `ANTHROPIC_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL`、`ANTHROPIC_DEFAULT_HAIKU_MODEL`、`CLAUDE_CODE_SUBAGENT_MODEL` | `--model <name>`（取自 `ANTHROPIC_MODEL`） |
| `codex` | `CODEX_MODEL` | `--model <name>` |
| `cursor` | `CURSOR_MODEL` | `--model <name>` |
| `copilot` | `COPILOT_MODEL` | `--model <name>` |
| `gemini` | `GEMINI_MODEL` | `--model <name>` |
| `kimi` | `KIMI_MODEL_NAME` | `--model <name>` |
| `qwen` | `QWEN_OPENAI_MODEL` | `--model <name>` |
| `openhands` | `LLM_MODEL` | 无 |
| `swe-agent` | `SWE_AGENT_MODEL` | `--agent.model.name <name>` |
| `trae-oss` | `TRAE_AGENT_MODEL` | 无 |
