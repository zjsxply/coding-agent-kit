from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
        result = self._npm_install("@augmentcode/auggie", scope, version=version)
        config_path = self.configure()
        ok = result.exit_code == 0
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=result.output,
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
        images = images or []
        log_dir = Path(tempfile.mkdtemp(prefix="cakit-auggie-"))
        log_path = log_dir / "auggie.log"

        requested_model = self._normalize_model_name(model_override or os.environ.get("CAKIT_AUGGIE_MODEL"))
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
        response = self._extract_response(result_payload)

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
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
            telemetry_log=str(log_path),
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["auggie", "--version"])
        text = result.output.strip()
        if result.exit_code != 0 or not text:
            return None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        return lines[0]

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
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        if not isinstance(payload, dict):
            return models_usage, None, None

        stats = payload.get("stats")
        if not isinstance(stats, dict):
            return {}, None, None

        models = stats.get("models")
        if not isinstance(models, dict) or not models:
            return {}, None, None

        llm_calls = 0
        for model_name, model_stats in models.items():
            if not isinstance(model_name, str) or not model_name.strip():
                return {}, None, None
            if not isinstance(model_stats, dict):
                return {}, None, None

            tokens = model_stats.get("tokens")
            api = model_stats.get("api")
            if not isinstance(tokens, dict) or not isinstance(api, dict):
                return {}, None, None

            prompt_tokens = self._as_int(tokens.get("prompt"))
            completion_tokens = self._as_int(tokens.get("candidates"))
            total_tokens = self._as_int(tokens.get("total"))
            model_calls = self._as_int(api.get("totalRequests"))
            if prompt_tokens is None or completion_tokens is None or total_tokens is None or model_calls is None:
                return {}, None, None

            models_usage[model_name] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
            llm_calls += model_calls

        tools = stats.get("tools")
        if not isinstance(tools, dict):
            return {}, None, None
        tool_calls = self._as_int(tools.get("totalCalls"))
        if tool_calls is None:
            return {}, None, None
        return models_usage, llm_calls, tool_calls

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

    @staticmethod
    def _normalize_model_name(value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized
