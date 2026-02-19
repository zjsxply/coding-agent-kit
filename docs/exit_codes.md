# Exit Codes

This document defines cakit command exit code behavior based on current implementation in `src/cli.py` and `src/agents/base.py`.

## Global Conventions

- `0`: command succeeded.
- `1`: command failed at runtime or strict validation failed.
- `2`: invalid user input/unsupported option (usage-level error).
- `>=3` / other non-zero values: usually propagated from upstream coding agent command exit codes (mainly in `cakit run`) or delegated subprocesses (mainly in `cakit skills`).
- `127`: command executable not found in a spawned process (propagated in `cakit run` when applicable).

`argparse` errors (for example missing required positional arguments) exit with `2`.

## `cakit install`

- `0`: requested agent(s) installed successfully.
- `1`: one or more target agents failed to install.
- `2`: unsupported agent selector/name.

## `cakit configure`

- `0`: configure completed for target agent(s), including no-op configure (`config_path: null`).
- `2`: unsupported agent selector/name.

## `cakit run`

- `0`: command succeeded and strict stats validation passed.
- `1`: install bootstrap failed, strict stats validation failed, or internal run result missing `cakit_exit_code`.
- `2`: input/option validation failure before run, including:
  - empty prompt
  - image/video file not found
  - `--env-file` invalid path or not a file
  - unsupported/invalid `--reasoning-effort`
  - unsupported image/video modality for the target coding agent
- Other non-zero values:
  - if upstream coding agent command exits non-zero, cakit returns the same exit code.
  - if spawned command binary is not found, this may surface as `127`.

Strict stats validation for command-success runs requires:
- non-empty `response`
- non-empty `models_usage`
- `llm_calls >= 1`
- `tool_calls >= 0`

If any of the above is missing/invalid, cakit exits with `1`.

## `cakit skills`

- `0`: delegated `npx skills`/`npm exec -- skills` succeeded.
- `1`: dependency/setup failure before delegation (for example Node.js/npm missing and auto-install failed).
- Other non-zero values: delegated process exit code from `npx skills` (or `npm exec -- skills`).

## `cakit tools`

- `0`: tool installation flow completed successfully.
- `1`: unsupported platform/prerequisites or any install step failed.

## `cakit env`

- `0`: selected env template (`.env.template` for `--lang en`, `.env.template.zh` for `--lang zh`) successfully written to the target output path.
- `1`: selected env template file not found.
