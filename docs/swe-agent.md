# SWE-agent (cakit)

This document describes how `cakit` runs SWE-agent CLI and extracts run stats.

## Auth

- API only.
- Required env vars:
  - `SWE_AGENT_API_KEY`
  - `SWE_AGENT_API_BASE`
  - `SWE_AGENT_MODEL`

## Install

- `cakit install swe-agent` installs latest upstream release tag at install time.
- `cakit install swe-agent --version <tag>` installs upstream release tarball.
- cakit also prepares runtime assets (`config/`, `tools/`, `trajectories/`) under `~/.cache/cakit/swe-agent-assets/<tag>` and passes:
  - `SWE_AGENT_CONFIG_DIR`
  - `SWE_AGENT_TOOLS_DIR`
  - `SWE_AGENT_TRAJECTORY_DIR`

## Run behavior

- cakit runs `sweagent run` with local deployment mode:
  - `--env.deployment.type=local`
  - `--env.repo.type=local`
  - `--problem_statement.text <prompt>`
- If current `--cwd` is not a git repo, cakit creates a temporary git repo under `/tmp` and uses that path.
- cakit writes a cakit-managed config to `~/.config/sweagent/config.yaml` and passes it with `--config`.

## Stats extraction

- Source of truth: latest `.traj` file in run `--output_dir`.
- `models_usage`:
  - `prompt_tokens = info.model_stats.tokens_sent`
  - `completion_tokens = info.model_stats.tokens_received`
  - `total_tokens = prompt + completion`
- `llm_calls`: `info.model_stats.api_calls`
- `tool_calls`: number of non-empty `action` entries in `trajectory` (sum across `attempts[*].trajectory` for retry runs).
- `response`:
  - latest non-empty text from trajectory steps (`response` / `thought` / `observation`)
  - fallback to `info.submission`
  - fallback to last non-empty stdout line
- `trajectory_path`: YAML-formatted trace from trajectory file; fallback to formatted raw output.

## Exit code policy

- cakit uses strict run validation:
  - non-empty `models_usage`
  - `llm_calls >= 1`
  - `tool_calls >= 0`
  - non-empty `response`
  - non-empty `trajectory_path`
- If any required field is missing on a command-success run, cakit returns non-zero `exit_code`.

## Media input

- `--image` / `--video` are not supported by `sweagent run`.
