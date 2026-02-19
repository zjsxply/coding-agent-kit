# Factory Agent（cakit）

本文说明 cakit 如何安装与运行 Factory Droid CLI（`droid`）。

## 安装

安装最新版本：

```bash
cakit install factory
```

安装指定版本：

```bash
cakit install factory --version <factory_cli_version>
```

cakit 行为：
- 不带 `--version` 时，调用官方安装脚本：`curl -fsSL https://app.factory.ai/cli | sh`
- 带 `--version` 时，从 `https://downloads.factory.ai/factory-cli/releases/<version>/...` 下载对应 release 二进制，并先做 SHA-256 校验再安装。

## 配置

`cakit configure factory` 为无操作（`config_path: null`）。

`cakit run factory` 的环境变量映射：

| 环境变量 | 含义 | 要求 |
| --- | --- | --- |
| `FACTORY_API_KEY` | Factory API Key（API 鉴权） | 可选（未做 OAuth 登录时通常必需） |
| `FACTORY_API_BASE_URL` | 可选的上游 API Base URL 覆盖项 | 可选 |
| `FACTORY_TOKEN` | 某些 CI 工作流中使用的可选 token 变量名 | 可选 |
| `CAKIT_FACTORY_MODEL` | cakit 的默认模型（映射到 `droid exec --model`；BYOK 模式可回退 `OPENAI_DEFAULT_MODEL`） | 可选 |
| `CAKIT_FACTORY_BYOK_API_KEY` | cakit BYOK 上游 API Key（写入 `customModels[].apiKey`；回退：`OPENAI_API_KEY`） | 可选 |
| `CAKIT_FACTORY_BYOK_BASE_URL` | cakit BYOK 上游 Base URL（写入 `customModels[].baseUrl`；回退：`OPENAI_BASE_URL`） | 可选 |
| `CAKIT_FACTORY_BYOK_PROVIDER` | cakit BYOK provider（`openai` / `anthropic` / `generic-chat-completion-api`） | 可选（不填时自动推断） |
| `FACTORY_LOG_FILE` | 可选的上游 CLI 日志文件路径 | 可选 |
| `FACTORY_DISABLE_KEYRING` | 可选；在 headless 环境禁用 keyring | 可选 |

当同时设置 `CAKIT_FACTORY_BYOK_API_KEY` + `CAKIT_FACTORY_BYOK_BASE_URL` + `CAKIT_FACTORY_MODEL` 时，cakit 会自动写入/更新 `~/.factory/settings.json` 的 `customModels`，并使用生成的 `custom:...` 模型引用运行 Droid。

当启用 BYOK 模式时，也支持共享回退：
- `OPENAI_API_KEY` -> `CAKIT_FACTORY_BYOK_API_KEY`
- `OPENAI_BASE_URL` -> `CAKIT_FACTORY_BYOK_BASE_URL`
- `OPENAI_DEFAULT_MODEL` -> `CAKIT_FACTORY_MODEL`

即使使用 BYOK 自定义模型，仍需要 Factory 鉴权。可使用 OAuth（运行 `droid` 后输入 `/login`）或设置有效的 `FACTORY_API_KEY`。

## 图像与视频输入

- 支持 `cakit run factory --image <path>`。
  - cakit 通过提示词注入本地路径，并指示 Droid 使用 `Read` 工具读取文件。
- 不支持 `cakit run factory --video <path>`。
  - `droid exec` 未提供通用 `--video` 参数。

## 推理强度

`cakit run factory --reasoning-effort <value>` 会直接映射为 `droid exec --reasoning-effort <value>`。

cakit 当前支持取值：
- `off`、`none`、`low`、`medium`、`high`

## 统计字段提取

`cakit run factory` 按 run 产物做严格提取：

1. 从 `droid exec --output-format json` 输出中解析精确 `{"type":"result", ...}` 结构。
2. 提取：
   - `response`：`result`
   - `llm_calls`：`num_turns`
   - token：`usage.input_tokens`、`usage.output_tokens`、`usage.cache_read_input_tokens`、`usage.cache_creation_input_tokens`
3. 从同一结果对象提取精确 `session_id`。
4. 基于精确会话设置文件提取模型名：
   - `~/.factory/sessions/**/<session_id>.settings.json`
   - 字段：`model`
5. 基于精确会话 transcript 提取工具调用次数：
   - `~/.factory/sessions/**/<session_id>.jsonl`
   - 统计 `type == "tool_call"`（若 transcript 内出现 `hook_event_name == "PreToolUse"` 也计入）
6. `models_usage` 的模型名必须来自 run 产物；不从配置/环境变量回填。

若关键统计字段无法按上述精确字段提取，cakit 对该次 run 返回非零退出码。

`trajectory_path` 指向由原始 CLI 输出转换得到的 YAML 可读轨迹文件。
