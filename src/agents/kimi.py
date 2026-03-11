from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    CodingAgent,
    CommandResult,
    InstallStrategy,
    RunCommandTemplate,
    VersionCommandTemplate,
)
from ..models import RunResult
from ..stats_extract import (
    build_single_model_stats_snapshot,
    last_value,
    parse_usage_by_model,
    req_str,
    select_values,
    sum_usage_entries,
)
from ..agent_runtime import install_version as runtime_install
from ..agent_runtime import parsing as runtime_parsing
from ..agent_runtime import env as runtime_env
from ..io_helpers import dump_toml


class KimiAgent(CodingAgent):
    name = "kimi"
    display_name = "Kimi Code CLI"
    binary = "kimi"
    supports_images = True
    supports_videos = True
    required_runtimes = ("uv",)
    install_strategy = InstallStrategy(kind="custom")
    run_template = RunCommandTemplate(
        base_args=("--print", "--output-format", "stream-json", "--yolo"),
        prompt_mode="flag",
        prompt_flag="--prompt",
        model_flag="--model",
        media_injection="natural",
        media_tool_name="ReadMediaFile",
    )
    version_template = VersionCommandTemplate(
        args=("kimi", "info", "--json"),
        parse_mode="json_path",
        json_path="$.kimi_cli_version",
    )
    _ALLOWED_PROVIDER_TYPES = {"kimi", "openai_legacy", "openai_responses"}

    def _install_with_custom_strategy(
        self,
        *,
        strategy: InstallStrategy,
        scope: str,
        version: Optional[str],
    ) -> CommandResult:
        if version and version.strip():
            package_spec = f"kimi-cli=={version.strip()}"
            result = runtime_install.uv_tool_install(
                package_spec=package_spec,
                python_version="3.13",
                force=False,
                with_packages=None,
                fallback_no_cache_dir=False,
                run=self._run,
                ensure_uv_fn=lambda: runtime_install.ensure_uv(self._run),
                pip_install_fn=lambda packages, no_cache: runtime_install.pip_install(
                    packages=packages,
                    no_cache_dir=no_cache,
                    run=self._run,
                ),
            )
            if isinstance(result, CommandResult):
                return result
            return CommandResult(
                exit_code=getattr(result, "exit_code", 1),
                stdout=getattr(result, "stdout", ""),
                stderr=getattr(result, "stderr", ""),
                duration_seconds=getattr(result, "duration_seconds", 0.0),
            )
        return self._run(["bash", "-lc", "curl -LsSf https://code.kimi.com/install.sh | bash"])

    def configure(self) -> Optional[str]:
        settings = self._resolve_runtime_settings(model_override=None)
        api_key = settings["api_key"]
        base_url = settings["base_url"]
        provider_type = settings["provider_type"]

        required = [api_key, base_url]
        if any(not value for value in required):
            return None
        if provider_type is None:
            return None
        config = {
            "providers": {
                "kimi": {
                    "name": "Kimi",
                    "type": provider_type,
                    "base_url": base_url,
                    "api_key": api_key,
                }
            }
        }
        path = Path.home() / ".kimi" / "config.toml"
        self._write_text(path, dump_toml(config))
        return str(path)

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        images = images or []
        videos = videos or []
        settings = self._resolve_runtime_settings(model_override=model_override)
        requested_model_name = settings["model_name"]
        session_id = str(uuid.uuid4())
        env: Dict[str, Optional[str]] = {
            "KIMI_API_KEY": settings["api_key"],
            "KIMI_BASE_URL": settings["base_url"],
            "KIMI_CLI_NO_AUTO_UPDATE": "1",
        }
        if requested_model_name:
            # Kimi CLI can require env-based model resolution in some flows.
            # Keep --model for explicit run control and also set env for compatibility.
            env["KIMI_MODEL_NAME"] = requested_model_name
        template = self.run_template
        extra_args = [
            "--work-dir",
            str(self.workdir),
            "--session",
            session_id,
        ]
        if reasoning_effort == "thinking":
            extra_args.append("--thinking")
        elif reasoning_effort == "none":
            extra_args.append("--no-thinking")
        cmd, run_prompt = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=requested_model_name,
            images=images,
            videos=videos,
            extra_args=extra_args,
        )
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payloads = runtime_parsing.load_output_json_payloads(output, stdout_only=False)
        usage, tool_calls, llm_calls, model_name = self._resolve_run_stats(
            payloads=payloads,
            session_id=session_id,
            prompt=run_prompt,
        )
        snapshot = build_single_model_stats_snapshot(
            model_name=model_name,
            usage=usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            total_cost=None,
        )

        return self.finalize_run(
            command_result=result,
            response=self._extract_stream_response(payloads, output),
            models_usage=snapshot.models_usage if snapshot is not None else {},
            llm_calls=snapshot.llm_calls if snapshot is not None else None,
            tool_calls=snapshot.tool_calls if snapshot is not None else None,
        )

    def _extract_stream_response(self, payloads: List[Dict[str, Any]], output: str) -> Optional[str]:
        assistant_contents = select_values(payloads, '$[?(@.role == "assistant")].content')
        for content in reversed(assistant_contents or []):
            if isinstance(content, str):
                normalized = runtime_parsing.normalize_text(content)
                if normalized is not None:
                    return normalized
                continue
            text = runtime_parsing.extract_content_text(content, allow_scalars=False)
            if text is not None:
                return text
        return runtime_parsing.last_stdout_line(output)

    def _resolve_runtime_settings(self, *, model_override: Optional[str]) -> Dict[str, Optional[str]]:
        raw_provider_type = os.environ.get("CAKIT_KIMI_PROVIDER_TYPE")
        provider_type: Optional[str]
        if isinstance(raw_provider_type, str):
            normalized_provider_type = raw_provider_type.strip()
            provider_type = (
                normalized_provider_type
                if normalized_provider_type in self._ALLOWED_PROVIDER_TYPES
                else None
            )
        else:
            provider_type = None
        return {
            "api_key": runtime_env.resolve_openai_api_key("KIMI_API_KEY"),
            "base_url": runtime_env.resolve_openai_base_url("KIMI_BASE_URL"),
            "model_name": runtime_env.resolve_openai_model("KIMI_MODEL_NAME", model_override=model_override),
            "provider_type": provider_type,
        }

    def _resolve_run_stats(
        self,
        *,
        payloads: List[Dict[str, Any]],
        session_id: str,
        prompt: str,
    ) -> Tuple[Optional[Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        usage: Optional[Dict[str, int]] = None
        tool_calls: Optional[int] = None
        llm_calls: Optional[int] = None
        model_name: Optional[str] = None
        session_usage, session_tool_calls, session_llm_calls, session_model_name = self._extract_session_stats(session_id, prompt)
        if session_usage is not None:
            usage = session_usage
        if session_tool_calls is not None:
            tool_calls = session_tool_calls
        if session_llm_calls is not None:
            llm_calls = session_llm_calls
        if session_model_name:
            model_name = session_model_name
        if usage is None:
            raw_usage = last_value(payloads, "$[*].usage")
            usage = parse_usage_by_model(raw_usage, "prompt_completion") if isinstance(raw_usage, dict) else None
        if tool_calls is None:
            tool_call_values = select_values(payloads, "$[*].tool_calls[*]")
            tool_calls = len(tool_call_values) if tool_call_values is not None else None
        if model_name is None:
            model_name = self._extract_model_name_from_log(session_id, prompt)
        return usage, tool_calls, llm_calls, model_name

    def _extract_session_stats(
        self, session_id: Optional[str], prompt: str
    ) -> Tuple[Optional[Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        wire_path = self._find_session_wire_path(session_id)
        if not wire_path:
            return None, None, None, None
        wire_text = self._read_text_lossy(wire_path)
        if wire_text is None:
            return None, None, None, None
        lines = wire_text.splitlines()

        messages: List[Dict[str, Any]] = []
        for line in lines:
            if not line:
                continue
            record = runtime_parsing.parse_json_dict(line)
            if record is None:
                continue
            message = last_value(record, "$.message")
            if isinstance(message, dict):
                messages.append(message)
        if not messages:
            return None, None, None, None

        turn_start_idx: Optional[int] = None
        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if req_str(message, "$.type") != "TurnBegin":
                continue
            payload = last_value(message, "$.payload")
            if not isinstance(payload, dict):
                continue
            user_input = last_value(payload, "$.user_input")
            if isinstance(user_input, list):
                user_input_parts = [
                    text
                    for text in (
                        runtime_parsing.normalize_text(item)
                        for item in (select_values(user_input, '$[?(@.type == "text")].text') or [])
                    )
                    if text is not None
                ]
                user_input = "".join(user_input_parts) if user_input_parts else None
            if isinstance(user_input, str) and user_input == prompt:
                turn_start_idx = idx
                break
        if turn_start_idx is None:
            return None, None, None, None

        turn_end_idx = len(messages)
        for idx in range(turn_start_idx + 1, len(messages)):
            if req_str(messages[idx], "$.type") == "TurnEnd":
                turn_end_idx = idx
                break
        turn_messages = messages[turn_start_idx + 1:turn_end_idx]

        tool_calls = None
        for path in (
            '$[?(@.type == "ToolCall")]',
            '$[?(@.type == "SubagentEvent")].payload.event[?(@.type == "ToolCall")]',
        ):
            values = select_values(turn_messages, path)
            if values is None:
                continue
            tool_calls = (tool_calls or 0) + len(values)

        status_payloads: List[Dict[str, Any]] = [
            payload
            for payload in (
                (select_values(turn_messages, '$[?(@.type == "StatusUpdate")].payload') or [])
                + (
                    select_values(
                        turn_messages,
                        '$[?(@.type == "SubagentEvent")].payload.event[?(@.type == "StatusUpdate")].payload',
                    )
                    or []
                )
            )
            if isinstance(payload, dict)
        ]

        usage_by_message_id: Dict[str, Dict[str, int]] = {}
        status_without_message_id: List[Dict[str, int]] = []
        message_ids = [req_str(payload, "$.message_id") for payload in status_payloads]
        llm_calls = (
            len({message_id for message_id in message_ids if message_id is not None})
            + sum(1 for message_id in message_ids if message_id is None)
            if status_payloads
            else None
        )
        model_name = next((model for model in (req_str(payload, "$.model") for payload in status_payloads) if model is not None), None)
        for payload in status_payloads:
            raw_usage = last_value(payload, "$.token_usage")
            normalized = parse_usage_by_model(raw_usage, "input_other_output_delta") if isinstance(raw_usage, dict) else None
            if normalized is None:
                continue
            message_id = req_str(payload, "$.message_id")
            if message_id is not None:
                previous = usage_by_message_id.get(message_id)
                if previous is None or normalized["total_tokens"] >= previous["total_tokens"]:
                    usage_by_message_id[message_id] = normalized
            else:
                status_without_message_id.append(normalized)

        usage_values = list(usage_by_message_id.values()) + status_without_message_id
        usage = sum_usage_entries(usage_values)
        return usage, tool_calls, llm_calls, model_name

    def _find_session_wire_path(self, session_id: Optional[str]) -> Optional[Path]:
        if not session_id:
            return None
        workdir = str(self.workdir)
        workdir_md5 = hashlib.md5(workdir.encode("utf-8")).hexdigest()
        kaos_name = "local"
        meta_text = self._read_text(Path.home() / ".kimi" / "kimi.json")
        if meta_text:
            meta = runtime_parsing.parse_json(meta_text)
            if isinstance(meta, dict):
                work_dirs = meta.get("work_dirs")
                if isinstance(work_dirs, list):
                    for item in work_dirs:
                        if not isinstance(item, dict) or item.get("path") != workdir:
                            continue
                        candidate = item.get("kaos")
                        if isinstance(candidate, str) and candidate:
                            kaos_name = candidate
                        break
        dir_basename = workdir_md5 if kaos_name == "local" else f"{kaos_name}_{workdir_md5}"
        wire_path = Path.home() / ".kimi" / "sessions" / dir_basename / session_id / "wire.jsonl"
        if wire_path.exists():
            return wire_path
        return None

    def _extract_model_name_from_log(self, session_id: Optional[str], prompt: str) -> Optional[str]:
        if not session_id:
            return None
        log_path = Path.home() / ".kimi" / "logs" / "kimi.log"
        lines = self._tail_lines(log_path, max_bytes=4 * 1024 * 1024)
        if not lines:
            return None
        start_idx: Optional[int] = None
        session_markers = (
            f"Created new session: {session_id}",
            f"Switching to session: {session_id}",
            f"Session {session_id} not found, creating new session",
        )
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if any(marker in line for marker in session_markers):
                start_idx = idx
                break
        if start_idx is None:
            return None

        anchor_idx: Optional[int] = None
        prompt_first_line = prompt.splitlines()[0] if prompt else ""
        workdir_str = str(self.workdir)
        for idx in range(len(lines) - 1, start_idx, -1):
            line = lines[idx]
            if "load_agents_md" in line and workdir_str in line:
                anchor_idx = idx
                break
            if prompt_first_line and "Running agent with command:" in line and prompt_first_line in line:
                anchor_idx = idx
                break
        if anchor_idx is None:
            return None

        for idx in range(anchor_idx, start_idx, -1):
            line = lines[idx]
            marker_pos = line.find("Using LLM model:")
            if marker_pos < 0:
                continue
            model_pos = line.find("model='", marker_pos)
            if model_pos < 0:
                continue
            remain = line[model_pos + len("model='"):]
            end_pos = remain.find("'")
            if end_pos <= 0:
                continue
            return remain[:end_pos] or None
        return None

    @staticmethod
    def _tail_lines(path: Path, *, max_bytes: int) -> List[str]:
        try:
            with path.open("rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                offset = max(0, size - max_bytes)
                file.seek(offset)
                content = file.read()
        except Exception:
            return []
        text = content.decode("utf-8", errors="ignore")
        if offset > 0:
            first_newline = text.find("\n")
            if first_newline == -1:
                return []
            text = text[first_newline + 1 :]
        return text.splitlines()
