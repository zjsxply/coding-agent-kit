from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class AuggieAgent(CodingAgent):
    name = "auggie"
    display_name = "Auggie"
    binary = "auggie"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="@augmentcode/auggie", scope=scope, version=version)

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
        images = images or []
        log_dir = Path(tempfile.mkdtemp(prefix="cakit-auggie-"))
        log_path = log_dir / "auggie.log"

        requested_model = self._normalize_text(model_override or os.environ.get("CAKIT_AUGGIE_MODEL"))
        env = {
            "AUGMENT_API_TOKEN": os.environ.get("AUGMENT_API_TOKEN"),
            "AUGMENT_API_URL": os.environ.get("AUGMENT_API_URL"),
            "AUGMENT_SESSION_AUTH": os.environ.get("AUGMENT_SESSION_AUTH"),
            "GITHUB_API_TOKEN": os.environ.get("GITHUB_API_TOKEN"),
            "AUGMENT_DISABLE_AUTO_UPDATE": "1",
        }
        cmd = [
            "auggie",
            "--print",
            "--quiet",
            "--output-format",
            "json",
            "--workspace-root",
            str(self.workdir),
            "--instruction",
            prompt,
            "--log-file",
            str(log_path),
            "--log-level",
            "debug",
        ]
        if requested_model:
            cmd.extend(["--model", requested_model])
        for image in images:
            cmd.extend(["--image", str(image)])

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(self._stdout_only(output))
        result_payload = self._extract_result_payload(payloads)
        models_usage, llm_calls, tool_calls = self._extract_stats(result_payload)

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            telemetry_log=str(log_path),
            response=self._extract_response(result_payload),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_first_line(["auggie", "--version"])

    def _extract_result_payload(self, payloads: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        result_payload: Optional[Dict[str, Any]] = None
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "result":
                result_payload = payload
        return result_payload

    def _extract_stats(
        self, payload: Optional[Dict[str, Any]]
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        return self._extract_gemini_style_stats(payload)

    def _extract_response(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        response = payload.get("result")
        if not isinstance(response, str):
            return None
        cleaned = response.strip()
        if not cleaned:
            return None
        return cleaned
