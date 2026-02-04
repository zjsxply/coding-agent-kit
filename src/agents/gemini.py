from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class GeminiAgent(CodeAgent):
    name = "gemini"
    display_name = "Google Gemini CLI"
    binary = "gemini"

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

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        model = os.environ.get("GEMINI_MODEL") or os.environ.get("GOOGLE_GEMINI_MODEL")
        images = images or []
        if images:
            image_paths: List[str] = []
            for path in images:
                try:
                    ref = str(path.relative_to(self.workdir))
                except Exception:
                    ref = str(path)
                image_paths.append(ref)
            quoted = ", ".join(json.dumps(item) for item in image_paths)
            prompt = (
                f"Please call read_many_files(paths=[{quoted}]) to load these image files before answering.\n\n{prompt}"
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
            "--output-format",
            "json",
            "--approval-mode",
            "yolo",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        result = self._run(cmd, env)
        output = result.output
        payloads = load_json_payloads(output)
        usage, models_usage, tool_calls = self._extract_usage(payloads)
        output_path = self._write_output(self.name, output)
        prompt_tokens = usage.get("prompt_tokens") if usage else None
        completion_tokens = usage.get("completion_tokens") if usage else None
        total_tokens = usage.get("total_tokens") if usage else None
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            models_usage=models_usage,
            tool_calls=tool_calls,
            telemetry_log=str(telemetry_path),
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["gemini", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _extract_usage(
        self, payloads: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, int]], Dict[str, Dict[str, int]], Optional[int]]:
        data = next((payload for payload in payloads if isinstance(payload, dict) and "stats" in payload), None)
        models_usage: Dict[str, Dict[str, int]] = {}
        tool_calls: Optional[int] = None
        if not isinstance(data, dict):
            return None, models_usage, tool_calls
        stats = data.get("stats")
        if not isinstance(stats, dict):
            return None, models_usage, tool_calls
        models = stats.get("models")
        if isinstance(models, dict):
            for model_name, model_stats in models.items():
                usage = self._extract_tokens_payload(model_stats)
                if usage:
                    models_usage[str(model_name)] = usage
        tools = stats.get("tools")
        if isinstance(tools, dict):
            total_calls = self._as_int(tools.get("totalCalls"))
            if total_calls is not None:
                tool_calls = total_calls
        totals = None
        if models_usage:
            totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            for usage in models_usage.values():
                totals["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
                totals["completion_tokens"] += usage.get("completion_tokens", 0) or 0
                totals["total_tokens"] += usage.get("total_tokens", 0) or 0
        return totals, models_usage, tool_calls

    def _extract_tokens_payload(self, payload: Any) -> Optional[Dict[str, int]]:
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

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return None
