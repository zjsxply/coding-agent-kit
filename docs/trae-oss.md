# Trae OSS (cakit)

This document describes how `cakit` runs `trae-cli` and extracts run stats.

## Auth

- API only.
- Required env vars:
  - `TRAE_AGENT_API_KEY`
  - `TRAE_AGENT_API_BASE`
  - `TRAE_AGENT_MODEL`

## Install

- `cakit install trae-oss --version <git_ref>` installs from `bytedance/trae-agent`.
- cakit installs runtime extras required by upstream imports:
  - `docker`
  - `pexpect`
  - `unidiff`

## Config and run

- cakit writes config to `~/.config/trae/config.yaml`.
- `cakit run trae-oss` calls:
  - `trae-cli run <prompt>`
  - `--working-dir <cwd>`
  - `--trajectory-file <path>`
  - `--config-file ~/.config/trae/config.yaml` (if present)
  - `--model <...>` when model is configured or overridden.

## Stats extraction

- Source of truth: trajectory JSON file.
- `models_usage`:
  - Sum all `llm_interactions[*].response.usage.input_tokens` as prompt tokens.
  - Sum all `llm_interactions[*].response.usage.output_tokens` as completion tokens.
  - `total_tokens = prompt + completion`.
- `llm_calls`: `len(llm_interactions)`.
- `tool_calls`: total length of `agent_steps[*].tool_calls` (missing is treated as zero for that step).
- Model name: trajectory top-level `model`.
- `response`:
  - `final_result`
  - fallback: latest non-empty `llm_interactions[*].response.content`
  - fallback: last non-empty stdout line
- `trajectory_path`: YAML-formatted trace from trajectory file; fallback to formatted raw output.

## Exit code policy

- cakit uses strict run validation for successful commands:
  - non-empty `models_usage`
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - non-empty `response`
- Missing required fields result in non-zero `exit_code`.

## Media input

- `trae-cli run` has no generic `--image` / `--video` flags.

