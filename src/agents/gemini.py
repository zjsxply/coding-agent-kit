from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class GeminiAgent(CodingAgent):
    name = "gemini"
    display_name = "Google Gemini CLI"
    binary = "gemini"
    supports_images = True
    supports_videos = True

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="@google/gemini-cli", scope=scope, version=version)

    def configure(self) -> Optional[str]:
        settings_path = Path.home() / ".gemini" / "settings.json"
        telemetry_path = Path.home() / ".gemini" / "telemetry.log"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["telemetry"] = {
            "enabled": True,
            "target": "local",
            "otlpEndpoint": "",
            "otlpProtocol": "http",
            "logPrompts": True,
            "outfile": str(telemetry_path),
        }
        settings_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
        return str(settings_path)

    def _run_impl(
        self,
        prompt: str,
        images: Optional[list[Path]] = None,
        videos: Optional[list[Path]] = None,
        reasoning_effort: Optional[str] = None,
        model_override: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        model = model_override or os.environ.get("GEMINI_MODEL")
        images = images or []
        videos = videos or []
        if images or videos:
            prompt, _ = self._build_symbolic_media_prompt(
                prompt,
                [*images, *videos],
            )
        telemetry_path = Path.home() / ".gemini" / "telemetry.log"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        if not (Path.home() / ".gemini" / "settings.json").exists():
            self.configure()
        env = {
            "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY"),
            "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY"),
            "GOOGLE_GEMINI_BASE_URL": os.environ.get("GOOGLE_GEMINI_BASE_URL"),
            "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        }
        cmd = [
            "gemini",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--approval-mode",
            "yolo",
        ]
        if model:
            cmd.extend(["--model", model])
        result = self._run(cmd, env, base_env=base_env)
        output = result.output
        payload = self._parse_output_json(output)
        models_usage, llm_calls, tool_calls = self._extract_stats(payload)
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            telemetry_log=str(telemetry_path),
            response=self._extract_response(payload),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_text(["gemini", "--version"])

    def _parse_output_json(self, output: str) -> Optional[Dict[str, Any]]:
        stdout = self._stdout_only(output).strip()
        if not stdout:
            return None
        parsed = self._extract_last_json_value(stdout)
        if isinstance(parsed, dict):
            return parsed
        return None

    def _extract_stats(
        self, data: Optional[Dict[str, Any]]
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        return self._extract_gemini_style_stats(data)

    def _extract_response(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        response = payload.get("response")
        if isinstance(response, str):
            cleaned = response.strip()
            if cleaned:
                return cleaned
        return None
