# Cursor Agent (cakit)

This document explains how cakit installs and runs Cursor Agent CLI (`cursor-agent`).

## Install

`cakit install cursor` uses Cursor's official install script:

```bash
curl -fsS https://cursor.com/install | bash
```

- Default install (no `--version`) installs the latest upstream build.
- Versioned install is supported:

```bash
cakit install cursor --version <cursor_build_id>
```

When a version is specified, cakit downloads the matching Cursor agent package and updates `~/.local/bin/cursor-agent` symlink.

## Configure

`cakit configure cursor` is a no-op (`config_path: null`).

## Run Behavior

`cakit run cursor "<prompt>"` runs:

```bash
cursor-agent -p "<prompt>" --print --output-format stream-json --force
```

- Optional model override: `cakit run cursor --model <model>`
- Model priority: `--model` > `CURSOR_MODEL` > `OPENAI_DEFAULT_MODEL`
- Optional API endpoint override: `CURSOR_API_BASE` (fallback: `OPENAI_BASE_URL`)
- API key: `CURSOR_API_KEY` (fallback: `OPENAI_API_KEY`)

Image/video flags are not supported in cakit for Cursor (`--image` / `--video` return unsupported).

## Stats Extraction

cakit parses stream JSON output and extracts:
- `models_usage` from usage-like fields in payloads
- `tool_calls` by counting tool-like events in payloads
- `response` from assistant/final message fields (stdout fallback)

`trajectory_path` points to a YAML-formatted, human-readable trace converted from run output.
