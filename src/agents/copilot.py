from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class CopilotAgent(CodingAgent):
    name = "copilot"
    display_name = "GitHub Copilot CLI"
    binary = "copilot"
    supports_images = True
    supports_videos = False
    _LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[^ ]+\s+\[[A-Z]+\]\s?(.*)$")

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
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        log_dir = self._prepare_log_dir()
        prompt, _, _ = self._build_natural_media_prompt(
            prompt,
            images=images,
            videos=None,
            tool_name="view",
        )
        model = model_override or os.environ.get("COPILOT_MODEL")
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
            "debug",
            "--log-dir",
            str(log_dir),
        ]
        if model:
            cmd.extend(["--model", model])
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        model_calls = self._load_model_call_payloads(log_dir)
        models_usage, llm_calls, tool_calls = self._extract_stats(model_calls)
        response = self._extract_response(model_calls, output)
        run_exit_code = self._resolve_strict_run_exit_code(
            command_exit_code=result.exit_code,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            response=response,
        )
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            telemetry_log=str(log_dir),
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
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

    def _load_model_call_payloads(self, log_dir: Path) -> List[Dict[str, Any]]:
        if not log_dir.exists():
            return []
        payloads: List[Dict[str, Any]] = []
        for path in sorted(log_dir.glob("process-*.log")):
            payloads.extend(self._parse_model_calls_from_log(path))
        return payloads

    def _parse_model_calls_from_log(self, path: Path) -> List[Dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return []
        messages = [self._extract_log_message(line) for line in lines]
        payloads: List[Dict[str, Any]] = []
        index = 0
        while index < len(messages):
            if messages[index].strip() != "data:":
                index += 1
                continue
            start = index + 1
            while start < len(messages) and not messages[start].strip():
                start += 1
            if start >= len(messages):
                break
            payload, next_index = self._parse_json_block(messages, start)
            index = max(next_index, index + 1)
            if self._is_model_call_payload(payload):
                payloads.append(payload)
        return payloads

    def _parse_json_block(self, messages: List[str], start: int) -> Tuple[Optional[Dict[str, Any]], int]:
        if start >= len(messages):
            return None, start
        if not messages[start].lstrip().startswith("{"):
            return None, start + 1
        parts: List[str] = []
        for index in range(start, len(messages)):
            parts.append(messages[index])
            candidate = "\n".join(parts).strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed, index + 1
            return None, index + 1
        return None, len(messages)

    def _extract_log_message(self, line: str) -> str:
        match = self._LOG_LINE_RE.match(line)
        if match:
            return match.group(1)
        return line

    def _is_model_call_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        usage = payload.get("usage")
        model = payload.get("model")
        choices = payload.get("choices")
        if not isinstance(usage, dict):
            return False
        if not isinstance(model, str) or not model.strip():
            return False
        if not isinstance(choices, list):
            return False
        return True

    def _extract_stats(
        self, payloads: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        if not payloads:
            return {}, None, None
        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0
        for payload in payloads:
            if not self._is_model_call_payload(payload):
                return {}, None, None
            model = str(payload.get("model")).strip()
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                return {}, None, None
            prompt_tokens = self._as_int(usage.get("prompt_tokens"))
            completion_tokens = self._as_int(usage.get("completion_tokens"))
            total_tokens = self._as_int(usage.get("total_tokens"))
            if prompt_tokens is None or completion_tokens is None or total_tokens is None:
                return {}, None, None
            entry = models_usage.setdefault(
                model,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens
            entry["total_tokens"] += total_tokens

            call_tool_calls = self._extract_tool_calls(payload.get("choices"))
            if call_tool_calls is None:
                return {}, None, None
            tool_calls += call_tool_calls
            llm_calls += 1
        return models_usage, llm_calls, tool_calls

    def _extract_tool_calls(self, choices: Any) -> Optional[int]:
        if not isinstance(choices, list):
            return None
        total = 0
        for choice in choices:
            if not isinstance(choice, dict):
                return None
            message = choice.get("message")
            if message is None:
                continue
            if not isinstance(message, dict):
                return None
            tool_calls = message.get("tool_calls")
            if tool_calls is None:
                continue
            if not isinstance(tool_calls, list):
                return None
            total += len(tool_calls)
        return total

    def _extract_response(self, payloads: List[Dict[str, Any]], output: str) -> Optional[str]:
        for payload in reversed(payloads):
            choices = payload.get("choices")
            if not isinstance(choices, list):
                continue
            for choice in reversed(choices):
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    cleaned = content.strip()
                    if cleaned:
                        return cleaned
        stdout = self._stdout_only(output).strip()
        if stdout:
            return stdout
        return None
