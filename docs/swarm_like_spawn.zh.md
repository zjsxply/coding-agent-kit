# Swarm-like Spawn 支持说明

本文记录 cakit 已支持 coding agent 中，当前具备 swarm-like 运行时 agent spawn 能力的功能状态。

范围说明：
- 这里只统计运行时拉起 helper agent / subagent 的能力。
- 仅支持预定义 subagent 列表、不能运行时 spawn 的，不算在这里。
- 下文 `测试版本` 取自当前 README 测试覆盖矩阵。
- 状态基于我们在 2026-03-14 核对到的上游行为。

`cakit` 目前还没有统一的 multiagent 开关，需要分别使用各上游 coding agent 自己的开启方式。

| Agent | 测试版本 | 当前能力 | 默认状态 | 开启方式 |
| --- | --- | --- | --- | --- |
| kimi | 1.12.0 | 单次运行内的 Agent Swarm 风格 subagent 拉起 | 已开启 | 基本 swarm-like 用法无需额外设置 |
| claude | 2.1.72 | Agent Teams | 未默认开启 | 设置 `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1`，然后重启 Claude Code |
| codex | 0.114.0 | multi-agent | 未默认开启 | 在 `~/.codex/config.toml` 中加入 `[features]` / `multi_agent = true` |
| openclaw | 2026.3.8 | 主 agent 拉起 subagent | 已开启 | 基本 spawn 用法无需额外设置 |
| goose | 1.27.2 | `auto` 模式下自动使用 subagents | 已开启 | 通常无需操作；如果 mode 被改过，切回 `auto` |

## Trajectory 覆盖范围

| Agent | `trajectory_path` 当前覆盖范围 | 是否包含子 agent 轨迹 | 说明 |
| --- | --- | --- | --- |
| kimi | CLI stdout + 对应 session 的 `wire.jsonl`（若存在也包含 `context.jsonl`） | 是 | Kimi 的 subagent 事件记录在 wire 日志里 |
| claude | CLI stdout + 主 transcript + `subagents/` 下 child transcript | 是 | Agent Teams / runtime-created child transcript 会一起写入 |
| codex | CLI stdout + 主线程/子线程的 exact rollout family | 是 | rollout 通过 parent-thread 关系聚合 |
| openclaw | CLI stdout + run-local transcript family | 是 | 同一临时 `OPENCLAW_HOME` 下的全部 transcript 一起写入 |
| goose | CLI stdout + 主 session export + run-local SQLite snapshot + request logs | 是 | Goose 没有单一 transcript 文件，因此改为组合 run-local 产物 |

## 统计字段覆盖范围

在 swarm-like spawn 场景下，`models_usage`、`llm_calls`、`tool_calls` 只有在父/子 agent 运行产物真实存在且能被精确匹配时，才会做 family 聚合。`total_cost` 口径单独处理：cakit 不会根据 token usage 自行估算成本。

| Agent | `models_usage` | `llm_calls` | `tool_calls` | `total_cost` | 说明 |
| --- | --- | --- | --- | --- | --- |
| kimi | 是 | 是 | 是 | 否 | 从 run-local session family 聚合；Kimi 当前没有稳定的 family cost 字段，因此 `total_cost` 保持 `null` |
| claude | 是 | 是 | 是 | 仅取上游顶层汇总 | 从主 transcript + child transcript family 聚合；`total_cost` 当前直接取 Claude 顶层 `result.total_cost_usd`，cakit 不会再按 child transcript 单独重求和 |
| codex | 是 | 是 | 是 | 否 | 按 rollout family 聚合；上游没有稳定的 rollout-family cost 字段，因此 `total_cost` 保持 `null` |
| openclaw | 条件成立时是 | 条件成立时是 | 条件成立时是 | 否 | 只有 child session transcript 真实存在时，cakit 才能把它们聚合进 family totals；若 `sessions_spawn` 失败，则只能统计主 run 实际产生的记录 |
| goose | 是 | 是 | 是 | 否 | 从 run-local sessions、SQLite 状态和 request logs（含 `sub_agent`）聚合；上游没有稳定的 family cost 来源 |

## Kimi

当前测试版本：`1.12.0`

- Kimi 默认就支持基本的 Agent Swarm 用法。
- 对于常规 swarm-like 使用，不需要额外配置。
- prompt 可以直接写，例如：`launch multiple subagents`。
- 当 Kimi session 日志可用时，cakit 会把 subagent 事件聚合进 `models_usage`、`llm_calls` 和 `tool_calls`。
- 按我们当前测试观察，彼此独立的多个 Kimi 顶层会话并发运行时仍可能触发竞态，建议不要同时跑多个顶层 Kimi 会话。
- Kimi 另外还有一个单独的 `CreateSubagent` 能力，用于定义新的 subagent 类型；这和日常 Agent Swarm prompt 不是一回事，常规使用不需要它。

## Claude

当前测试版本：`2.1.72`

- Claude 当前对应的 swarm-like 功能是 `Agent Teams`。
- 按当前上游行为，它默认不是开启状态。
- 开启方式是设置 `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1`。
- 修改该环境变量后，需要重启 Claude Code 才会生效。
- 在 cakit 里，只要把 `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1` 写进 `.env` 并重新 `source`，cakit 就会原样透传。

## Codex

当前测试版本：`0.114.0`

- Codex 的 multi-agent 当前由上游配置文件控制，不是 cakit 自己的单独开关。
- 开启方式是在 `~/.codex/config.toml` 里加入：

```toml
[features]
multi_agent = true
```

- 需要保证当前 shell / session 实际使用的 Codex 配置里保留这段设置。
- 如果你想结合 cakit 的通用 post-config hook 自动补这段配置，可以这样做：

```bash
export CAKIT_CONFIGURE_POST_COMMAND='if [ "$CAKIT_CONFIGURE_AGENT" = "codex" ]; then printf "\n[features]\nmulti_agent = true\n" >> "$CAKIT_CONFIG_PATH"; fi'
cakit install codex
```

- 这个例子适合“fresh config / 一次性追加”的简单场景；如果你的 Codex 配置里已经有 `[features]` 段，应该直接编辑已有段落，而不是重复追加第二个 `[features]`。

## OpenClaw

当前测试版本：`2026.3.8`

- 对于“主 agent 拉起 worker”这种基本 swarm-like 场景，OpenClaw 默认已经可用。
- 如果只是主 agent spawn subagent，不需要额外配置。
- 在 cakit 生成的本地配置里，`gateway.remote.token` 会对齐到 `gateway.auth.token`，这样隔离运行里的 `sessions_spawn` 才能正常通过本地 gateway 鉴权。
- 但真实 spawn 仍取决于本地 gateway 状态。如果目标端口上已经有另一个 token 不一致的 `openclaw-gateway` 在监听，主 agent 可能会尝试 `sessions_spawn`，但 child session 实际拉不起来。
- subagent 再继续 spawn subagent 属于另一层配置，默认关闭；但这不是本文这里的基本需求。

## Goose

当前测试版本：`1.27.2`

- Goose 在 `auto` 模式下会启用 subagents。
- 我们当前默认就是 `auto`，所以实际可视为默认开启。
- 如果之前手动切过 mode，可用 `goose --mode auto` 或设置 `GOOSE_MODE=auto` 切回。
