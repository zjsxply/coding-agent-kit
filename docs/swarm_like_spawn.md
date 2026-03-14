# Swarm-Like Spawn Support

This note records the current cakit-facing status of swarm-like runtime agent spawning for supported coding agents.

Scope:
- Covers runtime-launched helper agents / subagents.
- Does not count predefined-only subagent catalogs as swarm-like spawn.
- `Test Version` below is the current value in the README Test Coverage Matrix.
- Status is based on the upstream behavior we verified as of 2026-03-14.

`cakit` does not yet provide one unified multiagent switch. Use each upstream coding agent's own enablement path.

| Agent | Test Version | Current Status | Default | How To Enable |
| --- | --- | --- | --- | --- |
| kimi | 1.12.0 | Agent Swarm-style subagent launch in one run | On | No extra setup needed for basic swarm-like spawning |
| claude | 2.1.72 | Agent Teams | Off | Set `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1` and restart Claude Code |
| codex | 0.114.0 | Multi-agent | Off | Add `[features]` / `multi_agent = true` to `~/.codex/config.toml` |
| openclaw | 2026.3.8 | Main-agent subagent spawn | On | No extra setup needed for basic spawning |
| goose | 1.27.2 | Automatic subagents in auto mode | On | Usually nothing; if mode changed, switch back to `auto` |

## Trajectory Coverage

| Agent | Current `trajectory_path` coverage | Includes child-agent trace | Notes |
| --- | --- | --- | --- |
| kimi | CLI stdout + matching session `wire.jsonl` (and `context.jsonl` when present) | Yes | Kimi records nested subagent events in the wire log |
| claude | CLI stdout + main transcript + child transcripts under `subagents/` | Yes | Agent Teams / runtime-created child transcripts are included |
| codex | CLI stdout + exact rollout family for the main thread and child threads | Yes | rollout family is built from parent-thread linkage |
| openclaw | CLI stdout + run-local transcript family | Yes | all transcripts in the same temporary `OPENCLAW_HOME` are included |
| goose | CLI stdout + main session export + run-local SQLite snapshot + request logs | Yes | Goose has no single family transcript, so cakit assembles one from run-local artifacts |

## Stats Aggregation Coverage

Under swarm-like spawn, `models_usage`, `llm_calls`, and `tool_calls` are aggregated from parent/child run artifacts when those artifacts exist and can be matched exactly. `total_cost` is a separate signal: cakit does not estimate it from token usage.

| Agent | `models_usage` | `llm_calls` | `tool_calls` | `total_cost` | Notes |
| --- | --- | --- | --- | --- | --- |
| kimi | Yes | Yes | Yes | No | Aggregated from the run-local session family; Kimi does not expose a stable family cost field, so cakit keeps `total_cost` as `null` |
| claude | Yes | Yes | Yes | Upstream aggregate only | Aggregated from the main + child transcript family; `total_cost` currently comes from Claude's top-level `result.total_cost_usd`, and cakit does not independently resum child transcript cost |
| codex | Yes | Yes | Yes | No | Aggregated across the rollout family; no stable rollout-family cost field is exposed, so `total_cost` stays `null` |
| openclaw | Conditional | Conditional | Conditional | No | When child session transcripts exist, cakit aggregates them into the family totals; if `sessions_spawn` fails, only the records that were actually emitted by the main run are counted |
| goose | Yes | Yes | Yes | No | Aggregated across run-local sessions, SQLite state, and request logs including `sub_agent`; no stable family cost source is exposed |

## Kimi

Current tested version: `1.12.0`

- Basic Agent Swarm usage is already available in the default setup.
- For normal swarm-like usage, no extra config is needed.
- Prompting can be direct, for example: `launch multiple subagents`.
- When Kimi session logs are available, cakit aggregates subagent events into `models_usage`, `llm_calls`, and `tool_calls`.
- In our testing, separate concurrent Kimi sessions may still hit race conditions. Avoid running multiple top-level Kimi sessions at the same time.
- Kimi also has a separate `CreateSubagent` capability for defining new subagent types. That is a different knob and is not required for ordinary swarm-style prompting.

## Claude

Current tested version: `2.1.72`

- Claude's current swarm-like feature is `Agent Teams`.
- It is not on by default in the current upstream behavior.
- Enable it with `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1`.
- After changing the environment variable, restart Claude Code so the feature is picked up.
- In cakit, putting `CLAUDE_CODE_ENABLE_AGENT_TEAMS=1` in `.env` is enough because cakit passes the variable through directly.

## Codex

Current tested version: `0.114.0`

- Codex multi-agent is currently controlled by the upstream config file, not by a cakit flag.
- To enable it, add this to `~/.codex/config.toml`:

```toml
[features]
multi_agent = true
```

- Keep this enabled in the active Codex config used by the current shell/session.
- If you want to automate that through cakit's generic post-config hook, one simple example is:

```bash
export CAKIT_CONFIGURE_POST_COMMAND='if [ "$CAKIT_CONFIGURE_AGENT" = "codex" ]; then printf "\n[features]\nmulti_agent = true\n" >> "$CAKIT_CONFIG_PATH"; fi'
cakit install codex
```

- That example is suitable for a fresh config / one-time append. If your Codex config already has a `[features]` section, edit that existing section instead of appending a second `[features]` block.

## OpenClaw

Current tested version: `2026.3.8`

- OpenClaw main-agent subagent spawning is already available by default for the basic swarm-like case.
- No extra config is needed if all you need is the main agent spawning workers.
- In cakit's generated local config, `gateway.remote.token` is aligned with `gateway.auth.token` so isolated `sessions_spawn` calls can authenticate correctly.
- Real spawning still depends on the local gateway state. If a conflicting `openclaw-gateway` is already bound on the selected port with a different token, the parent agent may attempt `sessions_spawn` but the child sessions will fail to come up.
- Nested subagent -> subagent spawning is a separate setting and is off by default, but that is not required for the normal case documented here.

## Goose

Current tested version: `1.27.2`

- Goose subagents are available in `auto` mode.
- Our default assumption is `auto`, so this is effectively on by default.
- If the mode was changed manually, switch back with `goose --mode auto` or set `GOOSE_MODE=auto`.
