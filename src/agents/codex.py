from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodeAgent
from ..models import InstallResult, RunResult
from ..utils import load_json_payloads


class CodexAgent(CodeAgent):
    name = "codex"
    display_name = "OpenAI Codex"
    binary = "codex"

    def install(self, *, scope: str = "user") -> InstallResult:
        result = self._npm_install("@openai/codex", scope)
        config_path = self.configure()
        ok = result.exit_code == 0
        details = result.output
        if ok and config_path is None:
            ok = False
            details = (details + "\n" if details else "") + "codex configure failed"
        return InstallResult(
            agent=self.name,
            version=self.get_version() if ok else None,
            ok=ok,
            details=details,
            config_path=config_path,
        )

    def configure(self) -> Optional[str]:
        use_oauth = self._use_oauth()
        model = os.environ.get("CODEX_MODEL") or "gpt-5-codex"
        lines = [
            "project_root_markers = []",
            f"model = \"{model}\"",
        ]
        if not use_oauth:
            base_url = os.environ.get("CODEX_API_BASE") or "https://api.openai.com/v1"
            provider = "custom"
            lines.extend(
                [
                    f"model_provider = \"{provider}\"",
                    "",
                    f"[model_providers.{provider}]",
                    "name = \"custom\"",
                    f"base_url = \"{base_url}\"",
                    "env_key = \"OPENAI_API_KEY\"",
                    "wire_api = \"responses\"",
                ]
            )
        otel_exporter = os.environ.get("CODEX_OTEL_EXPORTER")
        otel_endpoint = os.environ.get("CODEX_OTEL_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        otel_protocol = os.environ.get("CODEX_OTEL_PROTOCOL")
        otel_env = os.environ.get("CODEX_OTEL_ENVIRONMENT")
        otel_log_prompt = os.environ.get("CODEX_OTEL_LOG_USER_PROMPT")
        if otel_endpoint and not otel_exporter:
            otel_exporter = "otlp-http"
        if otel_exporter:
            lines.append("")
            lines.append("[otel]")
            lines.append(f"exporter = \"{otel_exporter}\"")
            if otel_env:
                lines.append(f"environment = \"{otel_env}\"")
            if otel_log_prompt is not None:
                value = str(otel_log_prompt).strip().lower() in {"1", "true", "yes", "y"}
                lines.append(f"log_user_prompt = {str(value).lower()}")
            if otel_endpoint:
                lines.append("")
                lines.append(f"[otel.exporter.\"{otel_exporter}\"]")
                lines.append(f"endpoint = \"{otel_endpoint}\"")
                if otel_protocol:
                    lines.append(f"protocol = \"{otel_protocol}\"")
        config = "\n".join(lines) + "\n"
        path = os.path.expanduser("~/.codex/config.toml")
        self._write_text(Path(path), config)
        return path

    def run(self, prompt: str, images: Optional[list[Path]] = None) -> RunResult:
        images = images or []
        if self._use_oauth() and not self._auth_path().exists():
            message = f"codex OAuth is enabled but auth file not found at {self._auth_path()}; run `codex login`."
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
                llm_calls=None,
                total_cost=None,
                telemetry_log=None,
                exit_code=2,
                output_path=str(output_path),
                raw_output=message,
            )
        otel_endpoint = os.environ.get("CODEX_OTEL_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        env = {
            "OPENAI_API_BASE": os.environ.get("CODEX_API_BASE"),
        }
        if not self._use_oauth():
            env["OPENAI_API_KEY"] = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
        cmd = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if images:
            image_arg = ",".join(str(path) for path in images)
            cmd.extend(["--image", image_arg])
        cmd.append("-")
        result = self._run(cmd, env, input_text=prompt)
        output = result.output
        payloads = load_json_payloads(output)
        usage, models_usage = self._extract_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        output_path = self._write_output(self.name, output)
        prompt_tokens, completion_tokens, total_tokens = self._usage_totals(usage, models_usage)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            models_usage=models_usage,
            tool_calls=tool_calls,
            telemetry_log=otel_endpoint,
            exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["codex", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _use_oauth(self) -> bool:
        value = os.environ.get("CODEX_USE_OAUTH")
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _auth_path(self) -> Path:
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home).expanduser() / "auth.json"
        return Path.home() / ".codex" / "auth.json"

    def _extract_usage(self, payloads: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, int]], Dict[str, Dict[str, int]]]:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        models_usage: Dict[str, Dict[str, int]] = {}
        found = False
        for payload in payloads:
            usage = self._find_usage(payload)
            if not usage:
                continue
            found = True
            model = payload.get("model") or payload.get("model_name")
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

    def _count_tool_calls(self, payloads: List[Dict[str, Any]]) -> int:
        count = 0
        for payload in payloads:
            if self._looks_like_tool_call(payload):
                count += 1
        return count

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
        self,
        usage: Optional[Dict[str, int]],
        models_usage: Dict[str, Dict[str, int]],
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
