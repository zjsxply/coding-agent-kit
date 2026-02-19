# TRAE CLI (trae.cn)

This document describes how `cakit` runs official `traecli` from `trae.cn` and extracts run stats.

## Auth

- OAuth or API.
- API-mode env vars:
  - `CAKIT_TRAE_CN_API_KEY` (fallback: `OPENAI_API_KEY`)
  - `CAKIT_TRAE_CN_BASE_URL` (fallback: `OPENAI_BASE_URL`)
  - `CAKIT_TRAE_CN_MODEL` (fallback: `OPENAI_DEFAULT_MODEL`)
  - optional: `CAKIT_TRAE_CN_MODEL_NAME` (default `cakit-openai`)
  - optional: `CAKIT_TRAE_CN_BY_AZURE` (`1/true` to enable Azure-compatible request shape)

## Install

- `cakit install trae-cn`:
  - resolves latest version from `trae-cli_latest_version.txt`
  - downloads `trae-cli_<version>_<os>_<arch>.tar.gz` from `lf-cdn.trae.com.cn`
  - installs under `~/.local/share/cakit/trae-cn/<version>/trae-cli`
  - creates symlink `~/.local/bin/traecli`
- `cakit install trae-cn --version <value>` installs the specified version package.

## Config and run

- cakit writes config to:
  - `~/.config/cakit/trae-cn/trae_cli/trae_cli.yaml`
- cakit runs `traecli` with isolated config root:
  - `XDG_CONFIG_HOME=~/.config/cakit/trae-cn`
- `cakit run trae-cn` calls:
  - `traecli --print --json --yolo <prompt>`
- Model priority is: `--model` > `CAKIT_TRAE_CN_MODEL` > `OPENAI_DEFAULT_MODEL`.

## Stats extraction

- Source of truth: JSON payload from `--print --json`.
- `models_usage`:
  - strict path: top-level `token_usage.prompt_tokens`, `token_usage.completion_tokens`, `token_usage.total_tokens`
- `llm_calls`:
  - number of assistant-role messages in `agent_states[*].messages[*]`
- `tool_calls`:
  - sum of `agent_states[*].messages[*].tool_calls` lengths
- model name:
  - top-level `model`
- `response`:
  - latest non-empty assistant `content` in `agent_states[*].messages[*]`
  - fallback: last non-empty stdout line

## Exit code policy

- cakit uses strict run validation:
  - non-empty `models_usage`
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - non-empty `response`
  - non-empty `trajectory_path`
- Missing required fields returns non-zero `exit_code`.

## Media input

- `traecli` has no generic `--image` / `--video` flags.
