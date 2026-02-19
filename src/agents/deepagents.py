from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class DeepAgentsAgent(CodingAgent):
    name = "deepagents"
    display_name = "Deep Agents"
    binary = "deepagents"
    _THREAD_ID_RE = re.compile(r"Thread:\s*([0-9a-fA-F]{8})")

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        del scope
        package_spec = "deepagents-cli"
        if version and version.strip():
            package_spec = f"deepagents-cli=={version.strip()}"
        result = self._uv_tool_install(
            package_spec,
            python_version="3.12",
            force=True,
        )
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=ok,
            details=result.output,
            config_path=None,
        )

    def configure(self) -> Optional[str]:
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        del images, videos, reasoning_effort

        env, env_error, selected_model = self._build_run_env(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        cmd = [
            "deepagents",
            "-n",
            prompt,
            "--no-stream",
            "--model",
            selected_model,
        ]
        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        thread_id = self._extract_thread_id(output)

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        tool_calls: Optional[int] = None
        response: Optional[str] = None
        if thread_id:
            stats_payload = self._extract_checkpoint_stats(thread_id=thread_id, base_env=base_env)
            if isinstance(stats_payload, dict):
                parsed_models_usage = stats_payload.get("models_usage")
                parsed_llm_calls = self._as_int(stats_payload.get("llm_calls"))
                parsed_tool_calls = self._as_int(stats_payload.get("tool_calls"))
                parsed_response = stats_payload.get("response")
                if isinstance(parsed_models_usage, dict):
                    models_usage = self._validate_models_usage(parsed_models_usage)
                if parsed_llm_calls is not None:
                    llm_calls = parsed_llm_calls
                if parsed_tool_calls is not None:
                    tool_calls = parsed_tool_calls
                if isinstance(parsed_response, str):
                    cleaned = parsed_response.strip()
                    if cleaned:
                        response = cleaned
        if response is None:
            response = self._extract_response_from_output(output)

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            response=response,
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        first = self._version_first_line(["deepagents", "--version"])
        if first is None:
            return None
        prefixed = self._second_token_if_prefixed(first, prefix="deepagents")
        if prefixed:
            return prefixed
        return first

    def _build_run_env(
        self, *, model_override: Optional[str]
    ) -> tuple[Dict[str, str], Optional[str], str]:
        api_key = self._resolve_openai_api_key("DEEPAGENTS_OPENAI_API_KEY")
        base_url = self._resolve_openai_base_url("DEEPAGENTS_OPENAI_BASE_URL")
        model = self._resolve_openai_model("DEEPAGENTS_OPENAI_MODEL", model_override=model_override)

        missing: list[tuple[str, str]] = []
        if not api_key:
            missing.append(("DEEPAGENTS_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if not model:
            missing.append(("DEEPAGENTS_OPENAI_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return {}, self._missing_env_with_fallback_message(missing), ""

        normalized_model = self._normalize_model_spec(model)
        env: Dict[str, str] = {
            "OPENAI_API_KEY": api_key,
        }
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
        return env, None, normalized_model

    @staticmethod
    def _normalize_model_spec(model: str) -> str:
        cleaned = model.strip()
        if ":" in cleaned:
            return cleaned
        if "/" in cleaned:
            prefix, suffix = cleaned.split("/", 1)
            known = {
                "anthropic",
                "azure_ai",
                "azure_openai",
                "bedrock",
                "bedrock_converse",
                "cohere",
                "deepseek",
                "fireworks",
                "google_anthropic_vertex",
                "google_genai",
                "google_vertexai",
                "groq",
                "huggingface",
                "ibm",
                "mistralai",
                "nvidia",
                "ollama",
                "openai",
                "perplexity",
                "together",
                "upstage",
                "xai",
            }
            if prefix in known and suffix:
                return f"{prefix}:{suffix}"
        return f"openai:{cleaned}"

    def _extract_thread_id(self, output: str) -> Optional[str]:
        match = self._THREAD_ID_RE.search(output)
        if not match:
            return None
        return match.group(1).lower()

    def _extract_checkpoint_stats(
        self, *, thread_id: str, base_env: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        python_executable = self._deepagents_python()
        if not python_executable:
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
    print(json.dumps({"error": "sessions db not found"}))
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
    print(json.dumps({"error": "checkpoint not found"}))
    raise SystemExit(0)

serde = JsonPlusSerializer()
try:
    checkpoint = serde.loads_typed((row[0], row[1]))
except Exception as exc:
    print(json.dumps({"error": f"deserialize failed: {exc}"}))
    raise SystemExit(0)

channel_values = checkpoint.get("channel_values")
if not isinstance(channel_values, dict):
    print(json.dumps({"error": "channel_values missing"}))
    raise SystemExit(0)
messages = channel_values.get("messages")
if not isinstance(messages, list) or not messages:
    print(json.dumps({"error": "messages missing"}))
    raise SystemExit(0)

def as_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None

def extract_content_text(content):
    if isinstance(content, str):
        value = content.strip()
        return value or None
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        if parts:
            return "\n".join(parts).strip()
        return None
    return None

models_usage = {}
llm_calls = 0
tool_calls = 0
response = None

for message in messages:
    msg_type = getattr(message, "type", None)
    if msg_type != "ai":
        continue
    llm_calls += 1
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        print(json.dumps({"error": "usage_metadata missing"}))
        raise SystemExit(0)
    prompt_tokens = as_int(usage.get("input_tokens"))
    completion_tokens = as_int(usage.get("output_tokens"))
    if prompt_tokens is None or completion_tokens is None:
        print(json.dumps({"error": "usage_metadata tokens missing"}))
        raise SystemExit(0)

    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        print(json.dumps({"error": "response_metadata missing"}))
        raise SystemExit(0)
    model_name = response_metadata.get("model_name")
    if not isinstance(model_name, str) or not model_name.strip():
        print(json.dumps({"error": "model_name missing"}))
        raise SystemExit(0)
    model_name = model_name.strip()

    entry = models_usage.setdefault(
        model_name,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )
    entry["prompt_tokens"] += prompt_tokens
    entry["completion_tokens"] += completion_tokens
    entry["total_tokens"] = entry["prompt_tokens"] + entry["completion_tokens"]

    msg_tool_calls = getattr(message, "tool_calls", None)
    if msg_tool_calls is None:
        continue
    if not isinstance(msg_tool_calls, list):
        print(json.dumps({"error": "tool_calls invalid"}))
        raise SystemExit(0)
    tool_calls += len(msg_tool_calls)

    text = extract_content_text(getattr(message, "content", None))
    if text:
        response = text

if llm_calls < 1:
    print(json.dumps({"error": "no ai messages"}))
    raise SystemExit(0)
if not models_usage:
    print(json.dumps({"error": "models_usage empty"}))
    raise SystemExit(0)
if not isinstance(response, str) or not response.strip():
    print(json.dumps({"error": "response empty"}))
    raise SystemExit(0)

print(
    json.dumps(
        {
            "models_usage": models_usage,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "response": response,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
)
"""
        parsed = self._run([str(python_executable), "-c", parser_code, thread_id], base_env=base_env)
        if parsed.exit_code != 0:
            return None
        text = parsed.stdout.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("error"):
            return None
        return payload

    def _deepagents_python(self) -> Optional[Path]:
        binary = self._resolve_binary()
        if not binary:
            return None
        path = Path(binary).expanduser().resolve()
        candidate = path.parent / "python"
        if candidate.exists():
            return candidate
        return None

    def _validate_models_usage(self, raw: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        validated: Dict[str, Dict[str, int]] = {}
        for model_name, usage in raw.items():
            if not isinstance(model_name, str) or not model_name.strip():
                return {}
            if not isinstance(usage, dict):
                return {}
            prompt_tokens = self._as_int(usage.get("prompt_tokens"))
            completion_tokens = self._as_int(usage.get("completion_tokens"))
            total_tokens = self._as_int(usage.get("total_tokens"))
            if prompt_tokens is None or completion_tokens is None or total_tokens is None:
                return {}
            validated[model_name.strip()] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
        return validated

    def _extract_response_from_output(self, output: str) -> Optional[str]:
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return None
        filtered = [
            line
            for line in lines
            if not line.startswith("Running task non-interactively")
            and not line.startswith("Agent:")
            and not line.startswith("Thread:")
            and not line.startswith("✓ Task completed")
            and not line.startswith("🔧 Calling tool:")
            and not line.startswith("✓ Auto-approved:")
        ]
        if not filtered:
            return None
        return filtered[-1]
