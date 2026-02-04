from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class KimiAgent(CodeAgent):
    name = "kimi"
    display_name = "Kimi Code CLI"
    binary = "kimi"

    def install(self) -> InstallResult:
        if self._has_uv():
            result = self._run(["uv", "tool", "install", "--python", "3.13", "kimi-cli"])
        else:
            result = self._run(["python", "-m", "pip", "install", "kimi-cli"])
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
        if not api_key:
            return None
        base_url = os.environ.get("KIMI_BASE_URL") or "https://api.kimi.com/coding/v1"
        model_name = os.environ.get("KIMI_MODEL_NAME") or "kimi-for-coding"
        config = (
            'default_model = "cakit-kimi"\n'
            "default_thinking = false\n\n"
            "[providers.cakit-kimi]\n"
            'type = "kimi"\n'
            f'base_url = "{base_url}"\n'
            f'api_key = "{api_key}"\n\n'
            "[models.cakit-kimi]\n"
            'provider = "cakit-kimi"\n'
            f'model = "{model_name}"\n'
            "max_context_size = 262144\n"
        )
        path = Path.home() / ".kimi" / "config.toml"
        self._write_text(path, config)
        return str(path)

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        images = images or []
        if images:
            message = "image input is not supported for kimi in cakit run."
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
        env = {
            "KIMI_API_KEY": os.environ.get("KIMI_API_KEY"),
            "KIMI_BASE_URL": os.environ.get("KIMI_BASE_URL"),
            "KIMI_MODEL_NAME": os.environ.get("KIMI_MODEL_NAME"),
        }
        cmd = [
            "kimi",
            "--print",
            "--prompt",
            prompt,
            "--output-format",
            "stream-json",
            "--yolo",
            "--work-dir",
            str(self.workdir),
        ]
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
        result = self._run(["kimi", "info", "--json"])
        if result.exit_code == 0:
            try:
                data = json.loads(result.output.strip())
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("kimi_cli_version"):
                return str(data.get("kimi_cli_version"))
        return None

    def _has_uv(self) -> bool:
        return self._run(["bash", "-lc", "command -v uv"]).exit_code == 0

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
        found = False
        for payload in payloads:
            if self._looks_like_tool_call(payload):
                count += 1
                found = True
            tool_calls = payload.get("tool_calls")
            if isinstance(tool_calls, list):
                count += len(tool_calls)
                found = True
        return count if found else None

    def _looks_like_tool_call(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("tool", "tool_name", "toolName", "tool_call", "toolCall", "tool_use", "toolUse"):
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
