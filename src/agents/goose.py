from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class GooseAgent(CodingAgent):
    name = "goose"
    display_name = "Goose CLI"
    binary = "goose"
    supports_images = True
    supports_videos = True
    _SESSION_ID_RE = re.compile(r"session id:\s*([^\s]+)", re.IGNORECASE)

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        del scope
        script_url = "https://github.com/block/goose/releases/download/stable/download_cli.sh"
        version_env = ""
        if version and version.strip():
            normalized_version = version.strip()
            if not normalized_version.startswith("v"):
                normalized_version = f"v{normalized_version}"
            version_env = f"GOOSE_VERSION={shlex.quote(normalized_version)} "
        cmd = f"curl -fsSL {script_url} | {version_env}CONFIGURE=false bash"
        result = self._run(["bash", "-lc", cmd])
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
        del reasoning_effort
        images = images or []
        videos = videos or []
        run_prompt = prompt
        if images or videos:
            run_prompt, _, _ = self._build_natural_media_prompt(
                prompt,
                images=images,
                videos=videos,
                tool_name="developer__image_processor and developer__video_processor",
            )
        env, env_error = self._build_run_env(
            model_override=model_override,
            source_env=base_env,
        )
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        session_name = f"cakit-goose-{uuid.uuid4().hex}"
        cmd = [
            "goose",
            "run",
            "-t",
            run_prompt,
            "--name",
            session_name,
            "--with-builtin",
            "developer",
            "--output-format",
            "stream-json",
        ]
        provider = env.get("GOOSE_PROVIDER")
        model = env.get("GOOSE_MODEL")
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(self._stdout_only(output))
        session_id = self._extract_session_id(output)
        session_payload = self._export_session_payload(
            session_id=session_id,
            session_name=session_name,
            env=env,
            base_env=base_env,
        )

        model_name = self._extract_model_name_from_stream(payloads) or self._extract_model_name_from_session(session_payload)
        usage = self._extract_usage_from_session(session_payload)
        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=self._ensure_models_usage({}, usage, model_name) if usage is not None and model_name else {},
            tool_calls=self._extract_tool_calls_from_session(session_payload),
            llm_calls=self._extract_llm_calls_from_session(session_payload),
            response=self._extract_response(payloads=payloads, session_payload=session_payload, output=output),
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        first = self._version_first_line(["goose", "--version"])
        if first is None:
            return None
        prefixed = self._second_token_if_prefixed(first, prefix="goose")
        if prefixed:
            return prefixed
        return first

    def _build_run_env(
        self,
        *,
        model_override: Optional[str],
        source_env: Optional[Dict[str, str]],
    ) -> Tuple[Dict[str, str], Optional[str]]:
        env_source = source_env if source_env is not None else os.environ

        provider = self._normalize_text(env_source.get("CAKIT_GOOSE_PROVIDER")) or self._normalize_text(
            env_source.get("GOOSE_PROVIDER")
        )
        model = (
            self._normalize_text(model_override)
            or self._normalize_text(env_source.get("CAKIT_GOOSE_MODEL"))
            or self._normalize_text(env_source.get("GOOSE_MODEL"))
            or self._normalize_text(env_source.get("OPENAI_DEFAULT_MODEL"))
        )

        openai_api_key = self._resolve_openai_api_key("CAKIT_GOOSE_OPENAI_API_KEY", source_env=env_source)
        openai_host = self._normalize_text(env_source.get("OPENAI_HOST"))
        openai_base_path = self._normalize_text(env_source.get("CAKIT_GOOSE_OPENAI_BASE_PATH")) or self._normalize_text(
            env_source.get("OPENAI_BASE_PATH")
        )
        openai_base_url = self._resolve_openai_base_url("CAKIT_GOOSE_OPENAI_BASE_URL", source_env=env_source)
        if openai_base_url:
            endpoint = self._derive_openai_endpoint(openai_base_url)
            if endpoint is None:
                return {}, f"invalid CAKIT_GOOSE_OPENAI_BASE_URL: {openai_base_url}"
            derived_host, derived_base_path = endpoint
            if not openai_host:
                openai_host = derived_host
            if not openai_base_path:
                openai_base_path = derived_base_path

        cakit_keys = (
            "CAKIT_GOOSE_PROVIDER",
            "CAKIT_GOOSE_MODEL",
            "CAKIT_GOOSE_OPENAI_API_KEY",
            "CAKIT_GOOSE_OPENAI_BASE_URL",
            "CAKIT_GOOSE_OPENAI_BASE_PATH",
        )
        cakit_configured = any(self._normalize_text(env_source.get(key)) for key in cakit_keys)
        generic_openai_configured = any(
            self._normalize_text(env_source.get(key))
            for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_DEFAULT_MODEL")
        )
        if provider is None and (cakit_configured or generic_openai_configured):
            provider = "openai"
        if cakit_configured or generic_openai_configured:
            missing: List[tuple[str, str]] = []
            if provider is None:
                missing.append(("CAKIT_GOOSE_PROVIDER", "GOOSE_PROVIDER"))
            if model is None:
                missing.append(("CAKIT_GOOSE_MODEL", "OPENAI_DEFAULT_MODEL"))
            if provider == "openai" and openai_api_key is None:
                missing.append(("CAKIT_GOOSE_OPENAI_API_KEY", "OPENAI_API_KEY"))
            if missing:
                return {}, self._missing_env_with_fallback_message(missing)

        env: Dict[str, str] = {
            "GOOSE_MODE": "auto",
        }
        if provider:
            env["GOOSE_PROVIDER"] = provider
        if model:
            env["GOOSE_MODEL"] = model
        if openai_api_key:
            env["OPENAI_API_KEY"] = openai_api_key
        if openai_host:
            env["OPENAI_HOST"] = openai_host
        if openai_base_path:
            env["OPENAI_BASE_PATH"] = openai_base_path
        return env, None

    @staticmethod
    def _derive_openai_endpoint(base_url: str) -> Optional[Tuple[str, str]]:
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        host = f"{parsed.scheme}://{parsed.netloc}"
        path = (parsed.path or "").strip("/")
        if not path or path == "v1":
            path = "v1/chat/completions"
        return host, path

    def _export_session_payload(
        self,
        *,
        session_id: Optional[str],
        session_name: str,
        env: Dict[str, str],
        base_env: Optional[Dict[str, str]],
    ) -> Optional[Dict[str, Any]]:
        cmd = ["goose", "session", "export", "--format", "json"]
        if session_id:
            cmd.extend(["--session-id", session_id])
        else:
            cmd.extend(["--name", session_name])
        result = self._run(cmd, env=env, base_env=base_env)
        if result.exit_code != 0:
            return None
        stdout = self._stdout_only(result.output).strip()
        if not stdout:
            return None
        try:
            payload = json.loads(stdout)
        except Exception:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _extract_session_id(self, output: str) -> Optional[str]:
        stdout = self._stdout_only(output)
        match = self._SESSION_ID_RE.search(stdout)
        if not match:
            return None
        session_id = match.group(1).strip()
        if not session_id:
            return None
        return session_id

    def _extract_model_name_from_stream(self, payloads: List[Dict[str, Any]]) -> Optional[str]:
        model_name: Optional[str] = None
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "model_change":
                continue
            model = payload.get("model")
            if not isinstance(model, str):
                continue
            cleaned = model.strip()
            if cleaned:
                model_name = cleaned
        return model_name

    def _extract_model_name_from_session(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        model_config = payload.get("model_config")
        if not isinstance(model_config, dict):
            return None
        model_name = model_config.get("model_name")
        if not isinstance(model_name, str):
            return None
        cleaned = model_name.strip()
        if not cleaned:
            return None
        return cleaned

    def _extract_usage_from_session(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        if not isinstance(payload, dict):
            return None
        prompt_tokens = self._as_int(payload.get("accumulated_input_tokens"))
        completion_tokens = self._as_int(payload.get("accumulated_output_tokens"))
        if prompt_tokens is None:
            prompt_tokens = self._as_int(payload.get("input_tokens"))
        if completion_tokens is None:
            completion_tokens = self._as_int(payload.get("output_tokens"))
        if prompt_tokens is None or completion_tokens is None:
            return None
        total_tokens = self._as_int(payload.get("accumulated_total_tokens"))
        if total_tokens is None:
            total_tokens = self._as_int(payload.get("total_tokens"))
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_session_messages(self, payload: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(payload, dict):
            return None
        conversation = payload.get("conversation")
        raw_messages: Any
        if isinstance(conversation, list):
            raw_messages = conversation
        elif isinstance(conversation, dict):
            raw_messages = conversation.get("messages")
        else:
            return None
        if not isinstance(raw_messages, list):
            return None
        normalized: List[Dict[str, Any]] = []
        for message in raw_messages:
            if not isinstance(message, dict):
                return None
            normalized.append(message)
        return normalized

    def _extract_llm_calls_from_session(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        messages = self._extract_session_messages(payload)
        if messages is None:
            return None
        llm_calls = 0
        for message in messages:
            if message.get("role") == "assistant":
                llm_calls += 1
        return llm_calls

    def _extract_tool_calls_from_session(self, payload: Optional[Dict[str, Any]]) -> Optional[int]:
        messages = self._extract_session_messages(payload)
        if messages is None:
            return None
        tool_calls = 0
        for message in messages:
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                return None
            for item in content:
                if not isinstance(item, dict):
                    return None
                item_type = item.get("type")
                if not isinstance(item_type, str):
                    return None
                if item_type in {"toolRequest", "frontendToolRequest"}:
                    tool_calls += 1
        return tool_calls

    def _extract_response(
        self,
        *,
        payloads: List[Dict[str, Any]],
        session_payload: Optional[Dict[str, Any]],
        output: str,
    ) -> Optional[str]:
        response = self._extract_response_from_session(session_payload)
        if response:
            return response
        response = self._extract_response_from_stream(payloads)
        if response:
            return response
        stdout = self._stdout_only(output)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return None

    def _extract_response_from_session(self, payload: Optional[Dict[str, Any]]) -> Optional[str]:
        messages = self._extract_session_messages(payload)
        if messages is None:
            return None
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            text = self._extract_text_from_message(message)
            if text:
                return text
        return None

    def _extract_response_from_stream(self, payloads: List[Dict[str, Any]]) -> Optional[str]:
        chunks_by_message_id: Dict[str, List[str]] = {}
        latest_message_id: Optional[str] = None
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "message":
                continue
            message = payload.get("message")
            if not isinstance(message, dict):
                return None
            if message.get("role") != "assistant":
                continue
            message_id = message.get("id")
            if not isinstance(message_id, str) or not message_id.strip():
                text = self._extract_text_from_message(message)
                if text:
                    return text
                continue
            text = self._extract_text_from_message(message)
            if not text:
                continue
            message_id = message_id.strip()
            if message_id not in chunks_by_message_id:
                chunks_by_message_id[message_id] = []
            chunks_by_message_id[message_id].append(text)
            latest_message_id = message_id
        if latest_message_id:
            merged = "".join(chunks_by_message_id.get(latest_message_id, [])).strip()
            if merged:
                return merged
        return None

    def _extract_text_from_message(self, message: Dict[str, Any]) -> Optional[str]:
        content = message.get("content")
        if not isinstance(content, list):
            return None
        chunks: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                return None
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            cleaned = text.strip()
            if cleaned:
                chunks.append(cleaned)
        if chunks:
            return "\n".join(chunks)
        return None
