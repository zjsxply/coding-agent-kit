# 运行依赖与兼容性

本文汇总 `cakit install` 当前如何建模安装侧运行依赖，以及每个已支持 coding agent 采用哪种安装方式。

这一版先聚焦安装方式和安装器侧兼容性。运行时鉴权、多模态、行为差异等兼容性内容后续可以继续补充。

以下内容反映的是当前仓库源码在 2026 年 3 月 14 日的实现状态。

## 术语

- 建模的运行时依赖：`cakit install` 会在开始安装 agent 之前先尝试补齐的运行时/工具。它既包括 agent 显式声明的依赖，也包括安装策略自动推断出的依赖，例如 `npm` 推断 `node`、`uv_tool` 推断 `uv`、`shell` 推断 `bash`。
- 安装器实际要求：某条安装路径在实际执行时仍然需要的工具或平台条件，例如 `curl`、`tar`、`sha256sum`、`unzip`，或特定的操作系统/CPU 组合。

## 共享行为

- 当前只有 `npm` 安装会受到 `--scope` 影响。
- `--scope user` 会把 npm 类 agent 安装到 `~/.npm-global`。
- `--scope global` 会执行系统级 `npm install -g`。
- 对当前这批非 npm agent，`--scope` 实际上会被忽略。
- 部分 agent 会声明按顺序尝试的回退列表，例如先 `shell` 再 `npm`；对这些 agent 来说，`--scope` 只有在实际走到 npm 回退路径时才会生效。
- 在 Linux 上，cakit 目前可自动补齐建模过的 `node`、`uv`，以及一小组固定系统工具：`bash`、`bzip2`、`curl`、`git`、`gzip`、`tar`。
- 如果某条安装路径还依赖这组之外的工具，仍需宿主环境自行提供。
- `uv_tool` 会优先走 `uv tool install`；如果最终仍无法使用 `uv`，共享安装器会退回到 `python -m pip install`。
- 共享安装层里已经实现了 `uv_pip`，但当前没有任何 coding agent 在使用它。
- agent 也可以声明一个按顺序尝试的安装策略列表；cakit 会依次尝试并在第一个成功处停止。

## 安装策略类型

| 策略类型 | 当前使用情况 | 上游产物类型 | 建模的运行时依赖 | `--scope` 行为 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `npm` | 9 个主路径 agent，另有若干 shell-first agent 把它作为回退 | npm 包 | 自动推断 `node` | 支持 | 默认 user-scope 前缀为 `~/.npm-global` |
| `uv_tool` | 5 个 agent | Python 包或 Git URL | 自动推断 `uv` | 忽略 | 部分 agent 会指定 Python 版本或附加依赖 |
| `uv_pip` | 0 个 agent | Python 包列表 | 自动推断 `uv` | 忽略 | 已实现，当前未使用 |
| `shell` | 5 个主路径 agent，其中 4 个是 shell-first 并带 npm 回退 | 官方安装脚本 | 自动推断 `bash`，再叠加各 agent 额外声明 | 脚本路径忽略；npm 回退路径会生效 | 多数 shell 安装器还依赖 `curl`/`tar` 等主机工具；其中一部分已在 agent 侧建模，一部分没有 |
| `custom` | 4 个 agent | 混合：官方脚本、二进制包、或 agent 专属 `uv` 流程 | 取决于 agent | 当前这批 agent 中均忽略 | 用于上游打包方式不适合套共享模板的情况 |

## Npm 安装

本节所有 agent 都满足：

- 在 cakit 中建模 `node` 作为运行时依赖
- `cakit install <agent> --version ...` 使用 npm 包版本号或 tag
- 支持 `--scope user|global`

| Agent | 上游包名 | 说明 |
| --- | --- | --- |
| `auggie` | `@augmentcode/auggie` | — |
| `codebuddy` | `@tencent-ai/codebuddy-code` | — |
| `codex` | `@openai/codex` | — |
| `continue` | `@continuedev/cli` | 上游现在也提供 shell 安装脚本，但 cakit 当前仍保留已测试的 npm 版本安装路径 |
| `crush` | `@charmland/crush` | — |
| `gemini` | `@google/gemini-cli` | — |
| `kilocode` | `@kilocode/cli` | — |
| `qoder` | `@qoder-ai/qodercli` | 上游现在也提供 `https://qoder.com/install`，但 cakit 当前仍保留已测试的 npm 版本安装路径 |
| `qwen` | `@qwen-code/qwen-code` | — |

## Uv 安装

本节所有 agent 都满足：

- 在 cakit 中建模 `uv` 作为运行时依赖
- 忽略 `--scope`
- 使用共享的 `uv_tool` 安装路径

| Agent | 包名或引用 | cakit 中的版本语义 | 说明 |
| --- | --- | --- | --- |
| `aider` | `aider-chat` | PEP 440 版本 | 指定 Python `3.12`；启用 force reinstall |
| `deepagents` | `deepagents-cli` | PEP 440 版本 | 指定 Python `3.12`；启用 force reinstall |
| `openhands` | `openhands` | PEP 440 版本 | 指定 Python `3.12` |
| `swe-agent` | `git+https://github.com/SWE-agent/SWE-agent` | Git ref / release tag | 未传 `--version` 时，安装流程会先解析上游最新 release；纯 semver selector 会规范化为上游 `v` 前缀 tag |
| `trae-oss` | `git+https://github.com/bytedance/trae-agent.git` | Git ref | 指定 Python `3.12`；额外安装 `docker`、`pexpect`、`unidiff`；已安装版本回报会返回 uv 元数据中的解析后 git revision |

## Shell 安装器

这里的 `shell` 指的是 cakit 的安装入口：直接执行上游提供的 shell 安装脚本。

- 这些脚本的内部安装链路并不统一：有些脚本会下载并校验预编译二进制，有些脚本会下载归档再解压，有些脚本内部依然会走 npm 安装。
- 对这类策略，cakit 一定会建模 `bash`；下表里的“建模的运行时依赖”还会额外列出当前仓库里该 agent 显式声明的依赖，但这仍不等于上游脚本会探测到的全部工具集合。
- 部分 shell-first agent 还声明了直接 npm 回退；对这些 agent，建模依赖会同时包含脚本路径要求和 `node`，而 `--scope` 只有在回退路径真的触发时才有意义。
- 因此，本节既列出 cakit 当前调用的入口，也记录我在 2026 年 3 月 14 日根据当前上游脚本确认到的“脚本内部怎么装”。

| Agent | 默认安装路径 | 指定版本安装路径 | 建模的运行时依赖 | 安装器实际要求 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `claude` | `curl -fsSL https://claude.ai/install.sh | bash` | 仍使用同一脚本，但把版本选择器作为 `bash -s -- <value>` 传入 | `bash`、`curl`、`node` | `bash`、`curl`、校验 SHA256 的工具（`sha256sum` 或 `shasum`） | 脚本会先从 GCS bucket 下载对应平台的 `claude` 单文件二进制，读取 `manifest.json` 校验 SHA256，然后执行下载下来的 `claude install` 完成 launcher / shell integration；如果脚本路径失败，cakit 会回退到 `npm install -g @anthropic-ai/claude-code` |
| `copilot` | `curl -fsSL https://gh.io/copilot-install | bash` | 仍使用同一安装器，但通过 `VERSION=<value>` 传给 `bash` 进程 | `bash`、`curl`、`tar`、`node` | `bash`、`curl`/`wget`、`tar` | 当前可用安装器会下载 release `tar.gz`、可选校验 `SHA256SUMS.txt`、再把 `copilot` 解压到 `PREFIX/bin`；如果脚本路径失败，cakit 会回退到 `npm install -g @github/copilot` |
| `goose` | 使用 GitHub Releases 提供的官方下载脚本 | 仍使用同一脚本，但由 cakit 注入 `GOOSE_VERSION` | `bash`、`bzip2`、`curl`、`tar`、`libxcb`、`libgomp` | `bash`、`curl`、Linux/macOS 需要 `tar`，Windows 需要 `unzip`/PowerShell | 脚本会按平台下载 release 归档（Linux/macOS 为 `.tar.bz2`，Windows 为 `.zip`），解压出 `goose`/`goose.exe` 放到 bin 目录，然后可选执行 `goose configure`。当前 Linux 二进制还会动态依赖 `libxcb`、`libgomp`；而且只有目标 release tag 仍发布了当前平台对应归档时，指定版本安装才会成功 |
| `openclaw` | `curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard` | 仍使用同一脚本，但通过 `bash -s -- --no-onboard --version <value>` 传入 `--no-onboard --version` | `bash`、`curl`、`git`、`node`、`python3`、`make`、`g++`、`cmake` | `bash`、`curl`/`wget`；默认路径还需要 Node.js / npm / git，且在 `node-llama-cpp` 回退源码构建时还需要原生编译链（`python3`、`make`、`g++`、`cmake >= 3.19`）；源码路径还需要 `pnpm` | 默认安装路径在脚本内部仍然是 npm-backed：脚本会准备好 Node / npm 后执行 `npm install -g openclaw@...`。cakit 会在安装阶段禁用上游 onboarding，避免非交互环境因为 `/dev/tty` 失败；如果宿主发行版自带的 `cmake` 太旧，cakit 会先补一个用户态的新 `cmake` 再继续安装。若脚本路径仍失败，cakit 会回退到直接用 npm 安装。同一个脚本还支持 `--install-method git`，会 `git clone` 源码并用 `pnpm` 构建 |
| `opencode` | 包装后的 `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path` | 仍使用同一包装脚本，但通过 `bash -s -- --no-modify-path --version <value>` 传入参数 | `bash`、`curl`、`tar`、`which`、`node` | `bash`、`curl`、Linux 需要 `tar`，其他平台需要 `unzip` | 脚本会从 GitHub Releases 下载对应平台归档，解压出 `opencode` 二进制并安装到 `~/.opencode/bin`。cakit 会把 `which` 建模为运行时依赖，并在缺失时通过宿主机包管理器自动安装；同时继续通过 `--no-modify-path` 禁用上游 PATH 文件改写；如果脚本路径失败，cakit 会回退到 `npm install -g opencode-ai` |

## Custom 安装器

这些 agent 使用 `kind="custom"`，因为它们的上游分发方式不适合直接套用某个共享安装模板。

| Agent | 默认安装路径（未传 `--version`） | 指定版本安装路径 | 建模的运行时依赖 | 安装器实际要求 | 兼容性说明 |
| --- | --- | --- | --- | --- | --- |
| `cursor` | `curl -fsS https://cursor.com/install | bash` | 下载指定版本的 `agent-cli-package.tar.gz`，解压后更新 `~/.local/bin/agent` 和 `~/.local/bin/cursor-agent` 链接 | 无 | 默认路径需要 `bash` + `curl`；指定版本路径还需要下载和解压归档 | 当前指定版本安装路径只硬编码支持 Linux/Darwin 与 `x64`/`arm64` |
| `factory` | `curl -fsSL https://app.factory.ai/cli | sh` | 下载指定版本的 `droid` 和 `rg` 二进制，校验 SHA256 后安装到 `~/.local/bin/droid` 和 `~/.factory/bin/rg` | `node` | 默认路径需要 `sh` + `curl`；指定版本路径需要可直接下载二进制 | 当前指定版本安装路径支持 Linux/Darwin 与 `x64`/`arm64`；对 `x64`，若检测不到 AVX2，cakit 会切到 `-baseline` 构建 |
| `kimi` | `curl -LsSf https://code.kimi.com/install.sh | bash` | 走 agent 专属的 `uv tool install kimi-cli==<version>` 流程 | `uv` | 默认路径需要 `bash` + `curl`；指定版本路径需要可用的 `uv`/Python 安装链路 | 虽然 `kimi` 的策略类型是 `custom`，但它的指定版本安装路径本质上是 uv 安装 |
| `trae-cn` | 先解析 latest 版本，再从 trae.cn CDN 下载对应 tarball，解压后链接 `~/.local/bin/traecli` | 同一套二进制 tarball 流程，只是版本来自用户输入而不是 latest | 无 | `curl`、`tar`、可写安装目录 | 当前安装路径只支持 Linux/Darwin 与 `amd64`/`arm64` |

## 当前兼容性总结

- 当前最常见的安装路径仍然是 `npm`：23 个已支持 agent 中有 9 个使用它。
- 纯共享 `uv_tool` 安装覆盖 5 个 agent，再加上 `kimi` 的指定版本 uv 路径，可视为另有 1 条 uv-backed custom 路径。
- Shell 入口当前用于 `claude`、`copilot`、`goose`、`openclaw`、`opencode`。
- cakit 目前可以在 Linux 上自动补齐建模过的 `node`/`uv` 依赖，以及一小组固定系统工具；但超出这组范围的安装前提仍需宿主环境自行满足。
