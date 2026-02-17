from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text


class OpenClawAgent(CodingAgent):
    name = "openclaw"
    display_name = "OpenClaw"
    binary = "openclaw"
    supports_images = False
    supports_videos = False

    _SAFE_PROVIDER_ID_RE = re.compile(r"[^a-z0-9._-]+")
    _TOOL_CALL_TYPES = {"tool_use", "toolcall", "tool_call"}
    _DEFAULT_CONTEXT_WINDOW = 32_000
    _DEFAULT_MAX_TOKENS = 32_000

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="openclaw", scope=scope, version=version)

    def configure(self) -> Optional[str]:
        settings, err = self._resolve_runtime_settings(model_override=None)
        if err is not None:
            return None

        cmd = self._build_onboard_command(settings)
        result = self._run(cmd)
        if result.exit_code != 0:
            return None
        config_path = self._openclaw_config_path()
        if not config_path.exists():
            return None
        self._patch_custom_provider_limits(config_path)
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
        del images, videos
        settings, env_error = self._resolve_runtime_settings(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_home = Path(tempfile.mkdtemp(prefix="cakit-openclaw-home-"))
        session_id = f"cakit-{uuid.uuid4().hex}"
        env = {
            "OPENAI_API_KEY": settings["api_key"],
            "OPENCLAW_HOME": str(run_home),
        }
        onboard = self._run(self._build_onboard_command(settings), env=env, base_env=base_env)
        if onboard.exit_code != 0:
            message = "openclaw non-interactive onboard failed during run"
            return self._build_error_run_result(
                message=message,
                cakit_exit_code=1,
                command_exit_code=onboard.exit_code,
                raw_output=onboard.output,
            )
        self._patch_custom_provider_limits(self._resolve_runtime_config_path(run_home))
        cmd = [
            "openclaw",
            "agent",
            "--local",
            "--agent",
            "main",
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--json",
        ]
        if reasoning_effort:
            cmd.extend(["--thinking", reasoning_effort])

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payload = self._parse_run_payload(output)
        response, usage, model_name = self._extract_stats_from_payload(payload)
        session_path = self._resolve_session_path(session_id, agent_id="main", env_source=env)
        llm_calls, tool_calls = self._extract_counts_from_transcript(session_path)

        output_path = self._write_output(self.name, output)
        trajectory_content = format_trace_text(output, source=str(output_path))
        trajectory_path = self._write_trajectory(self.name, trajectory_content)
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name) if usage is not None and model_name else {},
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            response=response,
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        return self._version_first_line(["openclaw", "--version"])

    def _resolve_runtime_settings(
        self, *, model_override: Optional[str]
    ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        api_key = self._normalize_text(os.environ.get("CAKIT_OPENCLAW_API_KEY"))

        base_url = self._normalize_text(os.environ.get("CAKIT_OPENCLAW_BASE_URL"))

        model_ref = self._normalize_text(model_override)
        if model_ref is None:
            model_ref = self._normalize_text(os.environ.get("CAKIT_OPENCLAW_MODEL"))

        provider_id = self._normalize_provider_id(os.environ.get("CAKIT_OPENCLAW_PROVIDER_ID"))
        model_id, provider_from_model = self._split_model_ref(model_ref)
        if provider_id is None:
            provider_id = provider_from_model

        missing: list[str] = []
        if api_key is None:
            missing.append("CAKIT_OPENCLAW_API_KEY")
        if base_url is None:
            missing.append("CAKIT_OPENCLAW_BASE_URL")
        if model_id is None:
            missing.append("CAKIT_OPENCLAW_MODEL")
        if missing:
            return None, self._missing_env_message(missing)

        resolved: Dict[str, str] = {
            "api_key": api_key,
            "base_url": base_url,
            "model_id": model_id,
        }
        if provider_id:
            resolved["provider_id"] = provider_id
        return resolved, None

    def _build_onboard_command(self, settings: Dict[str, str]) -> list[str]:
        cmd = [
            "openclaw",
            "onboard",
            "--non-interactive",
            "--accept-risk",
            "--mode",
            "local",
            "--auth-choice",
            "custom-api-key",
            "--custom-base-url",
            settings["base_url"],
            "--custom-model-id",
            settings["model_id"],
            "--custom-api-key",
            settings["api_key"],
            "--skip-channels",
            "--skip-skills",
            "--skip-health",
            "--skip-ui",
            "--skip-daemon",
            "--json",
        ]
        provider_id = settings.get("provider_id")
        if provider_id:
            cmd.extend(["--custom-provider-id", provider_id])
        return cmd

    def _normalize_provider_id(self, value: Optional[str]) -> Optional[str]:
        normalized = self._normalize_text(value)
        if normalized is None:
            return None
        lowered = normalized.lower()
        cleaned = self._SAFE_PROVIDER_ID_RE.sub("-", lowered).strip("-")
        if not cleaned:
            return None
        return cleaned

    def _split_model_ref(self, model_ref: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        normalized = self._normalize_text(model_ref)
        if normalized is None:
            return None, None
        if "/" not in normalized:
            return normalized, None
        provider, model_id = normalized.split("/", 1)
        model_id = model_id.strip()
        if not model_id:
            return None, None
        return model_id, self._normalize_provider_id(provider)

    def _openclaw_home(self) -> Path:
        root = os.environ.get("OPENCLAW_HOME")
        if root:
            return Path(root).expanduser()
        return Path.home() / ".openclaw"

    def _openclaw_config_path(self) -> Path:
        override = os.environ.get("OPENCLAW_CONFIG_PATH")
        if override:
            return Path(override).expanduser()
        return self._openclaw_home() / "openclaw.json"

    def _resolve_runtime_config_path(self, run_home: Path) -> Path:
        nested = run_home / ".openclaw" / "openclaw.json"
        if nested.exists():
            return nested
        direct = run_home / "openclaw.json"
        if direct.exists():
            return direct
        return nested

    def _patch_custom_provider_limits(self, config_path: Path) -> None:
        min_context_window = self._resolve_limit_env(
            "CAKIT_OPENCLAW_CONTEXT_WINDOW",
            self._DEFAULT_CONTEXT_WINDOW,
        )
        min_max_tokens = self._resolve_limit_env(
            "CAKIT_OPENCLAW_MAX_TOKENS",
            self._DEFAULT_MAX_TOKENS,
        )
        text = self._read_text(config_path)
        if not text:
            return
        try:
            payload = json.loads(text)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        models = payload.get("models")
        if not isinstance(models, dict):
            return
        providers = models.get("providers")
        if not isinstance(providers, dict):
            return

        changed = False
        for provider in providers.values():
            if not isinstance(provider, dict):
                continue
            provider_models = provider.get("models")
            if not isinstance(provider_models, list):
                continue
            for model in provider_models:
                if not isinstance(model, dict):
                    continue
                context_window = self._as_int(model.get("contextWindow"))
                max_tokens = self._as_int(model.get("maxTokens"))
                if context_window is None or context_window < min_context_window:
                    model["contextWindow"] = min_context_window
                    changed = True
                if max_tokens is None or max_tokens < min_max_tokens:
                    model["maxTokens"] = min_max_tokens
                    changed = True
        if changed:
            self._write_text(config_path, json.dumps(payload, ensure_ascii=True, indent=2))

    def _resolve_limit_env(self, env_key: str, default: int) -> int:
        raw = self._normalize_text(os.environ.get(env_key))
        if raw is None:
            return default
        try:
            value = int(raw)
        except Exception:
            return default
        if value <= 0:
            return default
        return value

    def _resolve_session_path(
        self,
        session_id: str,
        *,
        agent_id: str,
        env_source: Optional[Dict[str, str]] = None,
    ) -> Path:
        source = env_source if env_source is not None else os.environ
        roots: list[Path] = []
        state_dir = source.get("OPENCLAW_STATE_DIR")
        if state_dir:
            roots.append(Path(state_dir).expanduser())
        else:
            openclaw_home = source.get("OPENCLAW_HOME")
            if openclaw_home:
                home_root = Path(openclaw_home).expanduser()
                roots.append(home_root / ".openclaw")
                roots.append(home_root)
            else:
                roots.append(self._openclaw_home())

        for root in roots:
            candidate = root / "agents" / agent_id / "sessions" / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate
        return roots[0] / "agents" / agent_id / "sessions" / f"{session_id}.jsonl"

    def _parse_run_payload(self, output: str) -> Optional[Dict[str, Any]]:
        stdout = self._stdout_only(output).strip()
        if not stdout:
            return None
        last_json = self._extract_last_json_value(stdout)
        if not isinstance(last_json, dict):
            return None
        return last_json

    def _extract_stats_from_payload(
        self, payload: Optional[Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[Dict[str, int]], Optional[str]]:
        if not isinstance(payload, dict):
            return None, None, None
        response = self._extract_response(payload)
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            return response, None, None
        agent_meta = meta.get("agentMeta")
        if not isinstance(agent_meta, dict):
            return response, None, None
        usage_raw = agent_meta.get("usage")
        usage = self._normalize_usage(usage_raw)
        provider = agent_meta.get("provider")
        model = agent_meta.get("model")
        model_name = None
        if isinstance(provider, str) and provider.strip() and isinstance(model, str) and model.strip():
            model_name = f"{provider.strip()}/{model.strip()}"
        return response, usage, model_name

    def _extract_response(self, payload: Dict[str, Any]) -> Optional[str]:
        payloads = payload.get("payloads")
        if not isinstance(payloads, list):
            return None
        messages: list[str] = []
        for item in payloads:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                messages.append(text.strip())
        if not messages:
            return None
        return messages[-1]

    def _normalize_usage(self, usage_raw: Any) -> Optional[Dict[str, int]]:
        if not isinstance(usage_raw, dict):
            return None
        input_tokens = self._as_int(usage_raw.get("input"))
        output_tokens = self._as_int(usage_raw.get("output"))
        cache_read = self._as_int(usage_raw.get("cacheRead"))
        cache_write = self._as_int(usage_raw.get("cacheWrite"))
        total_tokens = self._as_int(usage_raw.get("total"))
        has_any_usage = any(
            value is not None for value in (input_tokens, output_tokens, cache_read, cache_write, total_tokens)
        )
        if not has_any_usage:
            return None
        prompt_tokens = (input_tokens or 0) + (cache_read or 0) + (cache_write or 0)
        completion_tokens = output_tokens or 0
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_counts_from_transcript(self, session_path: Path) -> Tuple[Optional[int], Optional[int]]:
        if not session_path.exists():
            return None, None

        llm_calls = 0
        tool_calls = 0
        try:
            for raw_line in session_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    return None, None
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in {"user", "assistant"}:
                    continue
                if role == "assistant":
                    usage = message.get("usage")
                    if isinstance(usage, dict):
                        normalized = self._normalize_usage(
                            {
                                "input": usage.get("input"),
                                "output": usage.get("output"),
                                "cacheRead": usage.get("cacheRead"),
                                "cacheWrite": usage.get("cacheWrite"),
                                "total": usage.get("total"),
                            }
                        )
                        if normalized is None:
                            return None, None
                        llm_calls += 1
                tool_calls += self._count_tool_calls(message)
        except Exception:
            return None, None

        return (llm_calls or None), tool_calls

    def _count_tool_calls(self, message: Dict[str, Any]) -> int:
        tool_name_raw = message.get("toolName") or message.get("tool_name")
        has_message_tool = isinstance(tool_name_raw, str) and bool(tool_name_raw.strip())
        content = message.get("content")
        if not isinstance(content, list):
            return 1 if has_message_tool else 0
        content_tool_calls = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            block_type = item.get("type")
            if not isinstance(block_type, str):
                continue
            if block_type.strip().lower() not in self._TOOL_CALL_TYPES:
                continue
            content_tool_calls += 1
        if content_tool_calls > 0:
            return content_tool_calls
        return 1 if has_message_tool else 0
