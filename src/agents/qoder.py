from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class QoderAgent(CodingAgent):
    name = "qoder"
    display_name = "Qoder"
    binary = "qodercli"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="@qoder-ai/qodercli", scope=scope, version=version)

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
        del videos, reasoning_effort
        images = images or []
        model = self._normalize_text(model_override or os.environ.get("CAKIT_QODER_MODEL"))
        env = {
            "QODER_PERSONAL_ACCESS_TOKEN": os.environ.get("QODER_PERSONAL_ACCESS_TOKEN"),
        }
        cmd = [
            "qodercli",
            "-q",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        for image in images:
            cmd.extend(["--attachment", str(image)])

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(self._stdout_only(output))
        models_usage, llm_calls, tool_calls, response = self._extract_stats(payloads)
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        telemetry_path = Path.home() / ".qoder" / "logs" / "qodercli.log"
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            telemetry_log=str(telemetry_path) if telemetry_path.exists() else None,
            response=response,
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_first_line(["qodercli", "--version"])

    def _extract_stats(
        self, payloads: list[Dict[str, Any]]
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        if not payloads:
            return {}, None, None, None
        event_types: set[str] = set()
        for payload in payloads:
            if not isinstance(payload, dict):
                return {}, None, None, None
            event_type = payload.get("type")
            if not isinstance(event_type, str) or not event_type:
                return {}, None, None, None
            event_types.add(event_type)
        if "qoder_message" in event_types:
            return self._extract_qoder_message_stats(payloads)
        if "message_start" in event_types and "message_stop" in event_types:
            return self._extract_stream_message_stats(payloads)
        return {}, None, None, None

    def _extract_qoder_message_stats(
        self, payloads: list[Dict[str, Any]]
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        request_ids: set[str] = set()
        tool_calls = 0
        responses: list[str] = []
        assistant_seen = False
        for payload in payloads:
            if payload.get("type") != "qoder_message":
                continue
            message = payload.get("message")
            if not isinstance(message, dict):
                return {}, None, None, None
            role = message.get("role")
            if not isinstance(role, str):
                return {}, None, None, None
            if role != "assistant":
                continue
            assistant_seen = True

            response_meta = message.get("response_meta")
            if not isinstance(response_meta, dict):
                return {}, None, None, None
            model_name = self._normalize_text(response_meta.get("model_name"))
            request_id = self._normalize_text(response_meta.get("request_id"))
            if model_name is None or request_id is None:
                return {}, None, None, None

            usage = message.get("usage")
            if not isinstance(usage, dict):
                return {}, None, None, None
            parsed_usage = self._extract_total_usage(usage)
            if parsed_usage is None:
                return {}, None, None, None

            entry = models_usage.setdefault(
                model_name,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            entry["prompt_tokens"] += parsed_usage["prompt_tokens"]
            entry["completion_tokens"] += parsed_usage["completion_tokens"]
            entry["total_tokens"] += parsed_usage["total_tokens"]
            request_ids.add(request_id)

            message_tool_calls = message.get("tool_calls")
            if message_tool_calls is None:
                pass
            elif isinstance(message_tool_calls, list):
                tool_calls += len(message_tool_calls)
            else:
                return {}, None, None, None

            content = message.get("content")
            if not isinstance(content, str):
                return {}, None, None, None
            cleaned = content.strip()
            if cleaned:
                responses.append(cleaned)

        if not assistant_seen:
            return {}, None, None, None
        if not models_usage:
            return {}, None, None, None
        if not request_ids:
            return {}, None, None, None
        return models_usage, len(request_ids), tool_calls, (responses[-1] if responses else None)

    def _extract_stream_message_stats(
        self, payloads: list[Dict[str, Any]]
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[str]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        request_ids: set[str] = set()
        tool_calls = 0
        responses: list[str] = []
        current: Optional[Dict[str, Any]] = None
        assistant_messages = 0
        for payload in payloads:
            event_type = payload.get("type")
            if event_type == "message_start":
                message = payload.get("message")
                if not isinstance(message, dict):
                    return {}, None, None, None
                role = message.get("role")
                if not isinstance(role, str):
                    return {}, None, None, None
                if role != "assistant":
                    current = None
                    continue
                model_name = self._normalize_text(message.get("model"))
                request_id = self._normalize_text(message.get("id"))
                usage = message.get("usage")
                if model_name is None or request_id is None:
                    return {}, None, None, None
                if not isinstance(usage, dict):
                    return {}, None, None, None
                prompt_tokens = self._extract_stream_prompt_tokens(usage)
                completion_tokens = self._extract_stream_completion_tokens(usage)
                if prompt_tokens is None or completion_tokens is None:
                    return {}, None, None, None
                current = {
                    "model_name": model_name,
                    "request_id": request_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "response_parts": [],
                }
                assistant_messages += 1
                continue

            if event_type == "content_block_start":
                if current is None:
                    continue
                content_block = payload.get("content_block")
                if not isinstance(content_block, dict):
                    return {}, None, None, None
                block_type = content_block.get("type")
                if not isinstance(block_type, str):
                    return {}, None, None, None
                if block_type == "tool_use":
                    tool_calls += 1
                    continue
                if block_type != "text":
                    continue
                text = content_block.get("text")
                if text is None:
                    continue
                if not isinstance(text, str):
                    return {}, None, None, None
                if text:
                    current["response_parts"].append(text)
                continue

            if event_type == "content_block_delta":
                if current is None:
                    continue
                delta = payload.get("delta")
                if not isinstance(delta, dict):
                    return {}, None, None, None
                delta_type = delta.get("type")
                if not isinstance(delta_type, str):
                    return {}, None, None, None
                if delta_type != "text_delta":
                    continue
                text = delta.get("text")
                if not isinstance(text, str):
                    return {}, None, None, None
                if text:
                    current["response_parts"].append(text)
                continue

            if event_type == "message_delta":
                if current is None:
                    continue
                usage = payload.get("usage")
                if not isinstance(usage, dict):
                    return {}, None, None, None
                completion_tokens = self._extract_stream_completion_tokens(usage)
                if completion_tokens is None:
                    return {}, None, None, None
                current["completion_tokens"] = completion_tokens
                continue

            if event_type == "message_stop":
                if current is None:
                    continue
                prompt_tokens = current["prompt_tokens"]
                completion_tokens = current["completion_tokens"]
                total_tokens = prompt_tokens + completion_tokens
                model_name = current["model_name"]
                request_id = current["request_id"]
                entry = models_usage.setdefault(
                    model_name,
                    {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                )
                entry["prompt_tokens"] += prompt_tokens
                entry["completion_tokens"] += completion_tokens
                entry["total_tokens"] += total_tokens
                request_ids.add(request_id)
                response = "".join(current["response_parts"]).strip()
                if response:
                    responses.append(response)
                current = None
                continue

        if assistant_messages < 1:
            return {}, None, None, None
        if current is not None:
            return {}, None, None, None
        if not models_usage:
            return {}, None, None, None
        if not request_ids:
            return {}, None, None, None
        return models_usage, len(request_ids), tool_calls, (responses[-1] if responses else None)

    def _extract_total_usage(self, usage: Dict[str, Any]) -> Optional[Dict[str, int]]:
        prompt_tokens = self._as_int(usage.get("total_prompt_tokens"))
        completion_tokens = self._as_int(usage.get("total_completed_tokens"))
        total_tokens = self._as_int(usage.get("total_tokens"))
        if prompt_tokens is None or completion_tokens is None or total_tokens is None:
            return None
        if prompt_tokens < 0 or completion_tokens < 0 or total_tokens < 0:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_stream_prompt_tokens(self, usage: Dict[str, Any]) -> Optional[int]:
        input_tokens = self._as_int(usage.get("input_tokens"))
        cache_read_tokens = self._as_int(usage.get("cache_read_tokens"))
        if input_tokens is None:
            return None
        if cache_read_tokens is None:
            cache_read_tokens = 0
        if input_tokens < 0 or cache_read_tokens < 0:
            return None
        return input_tokens + cache_read_tokens

    def _extract_stream_completion_tokens(self, usage: Dict[str, Any]) -> Optional[int]:
        output_tokens = self._as_int(usage.get("output_tokens"))
        if output_tokens is None:
            return None
        if output_tokens < 0:
            return None
        return output_tokens
