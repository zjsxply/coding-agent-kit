from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class QwenAgent(CodingAgent):
    name = "qwen"
    display_name = "Qwen Code"
    binary = "qwen"
    supports_images = True
    supports_videos = True

    def install(self, *, scope: str = "user") -> InstallResult:
        result = self._npm_install("@qwen-code/qwen-code", scope)
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        tavily_key = os.environ.get("TAVILY_API_KEY")
        google_key = os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY")
        google_se_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
        providers = [{"type": "dashscope"}]
        default_provider = "dashscope"
        if tavily_key:
            providers.append({"type": "tavily", "apiKey": tavily_key})
            default_provider = "tavily"
        if google_key and google_se_id:
            providers.append({"type": "google", "apiKey": google_key, "searchEngineId": google_se_id})
            if default_provider == "dashscope":
                default_provider = "google"
        settings = {
            "webSearch": {
                "provider": providers,
                "default": default_provider,
            },
            "permissions": {
                "defaultMode": "yolo",
                "confirmShellCommands": False,
                "confirmFileEdits": False,
            },
            "telemetry": {
                "enabled": True,
                "target": "local",
                "otlpEndpoint": "",
                "logPrompts": True,
                "outfile": str(Path.home() / ".qwen" / "telemetry.log"),
            },
        }
        path = Path.home() / ".qwen" / "settings.json"
        self._write_text(path, json.dumps(settings, ensure_ascii=True, indent=2))
        return str(path)

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
        if images or videos:
            media_refs: List[str] = []
            for path in [*images, *videos]:
                try:
                    ref = str(path.relative_to(self.workdir))
                except Exception:
                    ref = str(path)
                media_refs.append(f"@{{{ref}}}")
            prompt = "\n".join(media_refs) + "\n\n" + prompt
        telemetry_path = str(Path.home() / ".qwen" / "telemetry.log")
        qwen_key = os.environ.get("QWEN_OPENAI_API_KEY")
        qwen_base = os.environ.get("QWEN_OPENAI_BASE_URL")
        qwen_model = os.environ.get("QWEN_OPENAI_MODEL") or os.environ.get("QWEN_MODEL")
        qwen_google_api_key = os.environ.get("CAKIT_QWEN_GOOGLE_API_KEY")
        env = {
            "OPENAI_API_KEY": qwen_key,
            "OPENAI_API_BASE": qwen_base,
            "OPENAI_BASE_URL": qwen_base,
            "OPENAI_MODEL": qwen_model,
            "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY"),
            "GOOGLE_API_KEY": qwen_google_api_key,
            "GOOGLE_SEARCH_ENGINE_ID": os.environ.get("GOOGLE_SEARCH_ENGINE_ID"),
        }
        cmd = [
            "qwen",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--approval-mode",
            "yolo",
            "--telemetry",
            "--telemetry-target",
            "local",
            "--telemetry-otlp-endpoint",
            "",
            "--telemetry-outfile",
            telemetry_path,
            "--telemetry-log-prompts",
        ]
        if qwen_model:
            cmd.extend(["--model", qwen_model])
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        telemetry = self._read_text(Path(telemetry_path))
        usage = self._extract_usage_from_telemetry(telemetry) or self._extract_usage_from_output(output)
        tool_calls = self._count_tool_calls_from_telemetry(telemetry)
        if tool_calls is None:
            tool_calls = self._count_tool_calls(output)
        output_path = self._write_output(self.name, output)
        models_usage = self._ensure_models_usage({}, usage, qwen_model)
        response = self._extract_response(load_json_payloads(output), output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            telemetry_log=telemetry_path,
            response=response,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["qwen", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _extract_usage_from_telemetry(self, text: Optional[str]) -> Optional[Dict[str, int]]:
        if not text:
            return None
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        found = False
        for payload in load_json_payloads(text):
            usage = self._extract_usage_from_mapping(payload) or self._extract_usage_from_tokens_payload(payload)
            if not usage:
                usage = self._extract_usage_from_dotted_keys(payload)
            if not usage:
                continue
            found = True
            totals["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
            totals["completion_tokens"] += usage.get("completion_tokens", 0) or 0
            totals["total_tokens"] += usage.get("total_tokens", 0) or 0
        if found:
            return totals
        return None

    def _extract_usage_from_output(self, output: str) -> Optional[Dict[str, int]]:
        if not output:
            return None
        for payload in load_json_payloads(output):
            usage = self._extract_usage_from_mapping(payload)
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

        def add_from_message(message: Any) -> None:
            if isinstance(message, dict):
                add_text(message.get("content"))
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
            event_type = payload.get("type")
            if isinstance(event_type, str) and "output_text" in event_type:
                add_text(payload.get("text"))
            for key in ("output", "final", "response", "answer"):
                add_text(payload.get(key))

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

    def _extract_usage_from_tokens_payload(self, payload: Any) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return None
        prompt = self._as_int(tokens.get("prompt"))
        if prompt is None:
            prompt = self._as_int(tokens.get("input"))
        completion = self._as_int(tokens.get("candidates"))
        if completion is None:
            completion = self._as_int(tokens.get("output"))
        total = self._as_int(tokens.get("total"))
        if prompt is None and completion is None and total is None:
            return None
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {
            "prompt_tokens": prompt or 0,
            "completion_tokens": completion or 0,
            "total_tokens": total or 0,
        }

    def _extract_usage_from_dotted_keys(self, payload: Any) -> Optional[Dict[str, int]]:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        found = False

        def visit(obj: Any) -> None:
            nonlocal found
            if isinstance(obj, dict):
                prompt = None
                completion = None
                total = None
                for key, value in obj.items():
                    if isinstance(value, (dict, list)):
                        visit(value)
                        continue
                    if not isinstance(key, str):
                        continue
                    key_lower = key.lower()
                    val = self._as_int(value)
                    if val is None:
                        continue
                    if key_lower.endswith("prompt_tokens") or key_lower.endswith("input_tokens"):
                        prompt = (prompt or 0) + val
                    elif key_lower.endswith("completion_tokens") or key_lower.endswith("output_tokens"):
                        completion = (completion or 0) + val
                    elif key_lower.endswith("total_tokens"):
                        total = (total or 0) + val
                if prompt is not None or completion is not None or total is not None:
                    found = True
                    totals["prompt_tokens"] += prompt or 0
                    totals["completion_tokens"] += completion or 0
                    totals["total_tokens"] += total if total is not None else (prompt or 0) + (completion or 0)
            elif isinstance(obj, list):
                for item in obj:
                    visit(item)

        visit(payload)
        if found:
            return totals
        return None

    def _extract_usage_from_mapping(self, payload: Any) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        if "usage" in payload and isinstance(payload["usage"], dict):
            return self._normalize_usage(payload["usage"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            if key in payload:
                return self._normalize_usage(payload)
        for value in payload.values():
            if isinstance(value, dict):
                nested = self._extract_usage_from_mapping(value)
                if nested:
                    return nested
            if isinstance(value, list):
                for item in value:
                    nested = self._extract_usage_from_mapping(item)
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

    def _count_tool_calls_from_telemetry(self, text: Optional[str]) -> Optional[int]:
        if not text:
            return None
        payloads = load_json_payloads(text)
        count = 0
        for payload in payloads:
            if self._looks_like_tool_call(payload):
                count += 1
        return count

    def _count_tool_calls(self, output: str) -> Optional[int]:
        payloads = load_json_payloads(output)
        if payloads:
            count = 0
            for payload in payloads:
                if self._looks_like_tool_call(payload):
                    count += 1
            return count
        return None

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
