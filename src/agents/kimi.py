from __future__ import annotations

import json
import os
import time
import uuid
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class KimiAgent(CodingAgent):
    name = "kimi"
    display_name = "Kimi Code CLI"
    binary = "kimi"
    supports_images = True
    supports_videos = True
    _ALLOWED_PROVIDER_TYPES = {"kimi", "openai_legacy", "openai_responses"}

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        if version and version.strip():
            package_spec = f"kimi-cli=={version.strip()}"
            if self._ensure_uv():
                result = self._run(["uv", "tool", "install", "--python", "3.13", package_spec])
            else:
                result = self._run(["python", "-m", "pip", "install", package_spec])
        else:
            result = self._run(["bash", "-lc", "curl -LsSf https://code.kimi.com/install.sh | bash"])
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        api_key = os.environ.get("KIMI_API_KEY")
        base_url = os.environ.get("KIMI_BASE_URL")
        provider_type = self._normalize_provider_type(os.environ.get("CAKIT_KIMI_PROVIDER_TYPE"))

        required = [api_key, base_url]
        if any(not value for value in required):
            return None
        if provider_type is None:
            return None
        config_lines = [
            '[providers."kimi"]',
            'name = "Kimi"',
            f'type = "{provider_type}"',
            f'base_url = "{base_url}"',
            f'api_key = "{api_key}"',
        ]
        config = "\n".join(config_lines) + "\n"
        path = Path.home() / ".kimi" / "config.toml"
        self._write_text(path, config)
        return str(path)

    @staticmethod
    def _normalize_provider_type(provider_type: Optional[str]) -> Optional[str]:
        if not isinstance(provider_type, str):
            return None
        normalized = provider_type.strip()
        if normalized in KimiAgent._ALLOWED_PROVIDER_TYPES:
            return normalized
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        images = images or []
        videos = videos or []
        run_started = time.time()
        requested_model_name = os.environ.get("KIMI_MODEL_NAME")
        session_id = str(uuid.uuid4())
        env = {
            "KIMI_API_KEY": os.environ.get("KIMI_API_KEY"),
            "KIMI_BASE_URL": os.environ.get("KIMI_BASE_URL"),
            "KIMI_CLI_NO_AUTO_UPDATE": "1",
        }
        run_prompt = prompt
        cmd = [
            "kimi",
            "--print",
            "--output-format",
            "stream-json",
            "--yolo",
            "--work-dir",
            str(self.workdir),
            "--session",
            session_id,
        ]
        run_prompt = prompt
        if images or videos:
            run_prompt = self._build_prompt_with_media_paths(prompt, images, videos)
        cmd.extend(["--prompt", run_prompt])
        if requested_model_name:
            cmd.extend(["--model", requested_model_name])
        if reasoning_effort == "thinking":
            cmd.append("--thinking")
        elif reasoning_effort == "none":
            cmd.append("--no-thinking")
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(output)
        usage: Optional[Dict[str, int]] = None
        tool_calls: Optional[int] = None
        llm_calls: Optional[int] = None
        model_name: Optional[str] = None

        session_usage, session_tool_calls, session_llm_calls, session_model_name = self._extract_session_stats(
            session_id, run_prompt, run_started
        )
        if session_usage is not None:
            usage = session_usage
        if session_tool_calls is not None:
            tool_calls = session_tool_calls
        if session_llm_calls is not None:
            llm_calls = session_llm_calls
        if session_model_name:
            model_name = session_model_name

        # Keep stdout parsing minimal and strict; prefer exact session artifacts.
        if usage is None:
            usage = self._extract_usage(payloads)
        if tool_calls is None:
            tool_calls = self._count_tool_calls(payloads)
        if model_name is None:
            model_name = self._extract_model_name_from_log(session_id, run_prompt)

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(
            self.name, format_trace_text(output, source=str(output_path))
        )
        models_usage: Dict[str, Dict[str, int]] = {}
        if usage is not None and model_name:
            models_usage = self._ensure_models_usage({}, usage, model_name)
        response = self._extract_response(payloads, output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            response=response,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["kimi", "info", "--json"])
        if result.exit_code == 0:
            try:
                data = json.loads(result.output.strip())
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("kimi_cli_version"):
                return str(data.get("kimi_cli_version"))
        return None

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            usage = payload.get("usage")
            if isinstance(usage, dict):
                return self._normalize_usage(usage)
        return None

    def _extract_response(self, payloads: List[Dict[str, Any]], output: str) -> Optional[str]:
        messages: List[str] = []

        def add_text(value: Any) -> None:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    messages.append(cleaned)

        def add_from_content(content: Any) -> None:
            if isinstance(content, str):
                add_text(content)
                return
            if isinstance(content, dict):
                add_text(content.get("text"))
                add_text(content.get("content"))
                return
            if not isinstance(content, list):
                return
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type in {"text", "output_text"}:
                    add_text(item.get("text"))

        def add_from_message(message: Any) -> None:
            if isinstance(message, dict):
                add_from_content(message.get("content"))
                add_text(message.get("text"))
            else:
                add_text(message)

        def add_from_choices(choices: Any) -> None:
            if not isinstance(choices, list):
                return
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message") or choice.get("delta")
                add_from_message(message)

        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            add_from_choices(payload.get("choices"))
            if payload.get("role") == "assistant":
                add_from_content(payload.get("content"))
            payload_type = payload.get("type")
            if payload_type in {"final", "assistant_message", "assistant"}:
                add_text(payload.get("text") or payload.get("message"))
            for key in ("output", "final", "response", "answer"):
                add_from_content(payload.get(key))

        if messages:
            return messages[-1]

        if output:
            stdout = output
            marker = "----- STDERR -----"
            if marker in stdout:
                stdout = stdout.split(marker, 1)[0]
            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if lines:
                return lines[-1]
        return None

    def _normalize_usage(self, raw: Dict[str, Any]) -> Optional[Dict[str, int]]:
        prompt = self._as_int(raw.get("prompt_tokens"))
        completion = self._as_int(raw.get("completion_tokens"))
        total = self._as_int(raw.get("total_tokens"))
        if None in {prompt, completion, total}:
            return None
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    def _count_tool_calls(self, payloads: List[Dict[str, Any]]) -> Optional[int]:
        count = 0
        found = False
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            tool_calls = payload.get("tool_calls")
            if isinstance(tool_calls, list):
                count += len(tool_calls)
                found = True
        return count if found else None

    def _extract_session_stats(
        self, session_id: Optional[str], prompt: str, run_started: float
    ) -> Tuple[Optional[Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        wire_path = self._find_session_wire_path(session_id)
        if not wire_path:
            return None, None, None, None
        return self._parse_session_wire(wire_path, prompt, run_started)

    def _find_session_wire_path(self, session_id: Optional[str]) -> Optional[Path]:
        if not session_id:
            return None
        workdir = str(self.workdir)
        workdir_md5 = hashlib.md5(workdir.encode("utf-8")).hexdigest()
        kaos_name = "local"
        meta_text = self._read_text(Path.home() / ".kimi" / "kimi.json")
        if meta_text:
            try:
                meta = json.loads(meta_text)
            except Exception:
                meta = None
            if isinstance(meta, dict):
                work_dirs = meta.get("work_dirs")
                if isinstance(work_dirs, list):
                    for item in work_dirs:
                        if not isinstance(item, dict):
                            continue
                        if item.get("path") != workdir:
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

    def _extract_turn_user_input_text(self, raw: Any) -> Optional[str]:
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, list):
            return None
        texts: List[str] = []
        for item in raw:
            if not isinstance(item, dict):
                return None
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text")
                if not isinstance(text, str):
                    return None
                texts.append(text)
        return "".join(texts)

    def _extract_model_name_from_log(self, session_id: Optional[str], prompt: str) -> Optional[str]:
        if not session_id:
            return None
        log_path = Path.home() / ".kimi" / "logs" / "kimi.log"
        text = self._read_text(log_path)
        if not text:
            return None
        lines = text.splitlines()
        session_markers = (
            f"Created new session: {session_id}",
            f"Switching to session: {session_id}",
            f"Session {session_id} not found, creating new session",
        )
        session_indexes = [
            idx for idx, line in enumerate(lines) if any(marker in line for marker in session_markers)
        ]
        if not session_indexes:
            return None
        start_idx = session_indexes[0]

        prompt_first_line = prompt.splitlines()[0] if prompt else ""
        anchor_idx: Optional[int] = None
        workdir_str = str(self.workdir)
        for idx in range(start_idx + 1, len(lines)):
            line = lines[idx]
            if "load_agents_md" in line and workdir_str in line:
                anchor_idx = idx
                break
            if prompt_first_line and "Running agent with command:" in line and prompt_first_line in line:
                anchor_idx = idx
                break
        if anchor_idx is None:
            return None

        model_prefix = "model='"
        for idx in range(anchor_idx, start_idx, -1):
            line = lines[idx]
            marker_pos = line.find("Using LLM model:")
            if marker_pos < 0:
                continue
            model_pos = line.find(model_prefix, marker_pos)
            if model_pos < 0:
                return None
            remain = line[model_pos + len(model_prefix) :]
            end_pos = remain.find("'")
            if end_pos <= 0:
                return None
            model_name = remain[:end_pos]
            return model_name or None
        return None

    def _parse_session_wire(
        self, path: Path, prompt: str, run_started: float
    ) -> Tuple[Optional[Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        usage_by_message_id: Dict[str, Dict[str, int]] = {}
        status_without_message_id: List[Dict[str, int]] = []
        status_message_ids: set[str] = set()
        tool_calls = 0
        model_name: Optional[str] = None

        def apply_status(payload: Any) -> bool:
            nonlocal model_name
            if not isinstance(payload, dict):
                return False
            token_usage = payload.get("token_usage")
            normalized = self._normalize_status_usage(token_usage)
            if normalized is None:
                return False
            message_id = payload.get("message_id")
            if isinstance(message_id, str) and message_id:
                previous = usage_by_message_id.get(message_id)
                if previous is None or normalized["total_tokens"] >= previous["total_tokens"]:
                    usage_by_message_id[message_id] = normalized
                status_message_ids.add(message_id)
            else:
                status_without_message_id.append(normalized)
            if model_name is None:
                candidate = payload.get("model")
                if isinstance(candidate, str) and candidate:
                    model_name = candidate
            return True

        in_target_turn = False
        turn_seen = False
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    return None, None, None, None
                if not isinstance(record, dict):
                    return None, None, None, None
                timestamp = record.get("timestamp")
                if isinstance(timestamp, (int, float)) and timestamp < (run_started - 2):
                    continue
                message = record.get("message")
                if message is None:
                    continue
                if not isinstance(message, dict):
                    return None, None, None, None
                message_type = message.get("type")
                if not isinstance(message_type, str):
                    return None, None, None, None

                if not in_target_turn:
                    if message_type != "TurnBegin":
                        continue
                    payload = message.get("payload")
                    if not isinstance(payload, dict):
                        return None, None, None, None
                    user_input = self._extract_turn_user_input_text(payload.get("user_input"))
                    if user_input is None or user_input != prompt:
                        continue
                    in_target_turn = True
                    turn_seen = True
                    continue

                if message_type == "TurnEnd":
                    break

                payload = message.get("payload")
                if message_type == "ToolCall":
                    tool_calls += 1
                    continue
                if message_type == "StatusUpdate":
                    if not apply_status(payload):
                        return None, None, None, None
                    continue
                if message_type != "SubagentEvent":
                    continue
                if not isinstance(payload, dict):
                    return None, None, None, None
                event = payload.get("event")
                if not isinstance(event, dict):
                    return None, None, None, None
                event_type = event.get("type")
                if not isinstance(event_type, str):
                    return None, None, None, None
                event_payload = event.get("payload")
                if event_type == "ToolCall":
                    tool_calls += 1
                    continue
                if event_type != "StatusUpdate":
                    continue
                if not apply_status(event_payload):
                    return None, None, None, None
        except Exception:
            return None, None, None, None

        if not turn_seen:
            return None, None, None, None

        usage_values = list(usage_by_message_id.values()) + status_without_message_id
        if usage_values:
            prompt_total = sum(value["prompt_tokens"] for value in usage_values)
            completion_total = sum(value["completion_tokens"] for value in usage_values)
            total_tokens = sum(value["total_tokens"] for value in usage_values)
            usage = {
                "prompt_tokens": prompt_total,
                "completion_tokens": completion_total,
                "total_tokens": total_tokens,
            }
        else:
            usage = None

        llm_calls = len(status_message_ids) + len(status_without_message_id)
        llm_calls_value = llm_calls or None
        return usage, tool_calls, llm_calls_value, model_name

    def _normalize_status_usage(self, raw: Any) -> Optional[Dict[str, int]]:
        if not isinstance(raw, dict):
            return None
        input_other = self._as_int(raw.get("input_other"))
        input_cache_read = self._as_int(raw.get("input_cache_read"))
        input_cache_creation = self._as_int(raw.get("input_cache_creation"))
        output = self._as_int(raw.get("output"))
        if None in {input_other, input_cache_read, input_cache_creation, output}:
            return None
        # Kimi can emit negative deltas for some input categories; clamp each field.
        prompt = max(0, input_other) + max(0, input_cache_read) + max(0, input_cache_creation)
        completion = max(0, output)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

    def _build_prompt_with_media_paths(self, prompt: str, images: list[Path], videos: list[Path]) -> str:
        image_paths: List[str] = []
        video_paths: List[str] = []
        for image in images:
            resolved = image.expanduser().resolve()
            image_paths.append(str(resolved))
        for video in videos:
            resolved = video.expanduser().resolve()
            video_paths.append(str(resolved))
        lines = [
            prompt,
            "",
            "You are provided with these media files.",
            "Use ReadMediaFile to open each file before answering.",
        ]
        if image_paths:
            lines.append("")
            lines.append("Images:")
            for path in image_paths:
                lines.append(f"- {path}")
        if video_paths:
            lines.append("")
            lines.append("Videos:")
            for path in video_paths:
                lines.append(f"- {path}")
        return "\n".join(lines)

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None
