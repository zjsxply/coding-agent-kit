from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class OpenHandsAgent(CodeAgent):
    name = "openhands"
    display_name = "OpenHands"
    binary = "openhands"

    def install(self, *, scope: str = "user") -> InstallResult:
        if self._has_uv():
            result = self._run(["uv", "tool", "install", "openhands", "--python", "3.12"])
        else:
            result = self._run(["python", "-m", "pip", "install", "openhands"])
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

    def run(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        images = images or []
        if images:
            message = "image input is not supported for openhands in cakit run."
            output_path = self._write_output(self.name, message)
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                models_usage={},
                tool_calls=None,
                response=message,
                exit_code=2,
                output_path=str(output_path),
                raw_output=message,
            )
        model = os.environ.get("OPENHANDS_LLM_MODEL") or os.environ.get("LLM_MODEL")
        api_key = os.environ.get("OPENHANDS_LLM_API_KEY") or os.environ.get("LLM_API_KEY")
        base_url = os.environ.get("OPENHANDS_LLM_BASE_URL") or os.environ.get("LLM_BASE_URL")
        env = {
            "OPENHANDS_LLM_MODEL": model,
            "OPENHANDS_LLM_API_KEY": api_key,
            "OPENHANDS_LLM_BASE_URL": base_url,
            "LLM_MODEL": model,
            "LLM_API_KEY": api_key,
            "LLM_BASE_URL": base_url,
        }
        cmd = ["openhands", "--headless", "--json", "-t", prompt]
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(output)
        usage = self._extract_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        output_path = self._write_output(self.name, output)
        model_name = os.environ.get("OPENHANDS_LLM_MODEL") or os.environ.get("LLM_MODEL")
        models_usage = self._ensure_models_usage({}, usage, model_name)
        response = self._extract_response(payloads, output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            response=response,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["openhands", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _has_uv(self) -> bool:
        return self._run(["bash", "-lc", "command -v uv"]).exit_code == 0

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        for payload in payloads:
            usage = self._find_usage(payload)
            if usage:
                return usage
        return None

    def _extract_response(self, payloads: List[Dict[str, Any]], output: str) -> Optional[str]:
        messages: List[str] = []

        def add_text(value: Any) -> None:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    messages.append(cleaned)

        def add_from_content(content: Any) -> None:
            if isinstance(content, list):
                parts: List[str] = []
                for entry in content:
                    if not isinstance(entry, dict):
                        continue
                    text = entry.get("text") or entry.get("output_text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                if parts:
                    messages.append("\n".join(parts))
            else:
                add_text(content)

        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key in ("final_response", "final_answer", "final", "response", "answer", "output"):
                if isinstance(payload.get(key), str):
                    add_text(payload.get(key))
            if payload.get("role") == "assistant":
                add_from_content(payload.get("content"))
            message = payload.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                add_from_content(message.get("content"))

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

    def _find_usage(self, payload: Any) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        if "usage" in payload and isinstance(payload["usage"], dict):
            return self._normalize_usage(payload["usage"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            if key in payload:
                return self._normalize_usage(payload)
        for value in payload.values():
            if isinstance(value, dict):
                nested = self._find_usage(value)
                if nested:
                    return nested
            if isinstance(value, list):
                for item in value:
                    nested = self._find_usage(item)
                    if nested:
                        return nested
        return None

    def _normalize_usage(self, raw: Dict[str, Any]) -> Dict[str, int]:
        prompt = self._as_int(raw.get("prompt_tokens"))
        completion = self._as_int(raw.get("completion_tokens"))
        total = self._as_int(raw.get("total_tokens"))
        if prompt is None and "input_tokens" in raw:
            prompt = self._as_int(raw.get("input_tokens"))
        if completion is None and "output_tokens" in raw:
            completion = self._as_int(raw.get("output_tokens"))
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {
            "prompt_tokens": prompt or 0,
            "completion_tokens": completion or 0,
            "total_tokens": total or 0,
        }

    def _count_tool_calls(self, payloads: List[Dict[str, Any]]) -> Optional[int]:
        count = 0
        for payload in payloads:
            if self._looks_like_tool_call(payload):
                count += 1
        return count

    def _looks_like_tool_call(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("tool", "tool_name", "toolName", "tool_call", "toolCall", "tool_use", "toolUse", "action"):
            if key in payload:
                return True
        event_type = payload.get("type") or payload.get("event") or payload.get("name")
        if isinstance(event_type, str) and "tool" in event_type.lower():
            return True
        for value in payload.values():
            if isinstance(value, dict) and self._looks_like_tool_call(value):
                return True
            if isinstance(value, list):
                for item in value:
                    if self._looks_like_tool_call(item):
                        return True
        return False

    def _usage_totals(self, usage: Optional[Dict[str, int]]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        if not usage:
            return None, None, None
        return (
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None
