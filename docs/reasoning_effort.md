# Reasoning Effort (`cakit run --reasoning-effort`)

This document describes how cakit maps the unified `--reasoning-effort` parameter to each coding agent.

If an agent does not support reasoning/thinking controls in cakit, passing `--reasoning-effort` returns an error with exit code `2`.

The status in this table was verified on **February 17, 2026**.

- OSS agents (`codex`, `gemini`, `crush`, `opencode`, `auggie`, `continue`, `openclaw`, `deepagents`, `kimi`, `qwen`, `openhands`, `swe-agent`, `trae-oss`): verified by reading upstream source code.
- Non-OSS agents (`claude`, `codebuddy`, `factory`, `cursor`, `copilot`, `qoder`): verified from official CLI docs.

| Agent | Supported values in cakit | Mapping in cakit | Upstream status |
| --- | --- | --- | --- |
| `claude` | `low`, `medium`, `high`, `max` | Sets `CLAUDE_CODE_EFFORT_LEVEL=<value>` for `claude` CLI | Closed-source CLI; cakit mapping is doc-based |
| `codex` | `minimal`, `low`, `medium`, `high`, `xhigh` | Adds `-c model_reasoning_effort=<value>` to `codex exec` | Upstream SDK/CLI supports `model_reasoning_effort` via `--config` |
| `codebuddy` | Not supported in `cakit run` | Not supported in cakit | Upstream CLI exposes model selection (`--model`) but no dedicated per-run reasoning-effort flag |
| `factory` | `off`, `none`, `low`, `medium`, `high` | Adds `--reasoning-effort <value>` to `droid exec` | Closed-source CLI docs expose `--reasoning-effort` in exec mode |
| `cursor` | Not supported | Not supported in cakit | Closed-source CLI; no documented reasoning/thinking toggle |
| `copilot` | Not supported | Not supported in cakit | Closed-source CLI; no documented reasoning/thinking toggle |
| `gemini` | Not supported in `cakit run` | Not supported in cakit | Upstream has thinking controls via model config aliases/settings (`thinkingConfig`), but no dedicated per-run reasoning-effort flag |
| `crush` | Not supported in `cakit run` | Not supported in cakit | Upstream `crush run` exposes model selection (`--model` / `--small-model`) but no dedicated per-run reasoning-effort flag; in cakit, when a model is selected for Crush, the same model is applied to both large and small model slots |
| `opencode` | Not supported in `cakit run` | Not supported in cakit | Upstream `opencode run` exposes `--variant`/`--thinking` controls, but cakit does not currently map unified `--reasoning-effort` to provider/model-specific variant semantics |
| `auggie` | Not supported in `cakit run` | Not supported in cakit | Upstream CLI supports model selection (`--model`) but no dedicated per-run reasoning-effort flag |
| `continue` | Not supported in `cakit run` | Not supported in cakit | Upstream `cn` exposes no dedicated per-run reasoning-effort flag |
| `openclaw` | `off`, `minimal`, `low`, `medium`, `high` | Adds `--thinking <value>` to `openclaw agent` | Upstream `openclaw agent` supports `--thinking` |
| `deepagents` | Not supported in `cakit run` | Not supported in cakit | Upstream `deepagents` CLI has no dedicated per-run reasoning-effort flag |
| `kimi` | `thinking`, `none` | Adds `--thinking` / `--no-thinking` to `kimi` | Upstream CLI provides `--thinking/--no-thinking` directly |
| `qwen` | Not supported in `cakit run` | Not supported in cakit | Upstream supports `model.generationConfig.reasoning` (and provider `extra_body`) in config, but no dedicated per-run reasoning-effort flag |
| `qoder` | Not supported in `cakit run` | Not supported in cakit | Upstream `qodercli` exposes model tier selection (`--model`) but no dedicated per-run reasoning-effort flag |
| `openhands` | Not supported in `cakit run` | Not supported in cakit | Upstream supports `reasoning_effort` in LLM config/env (`LLM_REASONING_EFFORT`), but no dedicated reasoning-effort CLI argument |
| `swe-agent` | Not supported in `cakit run` | Not supported in cakit | Upstream supports provider-specific reasoning fields through `agent.model.completion_kwargs`, but no dedicated unified reasoning-effort CLI flag |
| `trae-oss` | Not supported | Not supported in cakit | Upstream CLI/config has no reasoning-effort setting (only the `sequentialthinking` tool) |
