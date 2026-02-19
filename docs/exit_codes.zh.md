# 退出码说明

本文档基于当前实现（`src/cli.py`、`src/agents/base.py`）说明 cakit 各命令的退出码语义。

## 全局约定

- `0`：命令成功。
- `1`：运行失败，或严格字段校验失败。
- `2`：用户输入/参数不合法（用法级错误）。
- `>=3` 或其他非零值：通常来自上游 coding agent 命令自身退出码（主要在 `cakit run`），或透传子进程退出码（主要在 `cakit skills`）。
- `127`：子进程命令不可执行/未找到可执行文件（在 `cakit run` 中可能透传）。

`argparse` 的参数解析错误（例如缺少必填位置参数）会以 `2` 退出。

## `cakit install`

- `0`：目标 agent（单个或多个）全部安装成功。
- `1`：至少一个目标 agent 安装失败。
- `2`：agent 选择器/名称不支持。

## `cakit configure`

- `0`：目标 agent 配置成功；即使是 no-op（`config_path: null`）也视为成功。
- `2`：agent 选择器/名称不支持。

## `cakit run`

- `0`：命令成功，且严格统计字段校验通过。
- `1`：自动安装失败、严格字段校验失败，或内部结果缺少 `cakit_exit_code`。
- `2`：运行前参数校验失败，包括：
  - prompt 为空
  - 图像/视频文件不存在
  - `--env-file` 路径不存在或不是文件
  - `--reasoning-effort` 不支持或取值非法
  - 目标 coding agent 不支持传入的图像/视频模态
- 其他非零值：
  - 若上游 coding agent 命令本身非零退出，cakit 透传该退出码。
  - 若子进程命令未找到，可表现为 `127`。

当上游命令本身退出为 `0` 时，cakit 仍会做严格字段校验，要求：
- `response` 非空
- `models_usage` 非空
- `llm_calls >= 1`
- `tool_calls >= 0`

任一条件缺失/无效时，cakit 返回 `1`。

## `cakit skills`

- `0`：透传执行的 `npx skills`/`npm exec -- skills` 成功。
- `1`：透传前依赖/环境准备失败（例如 Node.js/npm 缺失且自动安装失败）。
- 其他非零值：透传子进程（`npx skills` 或 `npm exec -- skills`）自身退出码。

## `cakit tools`

- `0`：工具安装流程成功完成。
- `1`：平台/前置条件不满足，或安装步骤失败。

## `cakit env`

- `0`：成功将所选模板（`--lang en` 使用 `.env.template`，`--lang zh` 使用 `.env.template.zh`）写入目标路径。
- `1`：所选模板文件不存在。
