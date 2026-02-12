# Reasoning Effort (`cakit run --reasoning-effort`)

This document describes how cakit maps the unified `--reasoning-effort` parameter to each coding agent.

If an agent does not support reasoning/thinking controls in cakit, passing `--reasoning-effort` returns an error with exit code `2`.

The status in this table was verified against official CLI docs on **February 9, 2026**.

| Agent | Supported values in cakit | Mapping in cakit |
| --- | --- | --- |
| `claude` | `low`, `medium`, `high`, `max` | Sets `CLAUDE_CODE_EFFORT_LEVEL=<value>` for `claude` CLI |
| `codex` | `minimal`, `low`, `medium`, `high`, `xhigh` | Adds `-c model_reasoning_effort=<value>` to `codex exec` |
| `cursor` | Not supported | No reasoning-effort or thinking toggle is documented in cursor-agent CLI docs |
| `copilot` | Not supported | No reasoning-effort or thinking toggle is documented in Copilot CLI docs |
| `gemini` | Not supported | No reasoning-effort or thinking toggle is documented in Gemini CLI headless flags |
| `kimi` | `thinking`, `none` | Adds `--thinking` / `--no-thinking` to `kimi` |
| `qwen` | Not supported in `cakit run` | Qwen docs expose `model.generationConfig.extra_body.enable_thinking` in settings, but no documented per-run headless flag |
| `openhands` | Not supported | No per-run reasoning-effort flag is documented in OpenHands headless CLI docs |
| `swe-agent` | Not supported | No per-run reasoning-effort flag is documented in SWE-agent CLI docs |
| `trae-oss` | Not supported | No per-run reasoning-effort flag is documented in Trae CLI docs |
