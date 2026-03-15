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
)
from ..models import RunResult
from ..agent_runtime import env as runtime_env
from ..agent_runtime import parsing as runtime_parsing
from ..stats_extract import StatsArtifacts, extract_opencode_session_export_stats, merge_stats_snapshots, select_values


class OpenCodeAgent(CodingAgent):
    name = "opencode"
    display_name = "OpenCode"
    binary = "opencode"
    supports_images = True
    supports_videos = False
    required_runtimes = ("curl", "tar", "which")
    install_strategy = [
        InstallStrategy(
            kind="shell",
            shell_command="curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path",
            shell_versioned_command=(
                "curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path --version {version_quoted}"
            ),
        ),
        InstallStrategy(kind="npm", package="opencode-ai"),
    ]
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
        env_mode="runtime_assets",
    )
    _SUPPORTED_MODALITIES = ("text", "audio", "image", "video", "pdf")

    def _opencode_bin_dir(self) -> Path:
        return Path.home() / ".opencode" / "bin"

    def _runtime_asset_env(self, *, create_if_missing: bool = True) -> Optional[Dict[str, str]]:
        bin_dir = self._opencode_bin_dir()
        if create_if_missing:
            bin_dir.mkdir(parents=True, exist_ok=True)
        elif not bin_dir.is_dir():
            return None
        return {"PATH": str(bin_dir)}

    def is_installed(self) -> bool:
        runtime_assets_env = self._runtime_asset_env(create_if_missing=False)
        if runtime_assets_env is None:
            return False
        result = self._run(["opencode", "--version"], env=runtime_assets_env)
        return result.exit_code == 0 and bool(result.output.strip())

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

        runtime_assets_env = self._runtime_asset_env(create_if_missing=False) or {}
        run_env = {
            **runtime_assets_env,
            **self._build_run_env(
            model=model,
            custom_model_id=custom_model_id,
            api_key=api_key,
            base_url=base_url,
            model_capabilities=model_capabilities,
            ),
        }

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
        payloads = runtime_parsing.load_output_json_payloads(output)
        response = runtime_parsing.last_nonempty_text(
            select_values(payloads, '$[?(@.type == "text")].part.text')
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
            runtime_parsing.run_json_dict_command(
                args=["opencode", "export", session_id],
                run=self._run,
                env=run_env,
                base_env=base_env,
            )
            if session_id is not None
            else None
        )
        artifacts = StatsArtifacts(
            raw_output=output,
            session_payload=session_payload,
        )
        stats = merge_stats_snapshots(
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
        cakit_model = runtime_parsing.normalize_text(os.environ.get("CAKIT_OPENCODE_MODEL"))
        openai_default_model = runtime_parsing.normalize_text(os.environ.get("OPENAI_DEFAULT_MODEL"))
        raw_model = runtime_parsing.normalize_text(model_override) or cakit_model or openai_default_model
        provider = runtime_parsing.normalize_text(os.environ.get("CAKIT_OPENCODE_PROVIDER"))
        if provider is None and cakit_model is None and model_override is None and openai_default_model is not None:
            provider = "openai"
        api_key = runtime_env.resolve_openai_api_key("CAKIT_OPENCODE_OPENAI_API_KEY")
        base_url = runtime_env.resolve_openai_base_url("CAKIT_OPENCODE_OPENAI_BASE_URL")
        model_capabilities, capabilities_error = self._parse_model_capabilities()
        if capabilities_error is not None:
            return None, None, None, None, None, capabilities_error

        missing: list[tuple[str, str]] = []
        if base_url is not None and api_key is None:
            missing.append(("CAKIT_OPENCODE_OPENAI_API_KEY", "OPENAI_API_KEY"))
        if (api_key is not None or base_url is not None) and raw_model is None:
            missing.append(("CAKIT_OPENCODE_MODEL", "OPENAI_DEFAULT_MODEL"))
        if missing:
            return None, None, None, None, None, runtime_env.missing_env_with_fallback_message(missing)

        model = runtime_env.normalize_model(raw_model, provider=provider)
        if raw_model is None or model is not None:
            model_error = None
        elif "/" not in raw_model and ":" not in raw_model and provider is None:
            model_error = runtime_env.missing_env_message(["CAKIT_OPENCODE_PROVIDER"])
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
        model_id = runtime_env.extract_model_id(model)
        if model_id is None:
            return (
                None,
                None,
                None,
                None,
                None,
                runtime_env.missing_env_with_fallback_message([("CAKIT_OPENCODE_MODEL", "OPENAI_DEFAULT_MODEL")]),
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
        raw_capabilities = runtime_parsing.normalize_text(os.environ.get("CAKIT_OPENCODE_MODEL_CAPABILITIES"))
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
