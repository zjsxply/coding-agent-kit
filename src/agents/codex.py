from __future__ import annotations

import json
import datetime
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class CodexAgent(CodingAgent):
    name = "codex"
    display_name = "OpenAI Codex"
    binary = "codex"
    supports_images = True
    supports_videos = False

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        result = self._npm_install("@openai/codex", scope, version=version)
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
        model = os.environ.get("CODEX_MODEL")
        lines = ["project_root_markers = []"]
        if model:
            lines.append(f"model = \"{model}\"")
        api_key = os.environ.get("CODEX_API_KEY")
        if not use_oauth and api_key:
            base_url = os.environ.get("CODEX_API_BASE")
            provider = "custom"
            lines.extend(
                [
                    f"model_provider = \"{provider}\"",
                    "",
                    f"[model_providers.{provider}]",
                    "name = \"custom\"",
                    "env_key = \"CODEX_API_KEY\"",
                    "wire_api = \"responses\"",
                ]
            )
            if base_url:
                lines.insert(lines.index("env_key = \"CODEX_API_KEY\""), f"base_url = \"{base_url}\"")
        otel_exporter = os.environ.get("CODEX_OTEL_EXPORTER")
        otel_endpoint = os.environ.get("CODEX_OTEL_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        otel_protocol = os.environ.get("CODEX_OTEL_PROTOCOL")
        otel_env = os.environ.get("CODEX_OTEL_ENVIRONMENT")
        otel_log_prompt = os.environ.get("CODEX_OTEL_LOG_USER_PROMPT")
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
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            config_path = Path(codex_home).expanduser() / "config.toml"
        else:
            config_path = Path(os.path.expanduser("~/.codex/config.toml"))
        self._write_text(config_path, config)
        return str(config_path)

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
        if self._use_oauth() and not self._auth_path().exists():
            message = f"codex OAuth is enabled but auth file not found at {self._auth_path()}; run `codex login`."
            output_path = self._write_output(self.name, message)
            trajectory_path = self._write_trajectory(self.name, format_trace_text(message, source=str(output_path)))
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
                trajectory_path=str(trajectory_path) if trajectory_path else None,
            )
        otel_endpoint = os.environ.get("CODEX_OTEL_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        env = {
            "OPENAI_API_BASE": os.environ.get("CODEX_API_BASE"),
        }
        use_oauth = self._use_oauth()
        api_key = None if use_oauth else os.environ.get("CODEX_API_KEY")
        if api_key:
            env["CODEX_API_KEY"] = api_key
            env["OPENAI_API_KEY"] = api_key
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
        model = model_override or os.environ.get("CODEX_MODEL")
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
        if images:
            image_arg = ",".join(str(path) for path in images)
            cmd.extend(["--image", image_arg])
        cmd.append("-")
        unset_env = None
        if use_oauth or (not api_key):
            unset_env = ["OPENAI_API_KEY", "CODEX_API_KEY"]
        result = self._run(cmd, env, input_text=prompt, unset_env=unset_env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(output)
        models_usage, llm_calls = self._extract_models_usage(payloads)
        tool_calls = self._count_tool_calls(payloads)
        response = self._read_last_message(last_message_path)
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
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
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        result = self._run(["codex", "--version"])
        text = result.output.strip()
        if result.exit_code == 0 and text:
            return text
        return None

    def _use_oauth(self) -> bool:
        value = os.environ.get("CAKIT_CODEX_USE_OAUTH")
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
        thread_id = None
        for payload in payloads:
            if payload.get("type") == "thread.started":
                thread_id = payload.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    break
        if not thread_id:
            return {}, None
        session_path = self._find_session_file(thread_id)
        if not session_path:
            return {}, None
        model, usage, llm_calls = self._parse_session_file(session_path)
        if not usage:
            return {}, None
        if not model:
            return {"unknown": usage}, llm_calls
        return {model: usage}, llm_calls

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

    def _codex_home(self) -> Path:
        home = os.environ.get("CODEX_HOME")
        if home:
            return Path(home).expanduser()
        return Path.home() / ".codex"

    def _find_session_file(self, thread_id: str) -> Optional[Path]:
        root = self._codex_home() / "sessions"
        if not root.exists():
            return None
        date_path = self._thread_id_date_path(thread_id)
        if not date_path:
            return None
        search_root = root / date_path
        if not search_root.exists():
            return None
        matches = sorted(search_root.glob(f"rollout-*{thread_id}.jsonl"))
        if len(matches) == 1:
            return matches[0]
        return None

    def _thread_id_date_path(self, thread_id: str) -> Optional[Path]:
        hex_str = thread_id.replace("-", "").lower()
        if len(hex_str) != 32:
            return None
        if hex_str[12] != "7":
            return None
        try:
            timestamp_ms = int(hex_str[:12], 16)
        except Exception:
            return None
        timestamp = datetime.datetime.fromtimestamp(timestamp_ms / 1000, datetime.timezone.utc)
        return Path(f"{timestamp:%Y/%m/%d}")

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
                    input_tokens = self._as_int(total.get("input_tokens"))
                    cached_input_tokens = self._as_int(total.get("cached_input_tokens"))
                    output_tokens = self._as_int(total.get("output_tokens"))
                    reasoning_output_tokens = self._as_int(total.get("reasoning_output_tokens"))
                    total_tokens = self._as_int(total.get("total_tokens"))
                    if None in {
                        input_tokens,
                        cached_input_tokens,
                        output_tokens,
                        reasoning_output_tokens,
                        total_tokens,
                    }:
                        return model, None, None
                    prompt = input_tokens + cached_input_tokens
                    completion = output_tokens + reasoning_output_tokens
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

    def _count_tool_calls(self, payloads: List[Dict[str, Any]]) -> Optional[int]:
        tool_types = {"mcp_tool_call", "collab_tool_call", "command_execution", "web_search"}
        tool_item_ids: set[str] = set()
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type")
            if event_type not in {"item.started", "item.completed"}:
                continue
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type not in tool_types:
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                return None
            tool_item_ids.add(item_id)
        return len(tool_item_ids)
