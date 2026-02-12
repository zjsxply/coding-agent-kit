from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class GeminiAgent(CodingAgent):
    name = "gemini"
    display_name = "Google Gemini CLI"
    binary = "gemini"
    supports_images = True
    supports_videos = True

    def install(self, *, scope: str = "user") -> InstallResult:
        result = self._npm_install("@google/gemini-cli", scope)
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
        base_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        model = os.environ.get("GEMINI_MODEL")
        images = images or []
        videos = videos or []
        if images or videos:
            staged_paths = self._stage_media_files([*images, *videos], stage_dir_name=".cakit-media")
            prompt = self._build_media_reference_prompt(prompt, staged_paths)
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
        response = self._extract_response(payload)
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
            telemetry_log=str(telemetry_path),
            response=response,
            exit_code=run_exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["gemini", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

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
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        tool_calls: Optional[int] = None
        if not isinstance(data, dict):
            return models_usage, llm_calls, tool_calls
        stats = data.get("stats")
        if not isinstance(stats, dict):
            return {}, None, None
        models = stats.get("models")
        if not isinstance(models, dict) or not models:
            return {}, None, None

        total_llm_calls = 0
        for model_name, model_stats in models.items():
            if not isinstance(model_name, str) or not model_name.strip():
                return {}, None, None
            if not isinstance(model_stats, dict):
                return {}, None, None
            usage = self._extract_tokens_payload(model_stats.get("tokens"))
            api_calls = self._extract_total_requests(model_stats.get("api"))
            if usage is None or api_calls is None:
                return {}, None, None
            models_usage[model_name] = usage
            total_llm_calls += api_calls

        llm_calls = total_llm_calls
        tools = stats.get("tools")
        if not isinstance(tools, dict):
            return {}, None, None
        total_calls = self._as_int(tools.get("totalCalls"))
        if total_calls is None:
            return {}, None, None
        tool_calls = total_calls
        return models_usage, llm_calls, tool_calls

    def _extract_response(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        response = payload.get("response")
        if isinstance(response, str):
            cleaned = response.strip()
            if cleaned:
                return cleaned
        return None

    def _extract_tokens_payload(self, tokens: Any) -> Optional[Dict[str, int]]:
        if not isinstance(tokens, dict):
            return None
        prompt = self._as_int(tokens.get("prompt"))
        completion = self._as_int(tokens.get("candidates"))
        total = self._as_int(tokens.get("total"))
        if prompt is None or completion is None or total is None:
            return None
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    def _extract_total_requests(self, api: Any) -> Optional[int]:
        if not isinstance(api, dict):
            return None
        return self._as_int(api.get("totalRequests"))

    def _build_media_reference_prompt(self, prompt: str, media_paths: List[Path]) -> str:
        lines = ["Use these local media files as additional context before answering:"]
        for path in media_paths:
            try:
                rel_path = path.relative_to(self.workdir).as_posix()
            except Exception:
                rel_path = path.as_posix()
            lines.append(f"@{rel_path}")
        lines.append("")
        lines.append(prompt)
        return "\n".join(lines)
