# Source Matrix

This file maps each cakit agent to the upstream history source that should be used to derive an installable historical version.

## npm Package History

| Agent | Install selector | Primary history source | Notes |
| --- | --- | --- | --- |
| `auggie` | npm version | `@augmentcode/auggie` registry `time` | Prefer plain semver only |
| `codex` | npm version | `@openai/codex` registry `time` | Exclude platform-suffixed builds like `-linux-x64` |
| `codebuddy` | npm version | `@tencent-ai/codebuddy-code` registry `time` | Exclude `-next.*` prereleases unless explicitly requested |
| `continue` | npm version | `@continuedev/cli` registry `time` | Installer wraps npm install |
| `crush` | npm version | `@charmland/crush` registry `time` | Plain semver works |
| `gemini` | npm version | `@google/gemini-cli` registry `time` | Exclude nightly / preview when building stable timepoints |
| `kilocode` | npm version | `@kilocode/cli` registry `time` | Plain semver works |
| `openclaw` | npm version | `openclaw` registry `time` | Installer resolves versions through npm |
| `qoder` | npm version | `@qoder-ai/qodercli` registry `time` | Do not use the current shell manifest `latest` field for historical work |
| `qwen` | npm version | `@qwen-code/qwen-code` registry `time` | Exclude nightly / preview when building stable timepoints |

## PyPI Release History

| Agent | Install selector | Primary history source | Notes |
| --- | --- | --- | --- |
| `aider` | PyPI version | `aider-chat` release upload times | Prefer plain versions |
| `deepagents` | PyPI version | `deepagents-cli` release upload times | Prefer plain versions |
| `kimi` | PyPI version | `kimi-cli` release upload times | Prefer plain versions |
| `openhands` | PyPI version | `openhands` release upload times | Prefer plain versions |

## GitHub Releases

| Agent | Install selector | Primary history source | Notes |
| --- | --- | --- | --- |
| `copilot` | release version | `github/copilot-cli` releases | cakit can pass plain `0.0.420`; upstream installer adds `v` |
| `goose` | release version | `block/goose` releases | cakit normalizes to `v` internally |
| `opencode` | release version | `anomalyco/opencode` releases | Pass plain numeric version; installer strips/adds `v` |
| `swe-agent` | git tag | `SWE-agent/SWE-agent` releases | Keep the leading `v` tag |

## Git Ref / Commit

| Agent | Install selector | Primary history source | Notes |
| --- | --- | --- | --- |
| `trae-oss` | commit hash or git ref | `bytedance/trae-agent` commit history | Use exact commit on/before cutoff when no matching release stream exists |

## CDN / Installer-Managed Binaries

| Agent | Install selector | Primary history source | Method |
| --- | --- | --- | --- |
| `claude` | exact Claude Code version | public GCS bucket `claude-code-releases/` | List bucket objects via JSON API and take the newest version whose objects were created on/before cutoff |
| `factory` | release version | `downloads.factory.ai/factory-cli/releases/<version>/...` | Probe candidate versions with `HEAD` and compare `Last-Modified` |
| `trae-cn` | exact version | `lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_<version>_<os>_<arch>.tar.gz` | Probe candidate versions with `HEAD` and compare `Last-Modified` |
| `cursor` | build ID in cakit today; public release on website | `cursor.com/changelog/*`, `cursor.com/download`, current installer | Public release date is easy to confirm; exact agent build ID may require an extra translation step |

## Cursor Caveat

`cursor` is the one agent where the public product release (`2.5`, `2.6`) and the direct agent installer build ID (`2026.03.11-6dfa30c`) are different selectors.

Use this sequence:

1. Confirm the public release active at the target date from `cursor.com/changelog/*`.
2. If cakit still requires a build ID, treat the row as `inferred` until the build-ID mapping is confirmed.
3. If cakit later supports public Cursor release selectors directly, update the snapshot rows to `confirmed`.

## Recording Rule

When a version is partially known but not yet usable as an exact cakit install selector, save it anyway with:

- `status = inferred` for a likely upstream release value
- `status = pending` when no reliable selector is known yet

This keeps research progress durable between turns and prevents repeated re-discovery.
