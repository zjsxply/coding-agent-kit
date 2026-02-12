from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class CopilotAgent(CodingAgent):
    name = "copilot"
    display_name = "GitHub Copilot CLI"
    binary = "copilot"

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = self._npm_install("@github/copilot", scope, version=version)
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
        return None

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        log_dir = self._prepare_log_dir()
        model = os.environ.get("COPILOT_MODEL")
        env = {
            "GH_TOKEN": os.environ.get("GH_TOKEN"),
            "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN"),
        }
        cmd = [
            "copilot",
            "--prompt",
            prompt,
            "--yolo",
            "--no-ask-user",
            "--log-level",
            "info",
            "--log-dir",
            str(log_dir),
        ]
        if model:
            cmd.extend(["--model", model])
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(output)
        payloads.extend(self._load_log_payloads(log_dir))
        usage, models_usage = self._extract_usage(payloads)
        if usage is None:
            usage = self._extract_usage_text(output)
        tool_calls = self._count_tool_calls(payloads)
        output_path = self._write_output(self.name, output)
        models_usage = self._ensure_models_usage(models_usage, usage, model)
        response = self._extract_response(payloads, output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            telemetry_log=str(log_dir),
            response=response,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["copilot", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _prepare_log_dir(self) -> Path:
        root = os.environ.get("CAKIT_OUTPUT_DIR")
        base = Path(root) if root else Path.home() / ".cache" / "cakit"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_dir = base / "copilot-logs" / stamp
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _load_log_payloads(self, log_dir: Path) -> List[Dict[str, Any]]:
        if not log_dir.exists():
            return []
        payloads: List[Dict[str, Any]] = []
        files = [path for path in log_dir.rglob("*") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            payloads.extend(load_json_payloads(text))
        return payloads

    def _extract_usage_text(self, text: str) -> Optional[Dict[str, int]]:
        if not text:
            return None
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        for pattern in (r"prompt\s*[_ ]?tokens?\s*[:=]\s*(\d+)", r"input\s*[_ ]?tokens?\s*[:=]\s*(\d+)"):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                prompt_tokens = self._as_int(match.group(1))
                break
        for pattern in (
            r"completion\s*[_ ]?tokens?\s*[:=]\s*(\d+)",
            r"output\s*[_ ]?tokens?\s*[:=]\s*(\d+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                completion_tokens = self._as_int(match.group(1))
                break
        match = re.search(r"total\s*[_ ]?tokens?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        if match:
            total_tokens = self._as_int(match.group(1))
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        if total_tokens is None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        return {
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
            "total_tokens": total_tokens or 0,
        }

    def _extract_usage(
        self, payloads: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, int]], Dict[str, Dict[str, int]]]:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        models_usage: Dict[str, Dict[str, int]] = {}
        found = False
        for payload in payloads:
            usage = self._find_usage(payload)
            if not usage:
                continue
            found = True
            model = payload.get("model") or payload.get("model_name") or payload.get("modelName")
            if model:
                entry = models_usage.setdefault(
                    str(model),
                    {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                )
                entry["prompt_tokens"] += usage.get("prompt_tokens", 0)
                entry["completion_tokens"] += usage.get("completion_tokens", 0)
                entry["total_tokens"] += usage.get("total_tokens", 0)
            totals["prompt_tokens"] += usage.get("prompt_tokens", 0)
            totals["completion_tokens"] += usage.get("completion_tokens", 0)
            totals["total_tokens"] += usage.get("total_tokens", 0)
        if not found:
            return None, models_usage
        return totals, models_usage

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
            for key in ("response", "reply", "assistant_response", "assistantResponse", "final", "answer", "output"):
                add_text(payload.get(key))
            if payload.get("role") == "assistant":
                add_from_content(payload.get("content"))
            payload_type = payload.get("type")
            if payload_type in {"assistant", "final", "assistant_message"}:
                add_text(payload.get("text") or payload.get("message"))
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
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "promptTokens",
            "completionTokens",
            "totalTokens",
            "inputTokens",
            "outputTokens",
        ):
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
        if prompt is None:
            prompt = self._as_int(raw.get("input_tokens"))
        if completion is None:
            completion = self._as_int(raw.get("output_tokens"))
        if prompt is None:
            prompt = self._as_int(raw.get("promptTokens"))
        if completion is None:
            completion = self._as_int(raw.get("completionTokens"))
        if prompt is None:
            prompt = self._as_int(raw.get("inputTokens"))
        if completion is None:
            completion = self._as_int(raw.get("outputTokens"))
        if total is None:
            total = self._as_int(raw.get("totalTokens"))
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

    def _usage_totals(
        self, usage: Optional[Dict[str, int]], models_usage: Dict[str, Dict[str, int]]
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        if usage:
            return (
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
            )
        if models_usage:
            prompt_tokens = sum(v.get("prompt_tokens", 0) for v in models_usage.values())
            completion_tokens = sum(v.get("completion_tokens", 0) for v in models_usage.values())
            total_tokens = sum(v.get("total_tokens", 0) for v in models_usage.values())
            return prompt_tokens, completion_tokens, total_tokens
        return None, None, None

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None
