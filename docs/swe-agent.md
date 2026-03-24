# SWE-agent (cakit)

This document describes how `cakit` runs SWE-agent CLI and extracts run stats.

## Auth

- API only.
- Required env vars:
  - `SWE_AGENT_API_KEY` (fallback: `OPENAI_API_KEY`)
  - `SWE_AGENT_BASE_URL` (fallback: `OPENAI_BASE_URL`)
  - `SWE_AGENT_MODEL` (fallback: `OPENAI_DEFAULT_MODEL`)

## Install

- `cakit install swe-agent` resolves the latest upstream release tag, then installs that git ref with `uv tool install`.
- cakit installs the upstream CLI in a Python 3.12 `uv tool` environment and preinstalls `pip`, `tree-sitter==0.21.3`, and `tree-sitter-languages` so the upstream official `edit_anthropic` bundle can run in local deployment mode.
- If `uv` is unavailable, cakit falls back to `pip install` for the same git ref.
- `cakit install swe-agent --version <tag_or_plain_version>` installs the specified upstream git tag with the same flow. Plain semver such as `1.1.0` is normalized to the upstream `v1.1.0` tag internally.
- cakit also prepares runtime assets (`config/`, `tools/`, `trajectories/`) under `~/.cache/cakit/swe-agent-assets/<resolved_tag>` and passes:
  - `SWE_AGENT_CONFIG_DIR`
  - `SWE_AGENT_TOOLS_DIR`
  - `SWE_AGENT_TRAJECTORY_DIR`

## Run behavior

- cakit runs `sweagent run` with local deployment mode:
  - `--env.deployment.type=local`
  - `--env.repo.type=local`
  - `--problem_statement.text <prompt>`
- If the installed `sweagent run` supports `--output_dir`, cakit passes a run-local output directory and reads `.traj` files from there.
- Model priority is: `--model` > `SWE_AGENT_MODEL` > `OPENAI_DEFAULT_MODEL`.
- cakit deep-copies the upstream official `config/default.yaml` agent defaults when generating the run config, then injects the resolved model/API settings and rewrites tool bundle paths to cakit-managed runtime assets.
- If current `--cwd` is inside a clean git repo, cakit passes the repo root to SWE-agent instead of just the current subdirectory.
- If current `--cwd` is inside a dirty git repo, cakit clones the repo to `/tmp`, overlays the current uncommitted worktree changes, creates a temporary snapshot commit, and runs SWE-agent against that clean snapshot.
- If current `--cwd` is not a git repo, cakit creates a temporary git repo under `/tmp` and uses that path.
- cakit writes a cakit-managed config to `~/.config/sweagent/config.yaml` and passes it with `--config`.
- When a base URL is configured, cakit writes it to `agent.model.api_base` and also forwards `OPENAI_BASE_URL` to the child process.

## Stats extraction

- Source of truth: `.traj` files written to the run `--output_dir` when the installed CLI supports that flag.
- `models_usage`:
  - `prompt_tokens = info.model_stats.tokens_sent`
  - `completion_tokens = info.model_stats.tokens_received`
  - `total_tokens = prompt + completion`
- `llm_calls`: `info.model_stats.api_calls`
- `tool_calls`: number of non-empty `action` entries in `trajectory` (sum across `attempts[*].trajectory` for retry runs).
- `response`:
  - latest non-empty text from the last non-`submit` trajectory step, preferring `observation`, then `response`, then `thought`
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
