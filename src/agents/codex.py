from __future__ import annotations

import json
import os
import time
import uuid
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
                models_usage={},
                tool_calls=None,
                llm_calls=None,
                total_cost=None,
                telemetry_log=None,
                response=message,
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
        last_message_path = self._create_last_message_path()
        cmd = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--output-last-message",
            str(last_message_path),
        ]
        if images:
            image_arg = ",".join(str(path) for path in images)
            cmd.extend(["--image", image_arg])
        cmd.append("-")
        result = self._run(cmd, env, input_text=prompt)
        output = result.output
        payloads = load_json_payloads(output)
        models_usage, llm_calls = self._extract_models_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        response = self._read_last_message(last_message_path)
        output_path = self._write_output(self.name, output)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            telemetry_log=otel_endpoint,
            response=response,
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

    def _extract_models_usage(
        self, payloads: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int]]:
        turn_calls = self._count_turn_calls(payloads)
        thread_id = None
        for payload in payloads:
            if payload.get("type") == "thread.started":
                thread_id = payload.get("thread_id")
                if thread_id:
                    break
        if thread_id:
            session_path = self._find_session_file(thread_id)
            if session_path:
                model, usage, llm_calls = self._parse_session_file(session_path)
                if usage:
                    model_name = model or (os.environ.get("CODEX_MODEL") or "gpt-5-codex")
                    return {model_name: usage}, llm_calls or turn_calls
        usage = None
        model_name = None
        for payload in payloads:
            if model_name is None:
                model_name = payload.get("model") or payload.get("model_name")
            if usage is None:
                usage = self._find_usage(payload)
        if usage:
            model_name = model_name or (os.environ.get("CODEX_MODEL") or "gpt-5-codex")
            return self._ensure_models_usage({}, usage, model_name), turn_calls
        return {}, turn_calls

    def _create_last_message_path(self) -> Path:
        root = os.environ.get("CAKIT_OUTPUT_DIR")
        if root:
            output_dir = Path(root)
        else:
            output_dir = Path.home() / ".cache" / "cakit"
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex
        return output_dir / f"{self.name}-{stamp}-{unique}-last-message.txt"

    def _read_last_message(self, path: Path) -> Optional[str]:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
                if text:
                    return text
        except Exception:
            return None
        return None


    def _count_turn_calls(self, payloads: List[Dict[str, Any]]) -> Optional[int]:
        count = 0
        for payload in payloads:
            event_type = payload.get("type")
            if event_type in {"turn.completed", "turn.failed"}:
                count += 1
        return count or None

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

    def _codex_home(self) -> Path:
        home = os.environ.get("CODEX_HOME")
        if home:
            return Path(home).expanduser()
        return Path.home() / ".codex"

    def _find_session_file(self, thread_id: str) -> Optional[Path]:
        root = self._codex_home() / "sessions"
        if not root.exists():
            return None
        matches = sorted(root.glob(f"**/rollout-*{thread_id}.jsonl"))
        if matches:
            return matches[-1]
        return None

    def _parse_session_file(
        self, path: Path
    ) -> Tuple[Optional[str], Optional[Dict[str, int]], Optional[int]]:
        model: Optional[str] = None
        usage: Optional[Dict[str, int]] = None
        llm_calls = 0
        last_signature: Optional[Tuple[int, int, int]] = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("type") == "turn_context":
                    payload = record.get("payload")
                    if isinstance(payload, dict) and payload.get("model"):
                        model = payload.get("model")
                if record.get("type") == "event_msg":
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "token_count":
                        continue
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    total = info.get("total_token_usage")
                    if not isinstance(total, dict):
                        continue
                    prompt = self._as_int(total.get("input_tokens")) or 0
                    completion = self._as_int(total.get("output_tokens")) or 0
                    total_tokens = self._as_int(total.get("total_tokens"))
                    if total_tokens is None:
                        total_tokens = prompt + completion
                    signature = (prompt, completion, total_tokens)
                    if signature != last_signature:
                        llm_calls += 1
                        last_signature = signature
                    usage = {
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "total_tokens": total_tokens,
                    }
        except Exception:
            return model, usage, None
        return model, usage, (llm_calls or None)

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
