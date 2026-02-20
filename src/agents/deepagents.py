from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent, InstallStrategy, RunCommandTemplate, VersionCommandTemplate
from ..models import RunResult
from ..stats_extract import last_value, parse_usage_by_model, req_str, select_values, sum_int


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
        regex=r"^(?:deepagents\s+)?([A-Za-z0-9._-]+)$",
    )
    _THREAD_ID_RE = re.compile(r"Thread:\s*([0-9a-fA-F]{8})")

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        env, env_error, selected_model = self._build_run_env(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=selected_model,
        )
        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
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
            response = self._last_stdout_line(
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

        return self.finalize_run(
            command_result=result,
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
        assistant_messages = self._selected_dicts(payload, '$.messages[?(@.type == "ai")]')
        models_usage: Dict[str, Dict[str, int]] = {}
        for message in assistant_messages:
            model_name = req_str(message, "$.response_metadata.model_name")
            usage_raw = last_value(message, "$.usage_metadata")
            usage = parse_usage_by_model(usage_raw, "input_output") if isinstance(usage_raw, dict) else None
            if model_name is None or usage is None:
                continue
            self._merge_model_usage(models_usage, model_name, usage)

        nested_tool_calls = self._count_selected(payload, '$.messages[?(@.type == "ai")].tool_calls[*]')
        scalar_tool_calls = sum_int(payload, '$.messages[?(@.type == "ai")].tool_calls')
        tool_calls = (
            None
            if nested_tool_calls is None and scalar_tool_calls is None
            else (nested_tool_calls or 0) + (scalar_tool_calls or 0)
        )

        response = next(
            (
                text
                for text in (
                    self._extract_content_text(last_value(message, "$.content"), allow_scalars=True)
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
        api_key = self._resolve_openai_api_key("DEEPAGENTS_OPENAI_API_KEY")
        base_url = self._resolve_openai_base_url("DEEPAGENTS_OPENAI_BASE_URL")
        model = self._resolve_litellm_model(
            "DEEPAGENTS_OPENAI_MODEL",
            model_override=model_override,
            output_format="colon",
        )

        missing: list[tuple[str, str]] = []
        if not api_key:
            missing.append(("DEEPAGENTS_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if not model:
            missing.append(("DEEPAGENTS_OPENAI_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return {}, self._missing_env_with_fallback_message(missing), ""

        env: Dict[str, str] = {
            "OPENAI_API_KEY": api_key,
        }
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
        return env, None, model

    def _extract_checkpoint_stats(
        self, *, thread_id: str, base_env: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        binary = self._resolve_binary()
        if not binary:
            return None
        binary_path = Path(binary).expanduser().resolve()
        python_executable = binary_path.parent / "python"
        if not python_executable.exists():
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
        return self._run_json_dict_command(
            [str(python_executable), "-c", parser_code, thread_id],
            base_env=base_env,
        )
