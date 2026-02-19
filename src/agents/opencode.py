from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import CodingAgent
from ..models import InstallResult, RunResult
from ..utils import format_trace_text, load_json_payloads


class OpenCodeAgent(CodingAgent):
    name = "opencode"
    display_name = "OpenCode"
    binary = "opencode"
    supports_images = True
    supports_videos = True
    _SUPPORTED_MODALITIES = ("text", "audio", "image", "video", "pdf")

    def install(self, *, scope: str = "user", version: Optional[str] = None) -> InstallResult:
        return self._install_with_npm(package="opencode-ai", scope=scope, version=version)

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

        model, custom_model_id, api_key, base_url, model_capabilities, env_error = self._resolve_settings(
            model_override=model_override
        )
        if env_error is not None:
            return self._build_error_run_result(message=env_error, cakit_exit_code=1)

        run_env = self._build_run_env(
            model=model,
            custom_model_id=custom_model_id,
            api_key=api_key,
            base_url=base_url,
            model_capabilities=model_capabilities,
        )

        cmd = [
            "opencode",
            "run",
            "--format",
            "json",
        ]
        if model is not None:
            cmd.extend(["--model", model])
        for media_path in [*images, *videos]:
            cmd.extend(["--file", str(media_path)])
        cmd.append("--")
        cmd.append(prompt)

        result = self._run(cmd, env=run_env, base_env=base_env)
        output = result.output
        payloads = load_json_payloads(self._stdout_only(output))
        response = self._extract_response(payloads)

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls: Optional[int] = None
        tool_calls: Optional[int] = None
        total_cost: Optional[float] = None

        session_id = self._extract_session_id(payloads)
        session_payload = self._export_session_payload(
            session_id=session_id,
            env=run_env,
            base_env=base_env,
        )
        parsed_models_usage, parsed_llm_calls, parsed_tool_calls, parsed_total_cost = self._extract_stats_from_export(
            session_payload
        )
        if parsed_models_usage:
            models_usage = parsed_models_usage
        if parsed_llm_calls is not None:
            llm_calls = parsed_llm_calls
        if parsed_tool_calls is not None:
            tool_calls = parsed_tool_calls
        if parsed_total_cost is not None:
            total_cost = parsed_total_cost

        output_path = self._write_output(self.name, output)
        trajectory_path = self._write_trajectory(self.name, format_trace_text(output, source=str(output_path)))
        return RunResult(
            agent=self.name,
            agent_version=self.get_version(),
            runtime_seconds=result.duration_seconds,
            models_usage=models_usage,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            total_cost=total_cost,
            response=response,
            cakit_exit_code=None,
            command_exit_code=result.exit_code,
            output_path=str(output_path),
            raw_output=output,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
        )

    def get_version(self) -> Optional[str]:
        first = self._version_first_line(["opencode", "--version"])
        if first is None:
            return None
        prefixed = self._second_token_if_prefixed(first, prefix="opencode")
        if prefixed:
            return prefixed
        return first

    def _resolve_settings(
        self, *, model_override: Optional[str]
    ) -> Tuple[
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[list[str]],
        Optional[str],
    ]:
        cakit_model = self._normalize_text(os.environ.get("CAKIT_OPENCODE_MODEL"))
        openai_default_model = self._normalize_text(os.environ.get("OPENAI_DEFAULT_MODEL"))
        raw_model = self._normalize_text(model_override) or cakit_model or openai_default_model
        provider = self._normalize_text(os.environ.get("CAKIT_OPENCODE_PROVIDER"))
        if provider is None and cakit_model is None and model_override is None and openai_default_model is not None:
            provider = "openai"
        api_key = self._resolve_openai_api_key("CAKIT_OPENCODE_OPENAI_API_KEY")
        base_url = self._resolve_openai_base_url("CAKIT_OPENCODE_OPENAI_BASE_URL")
        model_capabilities, capabilities_error = self._parse_model_capabilities()
        if capabilities_error is not None:
            return None, None, None, None, None, capabilities_error

        missing: list[tuple[str, str]] = []
        if base_url is not None and api_key is None:
            missing.append(("CAKIT_OPENCODE_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if (api_key is not None or base_url is not None) and raw_model is None:
            missing.append(("CAKIT_OPENCODE_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, None, None, None, None, self._missing_env_with_fallback_message(missing)

        model = self._normalize_model(raw_model, provider=provider)
        if raw_model is not None and model is None:
            if "/" not in raw_model and ":" not in raw_model and provider is None:
                return (
                    None,
                    None,
                    None,
                    None,
                    None,
                    self._missing_env_message(["CAKIT_OPENCODE_PROVIDER"]),
                )
            return (
                None,
                None,
                None,
                None,
                None,
                "invalid CAKIT_OPENCODE_MODEL: expected provider/model or provider:model",
            )

        if api_key is not None or base_url is not None:
            if raw_model is None:
                return (
                    None,
                    None,
                    None,
                    None,
                    None,
                    self._missing_env_with_fallback_message([("CAKIT_OPENCODE_MODEL", "OPENAI_DEFAULT_MODEL")]),
                )
            if model is None:
                return (
                    None,
                    None,
                    None,
                    None,
                    None,
                    "invalid CAKIT_OPENCODE_MODEL: expected provider/model or provider:model",
                )
            model_id = self._extract_model_id(model)
            if model_id is None:
                return (
                    None,
                    None,
                    None,
                    None,
                    None,
                    self._missing_env_with_fallback_message([("CAKIT_OPENCODE_MODEL", "OPENAI_DEFAULT_MODEL")]),
                )
            return f"cakit-openai/{model_id}", model_id, api_key, base_url, model_capabilities, None

        return model, None, api_key, base_url, model_capabilities, None

    def _build_run_env(
        self,
        *,
        model: Optional[str],
        custom_model_id: Optional[str],
        api_key: Optional[str],
        base_url: Optional[str],
        model_capabilities: Optional[list[str]],
    ) -> Dict[str, str]:
        env: Dict[str, str] = {
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
        if api_key is not None:
            env["OPENAI_API_KEY"] = api_key
            run_root = Path(tempfile.mkdtemp(prefix="cakit-opencode-"))
            env["XDG_DATA_HOME"] = str(run_root / "data")
            env["XDG_CACHE_HOME"] = str(run_root / "cache")
            env["XDG_CONFIG_HOME"] = str(run_root / "config")
            env["XDG_STATE_HOME"] = str(run_root / "state")
            if custom_model_id is not None:
                resolved_base_url = base_url or "https://api.openai.com/v1"
                model_entry: Dict[str, Any] = {
                    "name": custom_model_id,
                    "attachment": True,
                    "reasoning": True,
                    "temperature": True,
                    "tool_call": True,
                    "limit": {
                        "context": 262144,
                        "output": 32768,
                    },
                }
                modalities_input = self._resolved_modalities_input(model_capabilities)
                if modalities_input is not None:
                    model_entry["modalities"] = {
                        "input": modalities_input,
                        "output": ["text"],
                    }
                config_content = {
                    "$schema": "https://opencode.ai/config.json",
                    "model": f"cakit-openai/{custom_model_id}",
                    "provider": {
                        "cakit-openai": {
                            "name": "CAKIT OpenAI Compatible",
                            "npm": "@ai-sdk/openai-compatible",
                            "options": {
                                "apiKey": api_key,
                                "baseURL": resolved_base_url,
                            },
                            "models": {
                                custom_model_id: model_entry
                            },
                        }
                    },
                }
                env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config_content, ensure_ascii=True)
        return env

    def _parse_model_capabilities(self) -> Tuple[Optional[list[str]], Optional[str]]:
        raw_capabilities = self._normalize_text(os.environ.get("CAKIT_OPENCODE_MODEL_CAPABILITIES"))
        if raw_capabilities is None:
            return None, None

        tokens = [token.strip().lower() for token in raw_capabilities.split(",") if token.strip()]
        if not tokens:
            return (
                None,
                "invalid CAKIT_OPENCODE_MODEL_CAPABILITIES: expected comma-separated values from text,audio,image,video,pdf",
            )

        invalid = sorted({token for token in tokens if token not in self._SUPPORTED_MODALITIES})
        if invalid:
            return (
                None,
                "invalid CAKIT_OPENCODE_MODEL_CAPABILITIES: unknown value(s): "
                + ", ".join(invalid)
                + " (allowed: text,audio,image,video,pdf)",
            )

        deduped: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return deduped, None

    @classmethod
    def _resolved_modalities_input(cls, model_capabilities: Optional[list[str]]) -> Optional[list[str]]:
        if model_capabilities is None:
            return None
        declared = set(model_capabilities)
        declared.add("text")
        return [modality for modality in cls._SUPPORTED_MODALITIES if modality in declared]

    def _normalize_model(self, model: Optional[str], *, provider: Optional[str]) -> Optional[str]:
        normalized = self._normalize_text(model)
        if normalized is None:
            return None
        if "/" in normalized:
            provider_id, model_id = normalized.split("/", 1)
        elif ":" in normalized:
            provider_id, model_id = normalized.split(":", 1)
        else:
            normalized_provider = self._normalize_text(provider)
            if normalized_provider is None:
                return None
            provider_id, model_id = normalized_provider, normalized
        provider_id = provider_id.strip()
        model_id = model_id.strip()
        if not provider_id or not model_id:
            return None
        return f"{provider_id}/{model_id}"

    def _extract_model_id(self, model: str) -> Optional[str]:
        cleaned = model.strip()
        if not cleaned:
            return None
        if "/" in cleaned:
            _, model_id = cleaned.split("/", 1)
            model_id = model_id.strip()
            return model_id or None
        if ":" in cleaned:
            _, model_id = cleaned.split(":", 1)
            model_id = model_id.strip()
            return model_id or None
        return cleaned

    def _extract_session_id(self, payloads: list[Dict[str, Any]]) -> Optional[str]:
        ids: set[str] = set()
        for payload in payloads:
            value = payload.get("sessionID")
            if value is None:
                continue
            if not isinstance(value, str):
                return None
            cleaned = value.strip()
            if not cleaned:
                return None
            ids.add(cleaned)
        if len(ids) != 1:
            return None
        return next(iter(ids))

    def _export_session_payload(
        self,
        *,
        session_id: Optional[str],
        env: Dict[str, str],
        base_env: Optional[Dict[str, str]],
    ) -> Optional[Dict[str, Any]]:
        if session_id is None:
            return None
        result = self._run(
            ["opencode", "export", session_id],
            env=env,
            base_env=base_env,
        )
        if result.exit_code != 0:
            return None
        stdout = result.stdout.strip()
        if not stdout:
            return None
        try:
            payload = json.loads(stdout)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _extract_stats_from_export(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, Dict[str, int]], Optional[int], Optional[int], Optional[float]]:
        if not isinstance(payload, dict):
            return {}, None, None, None

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return {}, None, None, None

        models_usage: Dict[str, Dict[str, int]] = {}
        llm_calls = 0
        tool_calls = 0
        total_cost = 0.0

        for message in messages:
            if not isinstance(message, dict):
                return {}, None, None, None
            info = message.get("info")
            if not isinstance(info, dict):
                return {}, None, None, None
            parts = message.get("parts")
            if not isinstance(parts, list):
                return {}, None, None, None

            role = info.get("role")
            if role not in {"user", "assistant"}:
                return {}, None, None, None
            if role != "assistant":
                continue

            provider_id = info.get("providerID")
            model_id = info.get("modelID")
            if not isinstance(provider_id, str) or not provider_id.strip():
                return {}, None, None, None
            if not isinstance(model_id, str) or not model_id.strip():
                return {}, None, None, None
            model_name = f"{provider_id.strip()}/{model_id.strip()}"

            cost = info.get("cost")
            if not isinstance(cost, (int, float)) or isinstance(cost, bool):
                return {}, None, None, None
            total_cost += float(cost)

            tokens = info.get("tokens")
            if not isinstance(tokens, dict):
                return {}, None, None, None
            cache = tokens.get("cache")
            if not isinstance(cache, dict):
                return {}, None, None, None

            input_tokens = self._as_int(tokens.get("input"))
            output_tokens = self._as_int(tokens.get("output"))
            reasoning_tokens = self._as_int(tokens.get("reasoning"))
            cache_read_tokens = self._as_int(cache.get("read"))
            cache_write_tokens = self._as_int(cache.get("write"))
            total_tokens = self._as_int(tokens.get("total"))
            if None in {
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cache_read_tokens,
                cache_write_tokens,
            }:
                return {}, None, None, None

            prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
            completion_tokens = output_tokens + reasoning_tokens
            resolved_total_tokens = (
                total_tokens
                if total_tokens is not None
                else prompt_tokens + completion_tokens
            )

            usage = models_usage.setdefault(
                model_name,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            usage["prompt_tokens"] += prompt_tokens
            usage["completion_tokens"] += completion_tokens
            usage["total_tokens"] += resolved_total_tokens

            llm_calls += 1

            for part in parts:
                if not isinstance(part, dict):
                    return {}, None, None, None
                part_type = part.get("type")
                if not isinstance(part_type, str):
                    return {}, None, None, None
                if part_type == "tool":
                    tool_calls += 1

        if llm_calls < 1:
            return {}, None, None, None
        if not models_usage:
            return {}, None, None, None
        return models_usage, llm_calls, tool_calls, total_cost

    def _extract_response(self, payloads: list[Dict[str, Any]]) -> Optional[str]:
        response: Optional[str] = None
        for payload in payloads:
            if payload.get("type") != "text":
                continue
            part = payload.get("part")
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if not isinstance(text, str):
                continue
            cleaned = text.strip()
            if cleaned:
                response = cleaned
        return response
