from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class CursorAgent(CodeAgent):
    name = "cursor"
    display_name = "Cursor Agent"
    binary = "cursor-agent"

    def install(self, *, scope: str = "user") -> InstallResult:
        result = self._run(["bash", "-c", "curl -fsS https://cursor.com/install | bash"])
        ok = result.exit_code == 0
        details = result.output
        return InstallResult(
            agent=self.name,
            version=self.get_version(),
            ok=ok,
            details=details,
            config_path=None,
        )

    def configure(self) -> Optional[str]:
        return None

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        images = images or []
        if images:
            message = "image input is not supported for cursor-agent in cakit run."
            output_path = self._write_output(self.name, message)
            return RunResult(
                agent=self.name,
                agent_version=self.get_version(),
                runtime_seconds=0.0,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                models_usage={},
                tool_calls=None,
                exit_code=2,
                output_path=str(output_path),
                raw_output=message,
            )
        model = os.environ.get("CURSOR_MODEL")
        endpoint = os.environ.get("CURSOR_API_BASE")
        env = {"CURSOR_API_KEY": os.environ.get("CURSOR_API_KEY")}
        cmd = [
            "cursor-agent",
            "-p",
            prompt,
            "--print",
            "--output-format",
            "stream-json",
            "--force",
        ]
        if model:
            cmd.extend(["--model", model])
        if endpoint:
            cmd.extend(["--endpoint", endpoint])
        result = self._run(cmd, env)
        output = result.output
        payloads = load_json_payloads(output)
        usage = self._extract_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        output_path = self._write_output(self.name, output)
        prompt_tokens, completion_tokens, total_tokens = self._usage_totals(usage)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            models_usage={},
            tool_calls=tool_calls,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["cursor-agent", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        for payload in payloads:
            usage = self._find_usage(payload)
            if usage:
                return usage
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
