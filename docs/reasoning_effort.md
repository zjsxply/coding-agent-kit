# Reasoning Effort (`cakit run --reasoning-effort`)

This document describes how cakit maps the unified `--reasoning-effort` parameter to each coding agent.

If an agent does not support reasoning/thinking controls in cakit, passing `--reasoning-effort` returns an error with exit code `2`.

The status in this table was verified on **February 12, 2026**.

- OSS agents (`codex`, `gemini`, `kimi`, `qwen`, `openhands`, `swe-agent`, `trae-oss`): verified by reading upstream source code.
- Non-OSS agents (`claude`, `cursor`, `copilot`): verified from official CLI docs.

| Agent | Supported values in cakit | Mapping in cakit | Upstream status |
| --- | --- | --- | --- |
| `claude` | `low`, `medium`, `high`, `max` | Sets `CLAUDE_CODE_EFFORT_LEVEL=<value>` for `claude` CLI | Closed-source CLI; cakit mapping is doc-based |
| `codex` | `minimal`, `low`, `medium`, `high`, `xhigh` | Adds `-c model_reasoning_effort=<value>` to `codex exec` | Upstream SDK/CLI supports `model_reasoning_effort` via `--config` |
| `cursor` | Not supported | Not supported in cakit | Closed-source CLI; no documented reasoning/thinking toggle |
| `copilot` | Not supported | Not supported in cakit | Closed-source CLI; no documented reasoning/thinking toggle |
| `gemini` | Not supported in `cakit run` | Not supported in cakit | Upstream has thinking controls via model config aliases/settings (`thinkingConfig`), but no dedicated per-run reasoning-effort flag |
| `kimi` | `thinking`, `none` | Adds `--thinking` / `--no-thinking` to `kimi` | Upstream CLI provides `--thinking/--no-thinking` directly |
| `qwen` | Not supported in `cakit run` | Not supported in cakit | Upstream supports `model.generationConfig.reasoning` (and provider `extra_body`) in config, but no dedicated per-run reasoning-effort flag |
| `openhands` | Not supported in `cakit run` | Not supported in cakit | Upstream supports `reasoning_effort` in LLM config/env (`LLM_REASONING_EFFORT`), but no dedicated reasoning-effort CLI argument |
| `swe-agent` | Not supported in `cakit run` | Not supported in cakit | Upstream supports provider-specific reasoning fields through `agent.model.completion_kwargs`, but no dedicated unified reasoning-effort CLI flag |
| `trae-oss` | Not supported | Not supported in cakit | Upstream CLI/config has no reasoning-effort setting (only the `sequentialthinking` tool) |
