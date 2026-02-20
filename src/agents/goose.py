from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .base import CodingAgent, InstallStrategy, RunCommandTemplate, VersionCommandTemplate
from ..models import RunResult
from .base import req_int, req_str, select_values


class GooseAgent(CodingAgent):
    name = "goose"
    display_name = "Goose CLI"
    binary = "goose"
    supports_images = True
    supports_videos = True
    install_strategy = InstallStrategy(
        kind="shell",
        shell_command="curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash",
        shell_versioned_command=(
            "curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh "
            "| GOOSE_VERSION={version_quoted} CONFIGURE=false bash"
        ),
        version_normalizer="prefix_v",
    )
    run_template = RunCommandTemplate(
        base_args=("run", "--with-builtin", "developer", "--output-format", "stream-json"),
        prompt_mode="flag",
        prompt_flag="-t",
        model_flag="--model",
        media_injection="natural",
        media_tool_name="developer__image_processor and developer__video_processor",
    )
    version_template = VersionCommandTemplate(
        args=("goose", "--version"),
        parse_mode="regex_first_line",
        regex=r"^(?:goose\s+)?([A-Za-z0-9._-]+)$",
    )
    _SESSION_ID_RE = re.compile(r"session id:\s*([^\s]+)", re.IGNORECASE)

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
        videos = videos or []
        env, env_error = self._build_run_env(
            model_override=model_override,
        )
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        session_name = f"cakit-goose-{uuid.uuid4().hex}"
        provider = env.get("GOOSE_PROVIDER")
        model = env.get("GOOSE_MODEL")
        if provider:
            extra_args = ["--name", session_name, "--provider", provider]
        else:
            extra_args = ["--name", session_name]
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=model,
            images=images,
            videos=videos,
            extra_args=extra_args,
        )

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        match = self._SESSION_ID_RE.search(self._stdout_only(output))
        session_id = match.group(1).strip() if match else None
        if session_id == "":
            session_id = None
        export_cmd = ["goose", "session", "export", "--format", "json"]
        if session_id:
            export_cmd.extend(["--session-id", session_id])
        else:
            export_cmd.extend(["--name", session_name])
        session_payload = self._run_json_dict_command(export_cmd, env=env, base_env=base_env, stdout_only=True)
        models_usage, llm_calls, tool_calls = self._extract_session_stats(
            session_payload=session_payload,
        )
        response: Optional[str] = None
        if isinstance(session_payload, dict):
            response = self._last_selected_text(
                session_payload,
                '$.conversation[?(@.role == "assistant")].content[?(@.type == "text")].text',
            )
        if response is None:
            response = self._last_stdout_line(output)
        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )

    def _build_run_env(
        self,
        *,
        model_override: Optional[str],
    ) -> Tuple[Dict[str, str], Optional[str]]:
        env_source = os.environ
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
            parsed = urlparse(openai_base_url)
            if not parsed.scheme or not parsed.netloc:
                return {}, f"invalid CAKIT_GOOSE_OPENAI_BASE_URL: {openai_base_url}"
            derived_host = f"{parsed.scheme}://{parsed.netloc}"
            derived_base_path = (parsed.path or "").strip("/")
            if not derived_base_path or derived_base_path == "v1":
                derived_base_path = "v1/chat/completions"
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
        else:
            missing = []
        if missing:
            return {}, self._missing_env_with_fallback_message(missing)
        env: Dict[str, str] = {"GOOSE_MODE": "auto"}
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

    def _extract_session_stats(
        self,
        *,
        session_payload: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        payload = session_payload if isinstance(session_payload, dict) else None
        if payload is None:
            return {}, None, None

        model_name = req_str(payload, "$.model_config.model_name")
        prompt_tokens = req_int(payload, "$.accumulated_input_tokens")
        completion_tokens = req_int(payload, "$.accumulated_output_tokens")
        total_tokens = req_int(payload, "$.accumulated_total_tokens")
        assistant_message_count = self._count_selected(payload, '$.conversation[?(@.role == "assistant")]')
        models_usage: Dict[str, Dict[str, int]] = {}
        if (
            model_name is not None
            and prompt_tokens is not None
            and completion_tokens is not None
            and total_tokens is not None
        ):
            models_usage[model_name] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

        tool_calls = self._count_selected_total(
            payload,
            (
                '$.conversation[?(@.role == "assistant")].content[?(@.type == "toolRequest")]',
                '$.conversation[?(@.role == "assistant")].content[?(@.type == "frontendToolRequest")]',
            ),
        )
        return (
            models_usage,
            assistant_message_count,
            tool_calls,
        )
