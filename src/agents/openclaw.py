from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from .base import CodingAgent, InstallStrategy, RunCommandTemplate
from ..models import RunResult
from ..stats_extract import last_value, select_values, sum_usage_entries


class OpenClawAgent(CodingAgent):
    name = "openclaw"
    display_name = "OpenClaw"
    binary = "openclaw"
    supports_images = False
    supports_videos = False
    install_strategy = InstallStrategy(kind="npm", package="openclaw")
    run_template = RunCommandTemplate(
        base_args=("agent", "--local", "--agent", "main", "--json"),
        prompt_mode="flag",
        prompt_flag="--message",
        model_flag=None,
        media_injection="none",
    )

    _SAFE_PROVIDER_ID_RE = re.compile(r"[^a-z0-9._-]+")
    def configure(self) -> Optional[str]:
        settings, err = self._resolve_runtime_settings(model_override=None)
        if err is not None:
            return None

        cmd = self._build_onboard_command(settings)
        result = self._run(cmd)
        if result.exit_code != 0:
            return None
        config_override = os.environ.get("OPENCLAW_CONFIG_PATH")
        config_path = (
            Path(config_override).expanduser()
            if config_override
            else self._openclaw_home() / "openclaw.json"
        )
        if not config_path.exists():
            return None
        limit_error = self._patch_custom_provider_limits(config_path)
        if limit_error is not None:
            self._raise_config_error(limit_error)
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
        settings, env_error = self._resolve_runtime_settings(model_override=model_override)
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_home = self._make_temp_dir(prefix="cakit-openclaw-home-")
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
        nested_config = run_home / ".openclaw" / "openclaw.json"
        direct_config = run_home / "openclaw.json"
        runtime_config_path = (
            nested_config
            if nested_config.exists()
            else direct_config
            if direct_config.exists()
            else nested_config
        )
        limit_error = self._patch_custom_provider_limits(runtime_config_path)
        if limit_error is not None:
            return self._build_error_run_result(message=limit_error, cakit_exit_code=1)
        extra_args = ["--session-id", session_id]
        if reasoning_effort:
            extra_args.extend(["--thinking", reasoning_effort])
        template = self.run_template
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            extra_args=extra_args,
        )

        result = self._run(cmd, env=env, base_env=base_env)
        output = result.output
        payload = runtime_parsing.parse_output_json_object(output)
        payload_session_id = runtime_parsing.normalize_text(last_value(payload, "$.meta.agentMeta.sessionId"))

        resolved_session_id = payload_session_id or session_id
        session_path = self._resolve_session_path(resolved_session_id, agent_id="main", env_source=env)
        records: Optional[list[Dict[str, Any]]] = None
        if session_path.exists():
            raw_records = self._read_text_lossy(session_path) or ""
            if raw_records:
                loaded_records = runtime_parsing.load_output_json_payloads(raw_records, stdout_only=False)
                if loaded_records:
                    records = loaded_records
        models_usage, llm_calls, tool_calls = self._extract_run_stats(
            payload=payload,
            records=records,
        )
        response = (
            runtime_parsing.last_nonempty_text(select_values(payload, "$.payloads[*].text"))
            if isinstance(payload, dict)
            else None
        )
        if response is None:
            response = runtime_parsing.last_stdout_line(output)

        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=models_usage,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
        )

    def _resolve_runtime_settings(
        self, *, model_override: Optional[str]
    ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        api_key = runtime_env.resolve_openai_api_key("CAKIT_OPENCLAW_API_KEY")
        base_url = runtime_env.resolve_openai_base_url("CAKIT_OPENCLAW_BASE_URL")
        model_ref = runtime_env.resolve_openai_model("CAKIT_OPENCLAW_MODEL", model_override=model_override)

        provider_id = self._normalize_provider_id(os.environ.get("CAKIT_OPENCLAW_PROVIDER_ID"))
        normalized_model_ref = runtime_parsing.normalize_text(model_ref)
        if normalized_model_ref is None:
            model_id, provider_from_model = None, None
        else:
            model_id = runtime_env.extract_model_id(normalized_model_ref, colon_as_provider=False)
            if not model_id:
                model_id, provider_from_model = None, None
            elif "/" not in normalized_model_ref:
                provider_from_model = None
            else:
                provider_from_model = self._normalize_provider_id(normalized_model_ref.split("/", 1)[0])
        if provider_id is None:
            provider_id = provider_from_model

        missing: list[tuple[str, str]] = []
        if api_key is None:
            missing.append(("CAKIT_OPENCLAW_API_KEY", "OPENAI_API_KEY"))
        if base_url is None:
            missing.append(("CAKIT_OPENCLAW_BASE_URL", "OPENAI_BASE_URL"))
        if model_id is None:
            missing.append(("CAKIT_OPENCLAW_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, runtime_env.missing_env_with_fallback_message(missing)

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
        normalized = runtime_parsing.normalize_text(value)
        if normalized is None:
            return None
        lowered = normalized.lower()
        cleaned = self._SAFE_PROVIDER_ID_RE.sub("-", lowered).strip("-")
        if not cleaned:
            return None
        return cleaned

    def _openclaw_home(self) -> Path:
        root = os.environ.get("OPENCLAW_HOME")
        if root:
            return Path(root).expanduser()
        return Path.home() / ".openclaw"

    def _patch_custom_provider_limits(self, config_path: Path) -> Optional[str]:
        min_context_window, context_error = self._resolve_limit_env("CAKIT_OPENCLAW_CONTEXT_WINDOW")
        if context_error is not None:
            return context_error
        min_max_tokens, max_tokens_error = self._resolve_limit_env("CAKIT_OPENCLAW_MAX_TOKENS")
        if max_tokens_error is not None:
            return max_tokens_error
        if min_context_window is None and min_max_tokens is None:
            return None
        text = self._read_text(config_path)
        if not text:
            return f"openclaw config not found or empty for custom limit overrides: {config_path}"
        payload = runtime_parsing.parse_json_dict(text)
        if payload is None:
            return f"failed to parse openclaw config for custom limit overrides: {config_path}"
        models = payload.get("models")
        if not isinstance(models, dict):
            return "openclaw config is missing models.providers for custom limit overrides"
        providers = models.get("providers")
        if not isinstance(providers, dict):
            return "openclaw config is missing providers for custom limit overrides"
        changed = False
        for provider in providers.values():
            if not isinstance(provider, dict):
                continue
            models_value = provider.get("models")
            if not isinstance(models_value, list):
                continue
            for model in models_value:
                if not isinstance(model, dict):
                    continue
                context_window = runtime_parsing.as_int(model.get("contextWindow"))
                max_tokens = runtime_parsing.as_int(model.get("maxTokens"))
                if min_context_window is not None and (context_window is None or context_window < min_context_window):
                    model["contextWindow"] = min_context_window
                    changed = True
                if min_max_tokens is not None and (max_tokens is None or max_tokens < min_max_tokens):
                    model["maxTokens"] = min_max_tokens
                    changed = True
        if changed:
            self._write_text(config_path, json.dumps(payload, ensure_ascii=True, indent=2))
        return None

    def _resolve_limit_env(self, env_key: str) -> Tuple[Optional[int], Optional[str]]:
        raw = runtime_parsing.normalize_text(os.environ.get(env_key))
        if raw is None:
            return None, None
        try:
            value = int(raw)
        except ValueError:
            return None, f"invalid {env_key}: expected a positive integer"
        if value <= 0:
            return None, f"invalid {env_key}: expected a positive integer"
        return value, None

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

    def _usage_from_total_and_output(self, usage_raw: Any, *, total_path: str) -> Optional[Dict[str, int]]:
        completion_tokens = runtime_parsing.as_int(last_value(usage_raw, "$.output"))
        total_tokens = runtime_parsing.as_int(last_value(usage_raw, total_path))
        if completion_tokens is None or completion_tokens < 0:
            return None
        if total_tokens is None or total_tokens < completion_tokens:
            return None
        return {
            "prompt_tokens": total_tokens - completion_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_run_stats(
        self,
        *,
        payload: Optional[Dict[str, Any]],
        records: Optional[list[Dict[str, Any]]],
    ) -> tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int]]:
        payload_usage = self._usage_from_total_and_output(
            last_value(payload, "$.meta.agentMeta.usage"),
            total_path="$.total",
        )
        payload_provider = runtime_parsing.normalize_text(last_value(payload, "$.meta.agentMeta.provider"))
        payload_model = runtime_parsing.normalize_text(last_value(payload, "$.meta.agentMeta.model"))
        payload_model_name = f"{payload_provider}/{payload_model}" if payload_provider and payload_model else None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        tool_calls: Optional[int] = None
        if records:
            assistant_messages = [
                item
                for item in (select_values(records, '$[?(@.message.role == "assistant")].message') or [])
                if isinstance(item, dict)
            ]
            llm_calls = len(assistant_messages) if assistant_messages else None
            tool_calls = 0
            for message in assistant_messages:
                block_call_values = select_values(message, '$.content[?(@.type == "toolCall")]')
                block_calls = len(block_call_values) if block_call_values is not None else 0
                if block_calls > 0:
                    tool_calls += block_calls
                elif runtime_parsing.normalize_text(last_value(message, "$.toolName")) is not None:
                    tool_calls += 1

            assistant_usages = [
                parsed
                for parsed in (
                    self._usage_from_total_and_output(entry, total_path="$.totalTokens")
                    for entry in (
                        select_values(
                            records,
                            '$[?(@.message.role == "assistant")].message.usage',
                        )
                        or []
                    )
                )
                if parsed is not None
            ]
            if assistant_usages:
                transcript_usage = sum_usage_entries(assistant_usages)
                provider = runtime_parsing.normalize_text(last_value(records, '$[?(@.message.role == "assistant")].message.provider'))
                model = runtime_parsing.normalize_text(last_value(records, '$[?(@.message.role == "assistant")].message.model'))
                if transcript_usage is not None and provider and model:
                    models_usage[f"{provider}/{model}"] = transcript_usage

        if not models_usage and payload_usage is not None and payload_model_name is not None:
            models_usage[payload_model_name] = payload_usage
        return models_usage, llm_calls, tool_calls
