# Copilot

## Auth

- OAuth: run `copilot`, then `/login`.
- `GH_TOKEN` / `GITHUB_TOKEN` can be used as GitHub auth tokens (Copilot Requests permission), but Copilot is not treated as an API-mode coding agent in cakit.

## Model Selection

- `cakit run copilot --model <name>` applies per-run model override.
- `COPILOT_MODEL` is also supported.

## Media Input

- `cakit run copilot --image <path>` is supported through natural-language path injection.
- cakit injects absolute local image paths into the prompt and asks Copilot to open files with available tools.
- `cakit run copilot --video <path>` is treated as unsupported.

## Stats Extraction

- cakit runs Copilot with `--log-level debug` and parses model-call payloads from run-local `--log-dir` logs.
- `models_usage`, `llm_calls`, and `tool_calls` are extracted from those payloads.
- If required stats are missing on an otherwise successful command, cakit returns a non-zero run status.
