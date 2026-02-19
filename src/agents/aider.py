from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class AiderAgent(CodingAgent):
    name = "aider"
    display_name = "Aider"
    binary = "aider"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        del scope
        package_spec = "aider-chat"
        if version and version.strip():
            package_spec = f"aider-chat=={version.strip()}"
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
        del videos
        images = images or []
        settings, env_error = self._resolve_runtime_settings(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_dir = Path(tempfile.mkdtemp(prefix="cakit-aider-"))
        analytics_log = run_dir / "analytics.jsonl"
        input_history = run_dir / "input.history"
        chat_history = run_dir / "chat.history.md"
        llm_history = run_dir / "llm.history.log"

        cmd = [
            "aider",
            "--message",
            prompt,
            "--model",
            settings["model"],
            "--edit-format",
            "ask",
            "--no-git",
            "--yes-always",
            "--no-show-model-warnings",
            "--no-show-release-notes",
            "--no-check-update",
            "--no-fancy-input",
            "--no-suggest-shell-commands",
            "--no-pretty",
            "--no-stream",
            "--analytics-log",
            str(analytics_log),
            "--no-analytics",
            "--input-history-file",
            str(input_history),
            "--chat-history-file",
            str(chat_history),
            "--llm-history-file",
            str(llm_history),
        ]
        if reasoning_effort:
            cmd.extend(["--reasoning-effort", reasoning_effort])
        cmd.extend(str(image) for image in images)

        env: Dict[str, str] = {
            "AIDER_OPENAI_API_KEY": settings["api_key"],
        }
        if settings.get("api_base"):
            env["AIDER_OPENAI_API_BASE"] = settings["api_base"]

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        events = self._load_analytics_events(analytics_log)
        models_usage, llm_calls, tool_calls, total_cost = self._extract_stats_from_analytics(events)
        response = self._extract_response(
            llm_history_path=llm_history,
            chat_history_path=chat_history,
            output=output,
        )

        output_path = self._write_output(self.name, output)
        trajectory_content = self._build_trajectory_content(
            output=output,
            output_path=output_path,
            analytics_log=analytics_log,
            chat_history=chat_history,
            llm_history=llm_history,
        )
        trajectory_path = self._write_trajectory(self.name, trajectory_content)

        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            total_cost=total_cost,
            telemetry_log=str(analytics_log) if analytics_log.exists() else None,
            response=response,
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        first = self._version_first_line(["aider", "--version"])
        prefixed = self._second_token_if_prefixed(first, prefix="aider")
        if prefixed:
            return prefixed
        return first

    def _resolve_runtime_settings(
        self, *, model_override: Optional[str]
    ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        api_key = self._resolve_openai_api_key("AIDER_OPENAI_API_KEY")
        api_base = self._resolve_openai_base_url("AIDER_OPENAI_API_BASE")
        model = self._resolve_openai_model("AIDER_MODEL", model_override=model_override)

        missing: list[tuple[str, str]] = []
        if api_key is None:
            missing.append(("AIDER_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if model is None:
            missing.append(("AIDER_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, self._missing_env_with_fallback_message(missing)

        return {
            "api_key": api_key,
            "api_base": api_base or "",
            "model": self._normalize_model(model),
        }, None

    @staticmethod
    def _normalize_model(model: str) -> str:
        normalized = model.strip()
        if "/" in normalized:
            return normalized
        if ":" in normalized:
            provider, model_name = normalized.split(":", 1)
            provider = provider.strip()
            model_name = model_name.strip()
            if provider and model_name:
                return f"{provider}/{model_name}"
        return f"openai/{normalized}"

    def _load_analytics_events(self, path: Path) -> Optional[list[Dict[str, Any]]]:
        if not path.exists():
            return None
        text = self._read_text(path)
        if not text:
            return None

        events: list[Dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except Exception:
                return None
            if not isinstance(event, dict):
                return None
            events.append(event)
        if not events:
            return None
        return events

    def _extract_stats_from_analytics(
        self,
        events: Optional[list[Dict[str, Any]]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[float]]:
        if not isinstance(events, list):
            return {}, None, None, None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0
        total_cost: Optional[float] = None

        for event in events:
            event_name = event.get("event")
            if not isinstance(event_name, str) or not event_name:
                return {}, None, None, None

            if event_name.startswith("command_"):
                tool_calls += 1

            if event_name != "message_send":
                continue

            properties = event.get("properties")
            if not isinstance(properties, dict):
                return {}, None, None, None
            model_name = properties.get("main_model")
            if not isinstance(model_name, str) or not model_name.strip():
                return {}, None, None, None

            prompt_tokens = self._as_int(properties.get("prompt_tokens"))
            completion_tokens = self._as_int(properties.get("completion_tokens"))
            total_tokens = self._as_int(properties.get("total_tokens"))
            if prompt_tokens is None or completion_tokens is None or total_tokens is None:
                return {}, None, None, None

            usage = models_usage.setdefault(
                model_name.strip(),
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            usage["prompt_tokens"] += prompt_tokens
            usage["completion_tokens"] += completion_tokens
            usage["total_tokens"] += total_tokens
            llm_calls += 1

            candidate_total_cost = self._as_float(properties.get("total_cost"))
            if candidate_total_cost is not None:
                total_cost = candidate_total_cost

        if llm_calls < 1 or not models_usage:
            return {}, None, None, None
        return models_usage, llm_calls, tool_calls, total_cost

    def _extract_response(
        self,
        *,
        llm_history_path: Path,
        chat_history_path: Path,
        output: str,
    ) -> Optional[str]:
        response = self._extract_response_from_llm_history(llm_history_path)
        if response:
            return response
        response = self._extract_response_from_chat_history(chat_history_path)
        if response:
            return response
        return self._extract_response_from_output(output)

    def _extract_response_from_llm_history(self, path: Path) -> Optional[str]:
        text = self._read_text(path)
        if not text:
            return None
        lines = text.splitlines()
        start_index: Optional[int] = None
        for index, line in enumerate(lines):
            if line.startswith("LLM RESPONSE "):
                start_index = index + 1
        if start_index is None:
            return None

        block = []
        for line in lines[start_index:]:
            if line.startswith("TO LLM ") or line.startswith("LLM RESPONSE "):
                break
            block.append(line)
        if not block:
            return None

        response_lines: list[str] = []
        capture_raw = False
        for line in block:
            if line == "ASSISTANT":
                capture_raw = True
                continue
            if line.startswith("ASSISTANT "):
                response_lines.append(line[len("ASSISTANT ") :])
                capture_raw = False
                continue
            if capture_raw:
                response_lines.append(line)

        if not response_lines:
            return None
        while response_lines and not response_lines[0].strip():
            response_lines.pop(0)
        while response_lines and not response_lines[-1].strip():
            response_lines.pop()
        if not response_lines:
            return None

        response = "\n".join(response_lines).strip()
        if not response:
            return None
        return response

    def _extract_response_from_chat_history(self, path: Path) -> Optional[str]:
        text = self._read_text(path)
        if not text:
            return None
        lines = text.splitlines()
        user_indices = [index for index, line in enumerate(lines) if line.startswith("#### ")]
        if not user_indices:
            return None

        start = user_indices[-1] + 1
        block: list[str] = []
        for line in lines[start:]:
            if line.startswith("#### "):
                break
            if line.startswith(">"):
                if any(item.strip() for item in block):
                    break
                continue
            if line.startswith("# aider chat started"):
                continue
            block.append(line)

        while block and not block[0].strip():
            block.pop(0)
        while block and not block[-1].strip():
            block.pop()
        if not block:
            return None
        response = "\n".join(block).strip()
        if not response:
            return None
        return response

    def _extract_response_from_output(self, output: str) -> Optional[str]:
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return None
        filtered = [line for line in lines if not line.startswith("Tokens:") and not line.startswith("Cost:")]
        if not filtered:
            return None
        return filtered[-1]

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _build_trajectory_content(
        self,
        *,
        output: str,
        output_path: Path,
        analytics_log: Path,
        chat_history: Path,
        llm_history: Path,
    ) -> str:
        parts = [output]

        analytics_text = self._read_text(analytics_log)
        if analytics_text and analytics_text.strip():
            parts.append(f"----- ANALYTICS LOG ({analytics_log}) -----\n{analytics_text}")

        chat_history_text = self._read_text(chat_history)
        if chat_history_text and chat_history_text.strip():
            parts.append(f"----- CHAT HISTORY ({chat_history}) -----\n{chat_history_text}")

        llm_history_text = self._read_text(llm_history)
        if llm_history_text and llm_history_text.strip():
            parts.append(f"----- LLM HISTORY ({llm_history}) -----\n{llm_history_text}")

        return format_trace_text("\n\n".join(parts), source=str(output_path))
