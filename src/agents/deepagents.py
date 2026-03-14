from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    RunParseResult,
    RunPlan,
    VersionCommandTemplate,
)
from ..stats_extract import last_value, merge_model_usage, parse_usage_by_model, req_str, select_values, sum_int
from ..agent_runtime import command_exec as runtime_command
from ..agent_runtime import env as runtime_env
from ..agent_runtime import install_version as runtime_install
from ..agent_runtime import parsing as runtime_parsing


class DeepAgentsAgent(CodingAgent):
    name = "deepagents"
    display_name = "Deep Agents"
    binary = "deepagents"
    install_strategy = InstallStrategy(
        kind="uv_tool",
        package="deepagents-cli",
        version_style="pep440",
        python_version="3.12",
        force=True,
    )
    run_template = RunCommandTemplate(
        base_args=("--no-stream",),
        prompt_mode="flag",
        prompt_flag="-n",
        model_flag="--model",
        media_injection="none",
    )
    version_template = VersionCommandTemplate(
        args=("deepagents", "--version"),
        parse_mode="regex_first_line",
        regex=r"^(?:deepagents(?:-cli)?\s+)?([A-Za-z0-9._-]+)$",
    )
    _THREAD_ID_RE = re.compile(r"Thread:\s*([0-9a-fA-F]{8})")

    def _build_run_plan(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> Optional[RunPlan]:
        env, env_error, selected_model = self._build_run_env(model_override=model_override)
        if env_error is not None:
            self._raise_config_error(env_error)
        return self._build_templated_run_plan(
            prompt=prompt,
            model=selected_model,
            env=env,
            template=self.run_template,
            parse_output=lambda output, command_result: self._parse_pipeline_output(
                output,
                command_result,
                base_env=base_env,
            ),
        )

    def _parse_pipeline_output(
        self,
        output: str,
        command_result: Any,
        *,
        base_env: Optional[Dict[str, str]],
    ) -> RunParseResult:
        match = self._THREAD_ID_RE.search(output)
        thread_id = match.group(1).lower() if match else None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        tool_calls: Optional[int] = None
        response: Optional[str] = None
        if thread_id:
            stats_payload = self._extract_checkpoint_stats(thread_id=thread_id, base_env=base_env)
            parsed = self._parse_checkpoint_stats_payload(stats_payload)
            if parsed is not None:
                models_usage, llm_calls, tool_calls, response = parsed
        if response is None:
            response = runtime_parsing.last_stdout_line(
                output,
                skip_prefixes=(
                    "Running task non-interactively",
                    "Agent:",
                    "Thread:",
                    "✓ Task completed",
                    "🔧 Calling tool:",
                    "✓ Auto-approved:",
                ),
            )
        return RunParseResult(
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )

    def _parse_checkpoint_stats_payload(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> Optional[tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[str]]]:
        if not isinstance(payload, dict):
            return None
        assistant_messages = [
            item
            for item in (select_values(payload, '$.messages[?(@.type == "ai")]') or [])
            if isinstance(item, dict)
        ]
        models_usage: Dict[str, Dict[str, int]] = {}
        for message in assistant_messages:
            model_name = req_str(message, "$.response_metadata.model_name")
            usage_raw = last_value(message, "$.usage_metadata")
            usage = parse_usage_by_model(usage_raw, "input_output") if isinstance(usage_raw, dict) else None
            if model_name is None or usage is None:
                continue
            merge_model_usage(models_usage, model_name, usage)

        nested_tool_call_values = select_values(payload, '$.messages[?(@.type == "ai")].tool_calls[*]')
        nested_tool_calls = len(nested_tool_call_values) if nested_tool_call_values is not None else None
        scalar_tool_calls = sum_int(payload, '$.messages[?(@.type == "ai")].tool_calls')
        tool_calls = (
            None
            if nested_tool_calls is None and scalar_tool_calls is None
            else (nested_tool_calls or 0) + (scalar_tool_calls or 0)
        )
        if tool_calls is None and assistant_messages:
            tool_calls = 0

        response = next(
            (
                text
                for text in (
                    runtime_parsing.extract_content_text(last_value(message, "$.content"), allow_scalars=True)
                    for message in reversed(assistant_messages)
                )
                if text is not None
            ),
            None,
        )
        return (
            models_usage,
            (len(assistant_messages) if assistant_messages else None),
            tool_calls,
            response,
        )

    def _build_run_env(
        self, *, model_override: Optional[str]
    ) -> tuple[Dict[str, str], Optional[str], str]:
        resolved, error = runtime_env.resolve_openai_env(
            api_key_env="DEEPAGENTS_OPENAI_API_KEY",
            model_env="DEEPAGENTS_OPENAI_MODEL",
            base_url_env="DEEPAGENTS_OPENAI_BASE_URL",
            model_override=model_override,
            normalize_text=runtime_parsing.normalize_text,
        )
        if error is not None:
            return {}, error, ""
        model_raw = resolved.get("model")
        model = (
            runtime_env.normalize_litellm_model(model_raw, output_format="colon")
            if isinstance(model_raw, str)
            else None
        )
        if not model:
            return {}, runtime_env.missing_env_with_fallback_message([("DEEPAGENTS_OPENAI_MODEL", "OPENAI_DEFAULT_MODEL")]), ""

        env: Dict[str, str] = {
            "OPENAI_API_KEY": str(resolved.get("api_key")),
        }
        base_url = resolved.get("base_url")
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
        return env, None, model

    def _extract_checkpoint_stats(
        self, *, thread_id: str, base_env: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        binary = runtime_command.resolve_binary(
            agent_name=self.name,
            binary=self.binary,
            npm_prefix=self._npm_prefix(),
            env_source=os.environ,
        )
        if not binary:
            return None
        binary_path = Path(binary).expanduser().resolve()
        python_executable = runtime_install.resolve_python_executable(search_dirs=(binary_path.parent,))
        if python_executable is None:
            return None
        parser_code = r"""
import json
import sqlite3
import sys
from pathlib import Path
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

thread_id = sys.argv[1]
db_path = Path.home() / ".deepagents" / "sessions.db"
if not db_path.exists():
    print("{}")
    raise SystemExit(0)

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute(
    "select type, checkpoint from checkpoints where thread_id=? order by checkpoint_id desc limit 1",
    (thread_id,),
)
row = cur.fetchone()
conn.close()
if not row:
    print("{}")
    raise SystemExit(0)

serde = JsonPlusSerializer()
try:
    checkpoint = serde.loads_typed((row[0], row[1]))
except Exception:
    print("{}")
    raise SystemExit(0)

def to_jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "dict"):
        try:
            return to_jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return {
                str(key): to_jsonable(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        except Exception:
            pass
    return str(value)

channel_values = checkpoint["channel_values"] if isinstance(checkpoint, dict) and "channel_values" in checkpoint else None
messages = channel_values["messages"] if isinstance(channel_values, dict) and "messages" in channel_values else []
if not isinstance(messages, list):
    messages = []

serialized_messages = [
    to_jsonable(
        {
            "type": getattr(message, "type", None),
            "usage_metadata": getattr(message, "usage_metadata", None),
            "response_metadata": getattr(message, "response_metadata", None),
            "tool_calls": getattr(message, "tool_calls", None),
            "content": getattr(message, "content", None),
        }
    )
    for message in messages
]

print(
    json.dumps(
        {
            "messages": serialized_messages,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
)
        """
        return runtime_parsing.run_json_dict_command(
            args=[python_executable, "-c", parser_code, thread_id],
            run=self._run,
            base_env=base_env,
        )
