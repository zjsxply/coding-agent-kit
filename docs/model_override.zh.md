# 模型覆盖（`cakit run --model`）

本文说明 `--model` 在各 coding agent 上的生效方式。

执行 `cakit run <agent> ... --model <name>` 时，cakit 仅在该次运行的子进程环境里覆盖模型相关变量，不会修改当前进程的 `os.environ`。
对 OpenAI 兼容 API agent，模型解析顺序为：
- `--model`
- 然后使用下表中的 agent 专属模型环境变量
- 最后回退到共享变量 `OPENAI_DEFAULT_MODEL`

| Agent | `--model` 覆盖的环境变量 | cakit 额外传递的模型命令行参数 |
| --- | --- | --- |
| `claude` | `ANTHROPIC_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL`、`ANTHROPIC_DEFAULT_HAIKU_MODEL`、`CLAUDE_CODE_SUBAGENT_MODEL` | `--model <name>`（取自 `ANTHROPIC_MODEL`） |
| `codex` | `CODEX_MODEL` | `--model <name>` |
| `aider` | `AIDER_MODEL` | `--model <name>` |
| `codebuddy` | `CODEBUDDY_MODEL` | `--model <name>` |
| `cursor` | `CURSOR_MODEL` | `--model <name>` |
| `copilot` | `COPILOT_MODEL` | `--model <name>` |
| `gemini` | `GEMINI_MODEL` | `--model <name>` |
| `crush` | `CAKIT_CRUSH_MODEL` | API 模式：生成临时运行配置（large/small 都设为所选模型）；OAuth 模式：`--model <name>` + `--small-model <name>` |
| `opencode` | `CAKIT_OPENCODE_MODEL` | `--model <provider/model>`；若设置 `CAKIT_OPENCODE_OPENAI_BASE_URL`，cakit 还会通过 `OPENCODE_CONFIG_CONTENT` 注入 provider `baseURL` |
| `factory` | `CAKIT_FACTORY_MODEL` | `--model <name>` |
| `auggie` | `CAKIT_AUGGIE_MODEL` | `--model <name>` |
| `continue` | `CAKIT_CONTINUE_OPENAI_MODEL` | 生成临时运行配置（`cn -p --config`） |
| `goose` | `CAKIT_GOOSE_MODEL` | `--model <name>` |
| `kilocode` | 无（运行时配置会按 `--model` 生成模型字段） | `--model <name>` |
| `openclaw` | `CAKIT_OPENCLAW_MODEL` | 无（`openclaw agent` 无按次 `--model` 参数） |
| `deepagents` | `DEEPAGENTS_OPENAI_MODEL` | `--model <name>` |
| `kimi` | `KIMI_MODEL_NAME` | `--model <name>` |
| `qoder` | `CAKIT_QODER_MODEL` | `--model <name>` |
| `trae-cn` | `CAKIT_TRAE_CN_MODEL` | 无 |
| `qwen` | `QWEN_OPENAI_MODEL` | `--model <name>` |
| `openhands` | `LLM_MODEL` | 无 |
| `swe-agent` | `SWE_AGENT_MODEL` | `--agent.model.name <name>` |
| `trae-oss` | `TRAE_AGENT_MODEL` | 无 |
