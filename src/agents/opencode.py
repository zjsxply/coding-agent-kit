from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import (
    CodingAgent,
    InstallStrategy,
    RunCommandTemplate,
    VersionCommandTemplate,
    extract_opencode_session_export_stats,
    select_values,
)
from ..models import RunResult


class OpenCodeAgent(CodingAgent):
    name = "opencode"
    display_name = "OpenCode"
    binary = "opencode"
    supports_images = True
    supports_videos = True
    install_strategy = InstallStrategy(kind="npm", package="opencode-ai")
    run_template = RunCommandTemplate(
        base_args=("run", "--format", "json"),
        prompt_mode="arg",
        prompt_flag=None,
        model_flag="--model",
        media_injection="none",
    )
    version_template = VersionCommandTemplate(
        args=("opencode", "--version"),
        parse_mode="regex_first_line",
        regex=r"^(?:opencode\s+)?([A-Za-z0-9._-]+)$",
    )
    _SUPPORTED_MODALITIES = ("text", "audio", "image", "video", "pdf")

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

        template = self.run_template
        extra_args: list[str] = []
        for media_path in [*images, *videos]:
            extra_args.extend(["--file", str(media_path)])
        extra_args.append("--")
        cmd, _ = self._build_templated_command(
            template=template,
            prompt=prompt,
            model=model,
            extra_args=extra_args,
        )

        result = self._run(cmd, env=run_env, base_env=base_env)
        output = result.output
        payloads = self._load_output_json_payloads(output)
        response = self._last_selected_text(
            payloads,
            '$[?(@.type == "text")].part[?(@.type == "text")].text',
        )

        session_ids = select_values(payloads, "$[*].sessionID")
        if session_ids is None:
            session_id = None
        else:
            unique_ids = {
                session_id.strip()
                for session_id in session_ids
                if isinstance(session_id, str) and session_id.strip()
            }
            session_id = next(iter(unique_ids)) if len(unique_ids) == 1 else None
        session_payload = (
            self._run_json_dict_command(
                ["opencode", "export", session_id],
                env=run_env,
                base_env=base_env,
            )
            if session_id is not None
            else None
        )
        artifacts = self._build_stats_artifacts(
            raw_output=output,
            session_payload=session_payload,
        )
        stats = self._merge_stats_snapshots(
            snapshots=[extract_opencode_session_export_stats(artifacts)]
        )

        return self.finalize_run(
            command_result=result,
            response=response,
            models_usage=stats.models_usage,
            llm_calls=stats.llm_calls,
            tool_calls=stats.tool_calls,
            total_cost=stats.total_cost,
        )

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
        if raw_model is None or model is not None:
            model_error = None
        elif "/" not in raw_model and ":" not in raw_model and provider is None:
            model_error = self._missing_env_message(["CAKIT_OPENCODE_PROVIDER"])
        else:
            model_error = "invalid CAKIT_OPENCODE_MODEL: expected provider/model or provider:model"
        if model_error is not None:
            return None, None, None, None, None, model_error

        if api_key is not None or base_url is not None:
            return self._resolve_openai_settings_with_key(
                model=model,
                raw_model=raw_model,
                api_key=api_key,
                base_url=base_url,
                model_capabilities=model_capabilities,
            )

        return model, None, api_key, base_url, model_capabilities, None

    def _resolve_openai_settings_with_key(
        self,
        *,
        model: Optional[str],
        raw_model: Optional[str],
        api_key: Optional[str],
        base_url: Optional[str],
        model_capabilities: Optional[list[str]],
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[list[str]], Optional[str]]:
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
            run_root = self._make_temp_dir(prefix="cakit-opencode-")
            env["XDG_DATA_HOME"] = str(run_root / "data")
            env["XDG_CACHE_HOME"] = str(run_root / "cache")
            env["XDG_CONFIG_HOME"] = str(run_root / "config")
            env["XDG_STATE_HOME"] = str(run_root / "state")
            if custom_model_id is not None:
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
                if model_capabilities is None:
                    modalities_input = None
                else:
                    declared_modalities = set(model_capabilities)
                    declared_modalities.add("text")
                    modalities_input = [
                        modality
                        for modality in self._SUPPORTED_MODALITIES
                        if modality in declared_modalities
                    ]
                if modalities_input is not None:
                    model_entry["modalities"] = {
                        "input": modalities_input,
                        "output": ["text"],
                    }
                provider_options: Dict[str, Any] = {
                    "apiKey": api_key,
                }
                if base_url is not None:
                    provider_options["baseURL"] = base_url
                config_content = {
                    "$schema": "https://opencode.ai/config.json",
                    "model": f"cakit-openai/{custom_model_id}",
                    "provider": {
                        "cakit-openai": {
                            "name": "CAKIT OpenAI Compatible",
                            "npm": "@ai-sdk/openai-compatible",
                            "options": provider_options,
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
