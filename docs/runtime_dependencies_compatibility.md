# Runtime Dependencies and Compatibility

This document summarizes how `cakit install` currently models installer/runtime dependencies and which install method each supported coding agent uses.

This first version focuses on install methods and installer-side compatibility. Run-time auth, media, and behavior compatibility can be expanded later.

The content below reflects the current source tree on March 14, 2026.

## Terms

- Modeled runtime dependency: a runtime/tool that cakit will try to resolve before installation starts. This includes agent-declared runtimes and strategy-inferred ones such as `node` for `npm`, `uv` for `uv_tool`, and `bash` for `shell`.
- Operational installer requirement: tools the selected install path still needs in practice, such as `curl`, `tar`, `sha256sum`, `unzip`, or a supported OS/CPU pair.

## Shared Behavior

- `npm` installs are the only installs currently affected by `--scope`.
- `--scope user` installs npm-based agents under `~/.npm-global`.
- `--scope global` runs a system-level `npm install -g`.
- For the current non-npm agents, `--scope` is effectively ignored.
- Some agents declare an ordered fallback list such as `shell` then `npm`; for those agents, `--scope` only matters if the npm fallback path is actually used.
- On Linux, cakit can auto-install modeled dependencies for `node`, `uv`, and a fixed set of system tools: `bash`, `bzip2`, `curl`, `git`, `gzip`, and `tar`.
- If an install path still needs tools outside that modeled set, the host environment must provide them.
- `uv_tool` prefers `uv tool install`; if `uv` still cannot be used, the shared helper can fall back to `python -m pip install`.
- `uv_pip` exists in the shared installer layer, but no current coding agent uses it yet.
- An agent can also declare an ordered list of install strategies; cakit tries them in order and stops at the first success.

## Install Strategy Kinds

| Strategy kind | Current usage | Typical upstream artifact | Modeled runtime dependency | `--scope` behavior | Notes |
| --- | --- | --- | --- | --- | --- |
| `npm` | 9 primary agents, plus fallback for some shell-first agents | npm package | inferred `node` | supported | Default user-scope prefix is `~/.npm-global` |
| `uv_tool` | 5 agents | Python package or Git URL | inferred `uv` | ignored | Some agents pin a Python version or add extra packages |
| `uv_pip` | 0 agents | Python package list | inferred `uv` | ignored | Implemented, currently unused |
| `shell` | 5 primary agents; 4 are shell-first with npm fallback | Official install script | inferred `bash`, plus any per-agent extras | ignored on the script path; npm fallback honors it | Most shell installers also need extra host tools such as `curl`/`tar`; some of those are modeled per agent, some are not |
| `custom` | 4 agents | Mixed: official script, binary archive, or agent-specific `uv` flow | varies by agent | ignored in the current agent set | Used when shared strategies do not match upstream packaging |

## Npm-Based Installs

All agents in this section:

- model `node` as a runtime dependency in cakit
- use npm package versions/tags for `cakit install <agent> --version ...`
- honor `--scope user|global`

| Agent | Upstream package | Notes |
| --- | --- | --- |
| `auggie` | `@augmentcode/auggie` | — |
| `codebuddy` | `@tencent-ai/codebuddy-code` | — |
| `codex` | `@openai/codex` | — |
| `continue` | `@continuedev/cli` | Upstream also has a shell installer, but cakit currently keeps npm install here because the versioned npm path is stable and already tested |
| `crush` | `@charmland/crush` | — |
| `gemini` | `@google/gemini-cli` | — |
| `kilocode` | `@kilocode/cli` | — |
| `qoder` | `@qoder-ai/qodercli` | Upstream now also exposes `https://qoder.com/install`, but cakit currently keeps npm install here because the package-version path is already tested |
| `qwen` | `@qwen-code/qwen-code` | — |

## Uv-Based Installs

All agents in this section:

- model `uv` as a runtime dependency in cakit
- ignore `--scope`
- use the shared `uv_tool` installer path

| Agent | Package or ref | Version style in cakit | Notes |
| --- | --- | --- | --- |
| `aider` | `aider-chat` | PEP 440 version | Requests Python `3.12`; uses force reinstall |
| `deepagents` | `deepagents-cli` | PEP 440 version | Requests Python `3.12`; uses force reinstall |
| `openhands` | `openhands` | PEP 440 version | Requests Python `3.12` |
| `swe-agent` | `git+https://github.com/SWE-agent/SWE-agent` | Git ref / release tag | Install flow resolves latest upstream release when `--version` is omitted; plain semver selectors are normalized to upstream `v` tags |
| `trae-oss` | `git+https://github.com/bytedance/trae-agent.git` | Git ref | Requests Python `3.12`; adds `docker`, `pexpect`, and `unidiff`; installed-version reporting returns the resolved git revision from uv metadata |

## Shell Installer

Here, `shell` means the cakit install entrypoint is an upstream shell installer script.

- These scripts do not all install the same way: some download and verify prebuilt binaries, some download archives and extract them, and some scripts internally still perform npm-based installation.
- cakit always models `bash` for this strategy. Additional modeled dependencies below reflect the current agent declarations in this repo, not the full superset of everything an upstream script might probe for.
- Some shell-first agents also declare a direct npm fallback. For those agents, modeled dependencies include both the script-path requirements and `node`, and `--scope` only matters if the fallback path is used.
- This section records both the cakit entrypoint and the installer behavior verified from the current upstream scripts on March 14, 2026.

| Agent | Default install path | Versioned install path | Modeled runtime dependency | Operational installer requirements | Notes |
| --- | --- | --- | --- | --- | --- |
| `claude` | `curl -fsSL https://claude.ai/install.sh | bash` | Same script, with the selector passed as `bash -s -- <value>` | `bash`, `curl`, `node` | `bash`, `curl`, and a SHA256 tool (`sha256sum` or `shasum`) | The script downloads a platform-specific `claude` standalone binary from a GCS bucket, reads `manifest.json`, verifies SHA256, then runs the downloaded binary's `install` subcommand to set up launcher/shell integration; if that script path fails, cakit falls back to `npm install -g @anthropic-ai/claude-code` |
| `copilot` | `curl -fsSL https://gh.io/copilot-install | bash` | Same installer, with `VERSION=<value>` exported for the `bash` process | `bash`, `curl`, `tar`, `node` | `bash`, `curl`/`wget`, `tar` | The current working installer downloads a release `tar.gz`, optionally validates `SHA256SUMS.txt`, then extracts `copilot` into `PREFIX/bin`; if that script path fails, cakit falls back to `npm install -g @github/copilot` |
| `goose` | Official download script from GitHub releases | Same script, with `GOOSE_VERSION` injected by cakit | `bash`, `bzip2`, `curl`, `tar`, `libxcb`, `libgomp` | `bash`, `curl`, plus `tar` on Linux/macOS or `unzip`/PowerShell on Windows | The script downloads a release archive (Linux/macOS: `.tar.bz2`; Windows: `.zip`), extracts the `goose`/`goose.exe` binary into a bin dir, then optionally runs `goose configure`. Current Linux binaries also dynamically require `libxcb` and `libgomp`, and versioned installs only work for release tags that still publish the matching platform archive |
| `openclaw` | `curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard` | Same script, with `--no-onboard --version <value>` passed via `bash -s -- --no-onboard --version <value>` | `bash`, `curl`, `git`, `node`, `python3`, `make`, `g++`, `cmake` | `bash`, `curl`/`wget`; the default path also needs Node.js / npm / git plus a native build toolchain (`python3`, `make`, `g++`, `cmake >= 3.19`) when `node-llama-cpp` falls back to source builds, and the source path also needs `pnpm` | The default install path is still npm-backed internally: the script prepares Node / npm and runs `npm install -g openclaw@...`. cakit disables upstream onboarding during install so non-interactive environments do not fail on `/dev/tty`; if the host distro only provides an older `cmake`, cakit supplements it with a newer user-local `cmake` before install. If the script install path still fails, cakit falls back to direct npm installation. The same installer also supports `--install-method git`, which clones the source repo and builds it with `pnpm` |
| `opencode` | Wrapped `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path` | Same wrapped script, with `--no-modify-path --version <value>` passed via `bash -s -- --no-modify-path --version <value>` | `bash`, `curl`, `tar`, `which`, `node` | `bash`, `curl`, `tar` on Linux, `unzip` on other platforms | The script downloads a platform release archive from GitHub Releases, extracts the `opencode` binary, and installs it under `~/.opencode/bin`. cakit models `which` as a runtime dependency and auto-installs it through the host package manager when missing, while also disabling the upstream PATH file edits with `--no-modify-path`; if that script path fails, cakit falls back to `npm install -g opencode-ai` |

## Custom Installers

These agents use `kind="custom"` because their upstream packaging does not fit one shared install template cleanly.

| Agent | Default install path (`--version` omitted) | Versioned install path | Modeled runtime dependency | Operational installer requirements | Compatibility notes |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `curl -fsS https://cursor.com/install | bash` | Download versioned `agent-cli-package.tar.gz`, extract it, then update `~/.local/bin/agent` and `~/.local/bin/cursor-agent` symlinks | none | Default path needs `bash` + `curl`; versioned path also needs archive download/extract support | Versioned install path currently hardcodes Linux/Darwin and `x64`/`arm64` |
| `factory` | `curl -fsSL https://app.factory.ai/cli | sh` | Download versioned `droid` and `rg` binaries, verify SHA256, then install to `~/.local/bin/droid` and `~/.factory/bin/rg` | `node` | Default path needs `sh` + `curl`; versioned path needs direct binary download support | Versioned install path currently supports Linux/Darwin and `x64`/`arm64`; on `x64`, cakit switches to a `-baseline` build when AVX2 is unavailable |
| `kimi` | `curl -LsSf https://code.kimi.com/install.sh | bash` | Agent-specific `uv tool install kimi-cli==<version>` flow | `uv` | Default path needs `bash` + `curl`; versioned path needs working `uv`/Python install path | Although `kimi` is `custom`, its versioned install path is effectively uv-based |
| `trae-cn` | Resolve latest version, download versioned tarball from the trae.cn CDN, extract it, then link `~/.local/bin/traecli` | Same binary-tarball path, but with the requested version instead of "latest" | none | `curl`, `tar`, writable install directory | Current versioned install path supports Linux/Darwin and `amd64`/`arm64` only |

## Current Compatibility Summary

- `npm` remains the most common install path: 9 of 23 supported coding agents.
- Pure shared `uv_tool` installs cover 5 agents, and `kimi` adds one more uv-backed custom versioned path.
- Shell entrypoints are currently used for `claude`, `copilot`, `goose`, `openclaw`, and `opencode`.
- cakit can auto-resolve modeled `node`/`uv` dependencies and a small fixed set of system tools on Linux, but installer requirements outside that set still have to be satisfied by the host environment.
